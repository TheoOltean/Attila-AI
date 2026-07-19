"""aai_hunter.py -- persistent PARALLEL field hunter for Attila engine memory.

Replaces the one-value-at-a-time throwaway narrowers (_multinarrow/_track):
ONE long-running process that watches the mod's JSON feeds (the oracle) and
hunts EVERY feasible field simultaneously by change-detection while the game
is played normally. Safe to leave running across game restarts and save-loads
(epoch handling). Run under WINDOWS python.exe (Win32 RPM via aai_mem):

    cd mem && python.exe -u aai_hunter.py

Outputs (all in mem/):
  _hunt_status.txt   live scoreboard, rewritten every cycle   <- WATCH THIS
  _hunt.log          append-only event log (rotates at 20 MB)
  _pins/<name>.txt   +/-0x200 neighborhood dump the moment a target pins
  _huntstate.json    resumable metadata (+ _huntstate.cands binary sidecar)

Method facts baked in (full story: reference/ENGINE_HOOKING.md):
  * Change-detection on STORED values is the only reliable method. DERIVED
    values (tax, net) have no cell and collapse to 0 candidates -- that
    verdict is only trusted after TWO independent early collapses WITHIN ONE
    game session (a race or a game exit can fake one; epoch resets clear the
    death counter so evidence never accumulates across discontinuities).
  * The feed emits floats at FULL precision ("%.10g" in aai_json.lua), so
    struct.pack('<f', v) recovers the exact stored bits: floats are
    exact-byte scannable. NEVER round an oracle float.
  * Campaign narrows/scans are gated on QUIESCENCE (live gold via the
    [STABLE] targets.py path == feed gold) so end-turn flapping can't kill
    true candidates. A value is only scanned/narrowed once it has held the
    SAME bits for STABLE_GENS feed generations (Target.quiescent) -- long
    enough that the ~1-2s feed->memory lag has certainly elapsed, so the read
    matches the feed. A value moving faster than that lag (battle men/ammo in
    melee) is never narrowed, so it never collapses spuriously -- which is why
    a collapse-to-zero is now honest evidence of a computed (DERIVED) value,
    not an artifact of chasing a stale number.
  * Elimination is two-strike: a candidate that stops matching is re-read a
    cycle later -- against the previous AND current oracle bits -- before
    being dropped (feed-lag tolerance). The re-read only happens under the
    same safety gates as the narrow that created it.
  * Heap discontinuities without a pid change (loading a save / another
    campaign in-process) are detected two ways: the resolved gold-canary
    ADDRESS moving, and the campaign turn number regressing. Either one
    triggers an epoch reset: candidates dropped, verdicts + learned layout
    knowledge kept, deaths cleared.
  * Small/common ints (ap, allies, at_war, men-at-full-strength) are
    NEIGHBOR-GRADE: never solo-scanned (density prescan aborts them); they
    are mined from the pin dumps of distinctive siblings.
  * Positions: engine separates spatial components from logic structs.
    Static settlement positions pin instantly via x+y pair-fingerprint;
    battle unit positions via x+y+z triple; bearing is found for free in a
    pinned transform's neighborhood. Pause-seeded battle scans never
    immediate-pin (a "pause" can be a results screen over freed memory --
    the exporter's final phase=complete snapshot disambiguates when
    present, but we stay defensive).
"""

import array
import json
import os
import pickle
import struct
import sys
import time
import traceback

import aai_mem as am
import aai_scan as sc
from targets import TARGETS

HERE = os.path.dirname(os.path.abspath(__file__))
FEED_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\Total War Attila\data"
STATUS = os.path.join(HERE, "_hunt_status.txt")
STATUS_JSON = os.path.join(HERE, "_hunt_status.json")  # machine-readable, for aai_hunt_viz
LOG = os.path.join(HERE, "_hunt.log")
STATE_META = os.path.join(HERE, "_huntstate.json")
STATE_CANDS = os.path.join(HERE, "_huntstate.cands")
PINS_DIR = os.path.join(HERE, "_pins")

INTERVAL = 1.5              # main cycle seconds
SCAN_SLICE = 0.9            # max seconds of first-scan work per cycle
NARROW_SLICE = 1.2          # max seconds of narrowing per cycle (>=1 target)
FRESH_S = 4.0               # feed mtime younger than this = actively writing
CAP_PER_TARGET = 1_000_000  # density-prescan abort threshold (candidates)
CAP_GLOBAL = 20_000_000     # total candidates across all targets
CAP_PERSIST = 200_000       # don't persist candidate sets bigger than this
DENSITY_PROBE = 128 << 20   # bytes scanned before extrapolating hit counts
MAX_NEEDLES = 32            # needles per merged scan pass
MIN_COVERAGE = 0.9          # a scan pass below this coverage is discarded
PIN_MAX = 8                 # scalar pin: <= this many candidates ...
PIN_CHANGES = 3             # ... surviving this many distinct value changes
STABLE_GENS = 4            # feed generations a value must hold IDENTICAL before
                           # we scan/narrow it: only then has the ~1-2s feed->
                           # memory lag certainly elapsed, so the read matches the
                           # feed. Kills the false-DERIVED on churning battle
                           # men/ammo -- a value moving faster than the lag is
                           # never narrowed, so it never collapses spuriously.
PRUNE_GENS = 120            # campaign gens an oracle may vanish before retire
PINS_MAX_FILES = 300        # cap on _pins/ dump files (oldest deleted)
LOG_ROTATE = 20_000_000     # bytes before _hunt.log rolls to .1
SPAN_GAP = 4096             # batched narrow: coalesce addrs closer than this
SPAN_MAX = 256 << 10
GOLD_PATH = TARGETS["gold"][0]   # [STABLE] canary: quiescence + epoch check

WAITING, SCANNING, NARROWING, PINNED, DERIVED, LOST = (
    "waiting", "scanning", "narrowing", "PINNED", "derived", "lost")
STATUS_RANK = {PINNED: 0, NARROWING: 1, SCANNING: 2, WAITING: 3,
               DERIVED: 4, LOST: 5}

_logf = None
_stdout_dead = False


def log(msg):
    global _logf, _stdout_dead
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    if not _stdout_dead:
        try:
            print(line, flush=True)
        except OSError:
            _stdout_dead = True     # detached console died; keep file logging
    try:
        if _logf is None:
            _logf = open(LOG, "a", encoding="utf-8")
        if _logf.tell() > LOG_ROTATE:
            _logf.close()
            _logf = None
            try:
                os.replace(LOG, LOG + ".1")
            except OSError:
                pass
            _logf = open(LOG, "a", encoding="utf-8")
        _logf.write(line + "\n")
        _logf.flush()
    except OSError:
        pass


def f32_bits(v):
    return struct.unpack("<I", struct.pack("<f", float(v)))[0]


def i32_bits(v):
    return struct.unpack("<I", struct.pack("<i", int(v)))[0]


def bits_to_f32(b):
    return struct.unpack("<f", struct.pack("<I", b))[0]


def bits_to_i32(b):
    return struct.unpack("<i", struct.pack("<I", b))[0]


def f32_close(bits, want):
    return abs(bits_to_f32(bits) - want) <= max(0.02, abs(want) * 1e-4)


def atomic_write(path, data, mode="w"):
    tmp = path + ".tmp"
    with open(tmp, mode, encoding=None if "b" in mode else "utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


am.k32.GetProcessTimes.argtypes = [am.W.HANDLE] + [am.C.POINTER(am.W.FILETIME)] * 4
am.k32.GetProcessTimes.restype = am.W.BOOL


def proc_ctime(mem):
    """Process creation FILETIME as int -- the true session token (Windows
    recycles pids, so pid+modbase equality can fake a same-session resume)."""
    ts = [am.W.FILETIME() for _ in range(4)]
    if not am.k32.GetProcessTimes(mem.h, *[am.C.byref(x) for x in ts]):
        return None
    return (ts[0].dwHighDateTime << 32) | ts[0].dwLowDateTime


def read_many(mem, addrs):
    """Batched reads: {addr: u32_bits | None}. Coalesces nearby addresses into
    span RPM calls (per-address RPM is seconds at 1e5 candidates); a span that
    crosses an unmapped page falls back to per-address reads."""
    out = {}
    lst = sorted(addrs)
    n = len(lst)
    i = 0
    while i < n:
        j = i
        start = lst[i]
        while (j + 1 < n and lst[j + 1] - lst[j] <= SPAN_GAP
               and lst[j + 1] + 4 - start <= SPAN_MAX):
            j += 1
        size = lst[j] + 4 - start
        buf = None
        try:
            buf = mem.read(start, size)
        except OSError:
            pass
        if buf is not None and len(buf) >= size:
            for k in range(i, j + 1):
                out[lst[k]] = struct.unpack_from("<I", buf, lst[k] - start)[0]
        else:
            for k in range(i, j + 1):
                try:
                    out[lst[k]] = struct.unpack("<I", mem.read(lst[k], 4))[0]
                except OSError:
                    out[lst[k]] = None
        i = j + 1
    return out


# ---- feeds ------------------------------------------------------------------
class Feed:
    def __init__(self, name):
        self.path = os.path.join(FEED_DIR, name)
        self.mtime = 0.0
        self.doc = None
        self.gen = 0    # bumps when a NEW file generation parses

    def poll(self):
        try:
            mt = os.path.getmtime(self.path)
        except OSError:
            return False
        if mt == self.mtime:
            return False
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return False    # transient remove->rename gap; retry next poll
        self.mtime, self.doc = mt, doc
        self.gen += 1
        return True

    def fresh(self):
        return self.doc is not None and (time.time() - self.mtime) < FRESH_S


def human_faction(doc):
    for f in (doc or {}).get("factions") or []:
        if f.get("human"):
            return f
    return None


def _isnum(v):
    return isinstance(v, (int, float))


def campaign_oracles(doc):
    """Feed -> {target_key: spec}. Emit for ANY numeric value (narrowing works
    at any magnitude); FIRST-SCAN distinctiveness is gated in scan_eligible +
    the density prescan, not here."""
    out = {}
    hf = human_faction(doc)
    if not hf:
        return out
    r = hf.get("religion_pct")
    if _isnum(r):
        out["religion"] = dict(typ="f32", value=float(r), kind="scalar", prio=0)
    for fo in hf.get("forces") or []:
        cqi = fo.get("cqi")
        if cqi is None:
            continue
        up = fo.get("upkeep")
        if _isnum(up):
            out["upkeep.c%d" % cqi] = dict(typ="i32", value=int(up),
                                           kind="scalar", prio=2)
        x, y = fo.get("x"), fo.get("y")
        if _isnum(x) and _isnum(y) and abs(x) > 1:
            out["fpos.c%d" % cqi] = dict(typ="f32", value=float(x), kind="pair",
                                         extras=[f32_bits(y)], prio=5)
    for s in hf.get("settlements") or []:
        nm = (s.get("name") or "?").replace("att_reg_", "")
        po = s.get("public_order")
        if _isnum(po):
            out["po.%s" % nm] = dict(typ="i32", value=int(po),
                                     kind="scalar", prio=3)
        x, y = s.get("x"), s.get("y")
        if _isnum(x) and _isnum(y) and abs(x) > 1:
            out["spos.%s" % nm] = dict(typ="f32", value=float(x), kind="pair",
                                       extras=[f32_bits(y)], prio=1,
                                       static=True)
    return out


def battle_oracles(doc, epoch):
    out = {}
    for ai, al in enumerate((doc or {}).get("alliances") or []):
        for ri, ar in enumerate(al.get("armies") or []):
            for u in ar.get("units") or []:
                i = u.get("i")
                if i is None:
                    continue
                key = "b%d.a%d.%d.u%s" % (epoch, ai, ri, i)
                pos = u.get("pos") or {}
                x, y, z = pos.get("x"), pos.get("y"), pos.get("z")
                if all(_isnum(v) for v in (x, y, z)) and abs(x) > 1:
                    out["pos." + key] = dict(
                        typ="f32", value=float(x), kind="triple",
                        extras=[f32_bits(y), f32_bits(z)], prio=1,
                        domain="battle", epoch=epoch,
                        bearing=u.get("bearing"))
                men, men0 = u.get("men"), u.get("men0")
                if (isinstance(men, int) and isinstance(men0, int)
                        and men != men0 and men > 0):
                    # deferred-until-first-casualty rule: men==men0 is common
                    # AND collides across units; a bled value is distinctive.
                    out["men." + key] = dict(typ="i32", value=men,
                                             kind="scalar", prio=0,
                                             domain="battle", epoch=epoch,
                                             unit=(ai, ri, int(i)))
                amv, am0 = u.get("ammo"), u.get("ammo0")
                if (isinstance(amv, int) and isinstance(am0, int)
                        and am0 > 0 and amv != am0 and amv > 0):
                    out["ammo." + key] = dict(typ="i32", value=amv,
                                              kind="scalar", prio=2,
                                              domain="battle", epoch=epoch)
    return out


# ---- targets ----------------------------------------------------------------
class Target:
    def __init__(self, key, spec):
        self.key = key
        self.typ = spec["typ"]
        self.kind = spec["kind"]                 # scalar | pair | triple
        self.prio = spec.get("prio", 5)
        self.domain = spec.get("domain", "campaign")
        self.static = spec.get("static", False)
        self.epoch = spec.get("epoch")
        self.unit = spec.get("unit")
        self.bearing = spec.get("bearing")
        self.extras = spec.get("extras") or []
        self.value = spec["value"]
        self.status = WAITING
        self.cands = array.array("Q")
        self.suspects = array.array("Q")
        self.expected = None       # u32 bits candidates should hold now
        self.changes = 0           # distinct value-changes THIS candidate set survived
        self.deaths = 0            # early scan-collapses this epoch (DERIVED at 2)
        self.narrows_this_scan = 0
        self.note = ""
        self.hist = []             # [(gen, bits)] last two feed generations
        self.pin_addrs = []
        self.scan_bits = None      # bits the in-flight scan is looking for
        self.settled_bits = None   # refreshed every cycle by update_targets
        self.last_seen_gen = None  # last feed generation whose specs had us
        self.read_fails = 0        # consecutive whole-set read failures
        self.no_scan_bits = None   # value bits proven unscannable (too common)
        self.dense_aborts = 0      # density aborts at distinct values (LOST at 3)
        self.suspect_gen = None    # feed generation when suspects were parked
        self.stable_gens = 0       # consecutive feed gens the value held identical

    def bits(self):
        return i32_bits(self.value) if self.typ == "i32" else f32_bits(self.value)

    def observe(self, spec, gen):
        """Track this generation's oracle. Returns settled bits or None
        (settled = identical bits across the 2 most recent generations)."""
        self.value = spec["value"]
        if spec.get("extras"):
            self.extras = spec["extras"]
        if spec.get("bearing") is not None:
            self.bearing = spec["bearing"]
        b = self.bits()
        if not self.hist or self.hist[-1][0] != gen:
            prev = self.hist[-1][1] if self.hist else None
            self.stable_gens = (self.stable_gens + 1) if prev == b else 1
            self.hist.append((gen, b))
            self.hist = self.hist[-2:]
        if len(self.hist) == 2 and self.hist[0][1] == self.hist[1][1]:
            return self.hist[1][1]
        return None

    def quiescent(self):
        """Has the value held still long enough that a memory read is sure to
        match the feed? (Below this it is moving faster than our read lag --
        narrowing it would chase a stale number and drop the true cell.)"""
        return self.settled_bits is not None and self.stable_gens >= STABLE_GENS

    def fval(self):
        return ("%.4f" % self.value) if self.typ == "f32" else str(int(self.value))

    def distinctive_bits(self, bits):
        """Is this value worth a solo first scan? (Common small ints drown in
        hits; the density prescan is the backstop for what slips through.)"""
        if self.typ == "f32":
            return abs(bits_to_f32(bits)) > 1e-3
        return abs(bits_to_i32(bits)) >= 8


# ---- merged multi-needle incremental scan ------------------------------------
class ScanPass:
    def __init__(self, mem, jobs, paused=False):
        """jobs: {key: needle_bytes}. Identical needles are deduped -- one
        find-loop serves every target wanting those bits."""
        self.keys_by_needle = {}
        for k, nb in jobs.items():
            self.keys_by_needle.setdefault(nb, []).append(k)
        self.hits = {nb: array.array("Q") for nb in self.keys_by_needle}
        self.regions = [(b, s) for b, s, _ in sc.regions(mem)]
        self.total = sum(s for _, s in self.regions) or 1
        self.scanned = 0
        self.ri = 0
        self.off = 0
        self.aborted = set()      # needles projected past CAP_PER_TARGET
        self.paused = paused      # seeded from a frozen (paused?) battle
        self.t0 = time.time()

    def done(self):
        return self.ri >= len(self.regions)

    def step(self, mem, chunk=4 << 20):
        if self.done():
            return
        base, size = self.regions[self.ri]
        n = min(chunk, size - self.off)
        buf = b""
        try:
            buf = mem.read(base + self.off, n)
        except OSError:
            pass
        if buf:
            self.scanned += len(buf)
            b0 = base + self.off
            for nb, arr in self.hits.items():
                if nb in self.aborted:
                    continue
                i = buf.find(nb)
                while i != -1:
                    a = b0 + i
                    if a % 4 == 0:
                        arr.append(a)
                    i = buf.find(nb, i + 1)
        if not buf or len(buf) < n:
            self.ri += 1
            self.off = 0
        else:
            self.off += len(buf)
            if self.off >= size:
                self.ri += 1
                self.off = 0
        if self.scanned >= DENSITY_PROBE:
            proj = self.total / max(1, self.scanned)
            for nb, arr in self.hits.items():
                if nb not in self.aborted and len(arr) * proj > CAP_PER_TARGET:
                    self.aborted.add(nb)


# ---- the hunter ---------------------------------------------------------------
class Hunter:
    def __init__(self):
        self.mem = None
        self.pid = None
        self.modbase = None
        self.ctime = None
        self.camp = Feed("aai_campaign.json")
        self.batt = Feed("aai_battle.json")
        self.targets = {}
        self.scanpass = None
        self.battle_epoch = 0
        self.battle_last_t = None
        self.battle_live = False
        self.learned = {}
        self.block_snap = None
        self.block_turn = None
        self.world = "starting"
        self.quiescent = False
        self.canary_live = None
        self.canary_addr = None
        self.canary_pending = None
        self.last_turn = None
        self.dirty = False
        self.last_save = 0.0
        self.last_save_err = None
        self.resume_meta = None

    # -- process / epoch --
    def connect(self):
        pid = am.find_pid()
        if not pid:
            if self.mem is not None:
                log("game closed (pid %s gone) -- waiting for restart" % self.pid)
                try:
                    self.mem.close()
                except Exception:
                    pass
                self.mem = None
            return False
        if self.mem is not None and pid == self.pid:
            return True
        try:
            mem = am.Mem(pid)
        except (OSError, RuntimeError):
            return False
        try:
            modbase = mem.module_base()
        except (OSError, KeyError):
            mem.close()     # engine module not loaded yet; don't leak the handle
            return False
        if self.mem is not None:
            try:
                self.mem.close()
            except Exception:
                pass
        old_pid = self.pid
        old_ctime = self.ctime
        self.mem, self.pid, self.modbase = mem, pid, modbase
        self.ctime = proc_ctime(mem)
        log("attached: Attila.exe pid=%d  %s @ 0x%08X"
            % (pid, am.ENGINE_MODULE, modbase))
        if old_pid is not None and old_pid != pid:
            self.epoch_reset("game restarted (pid %s -> %s)" % (old_pid, pid))
        elif (old_pid == pid and old_ctime is not None
              and self.ctime != old_ctime):
            # Windows recycled the pid for a NEW game process
            self.epoch_reset("same pid, new process (creation time changed)")
        elif self.resume_meta is not None:
            self.apply_resume()
        return True

    def epoch_reset(self, reason):
        """The address space is discontinuous (new process, or a save/campaign
        loaded in-process): every raw address is invalid. Keep verdicts and
        learned layout knowledge; requeue everything else with a CLEAN death
        counter -- collapse evidence never accumulates across discontinuities."""
        log("EPOCH RESET: %s" % reason)
        for key in [k for k, t in self.targets.items() if t.domain == "battle"]:
            del self.targets[key]
        self.battle_live = False
        self.battle_last_t = None
        for t in self.targets.values():
            if t.status in (DERIVED, LOST):
                continue
            was = t.status
            t.status = WAITING
            t.cands = array.array("Q")
            t.suspects = array.array("Q")
            t.expected = None
            t.changes = 0
            t.deaths = 0
            t.narrows_this_scan = 0
            t.read_fails = 0
            t.suspect_gen = None
            t.stable_gens = 0
            t.hist = []
            t.note = "epoch reset (was %s)" % was
        self.scanpass = None
        self.block_snap = None
        self.block_turn = None
        self.canary_addr = None
        self.canary_pending = None
        self.last_turn = None
        self.dirty = True

    def check_canary(self):
        """live gold (stable path) == feed gold => campaign memory quiescent:
        safe to eliminate candidates. Doubles as the heap-discontinuity check:
        the canary ADDRESS moving with the pid unchanged means a save or a
        different campaign was loaded in-process."""
        self.quiescent = False
        self.canary_live = None
        hf = human_faction(self.camp.doc)
        if not (self.mem and hf and isinstance(hf.get("gold"), int)):
            return
        try:
            addr = self.mem.resolve(GOLD_PATH)
            live = struct.unpack("<i", self.mem.read(addr, 4))[0]
        except OSError:
            return
        if self.canary_addr is not None and addr != self.canary_addr:
            # the most destructive action in the program: require the SAME new
            # address on two consecutive cycles (one torn resolve mid-update
            # must not nuke every candidate set).
            if addr == self.canary_pending:
                self.epoch_reset("campaign heap rebuilt (gold canary moved "
                                 "0x%08X -> 0x%08X: save/campaign loaded?)"
                                 % (self.canary_addr, addr))
                self.canary_addr = addr
                self.canary_pending = None
            else:
                self.canary_pending = addr
            return
        self.canary_pending = None
        self.canary_addr = addr
        self.canary_live = live
        self.quiescent = (live == hf["gold"]) and self.camp.fresh()

    def canary_ok(self):
        """Cheap mid-cycle re-check before each campaign narrow: quiescence can
        be lost between check_canary and a narrow seconds later (end turn)."""
        hf = human_faction(self.camp.doc)
        if not hf or not isinstance(hf.get("gold"), int):
            return False
        try:
            a = self.mem.resolve(GOLD_PATH)
            return (a == self.canary_addr
                    and struct.unpack("<i", self.mem.read(a, 4))[0] == hf["gold"])
        except OSError:
            return False

    def detect_reload(self):
        """Second discontinuity signal: campaign turn number regressed."""
        turn = (self.camp.doc or {}).get("turn")
        if not (self.camp.fresh() and isinstance(turn, (int, float))):
            return
        if self.last_turn is not None and turn < self.last_turn:
            self.epoch_reset("turn regressed %s -> %s (save loaded)"
                             % (self.last_turn, turn))
        self.last_turn = turn

    # -- world detection --
    def detect_world(self):
        doc = self.batt.doc or {}
        if self.battle_live and doc.get("phase") == "complete":
            # the exporter's final snapshot: a positive end-of-battle signal
            log("battle #%d complete -> retiring its targets" % self.battle_epoch)
            self.retire_battle(keep_epoch=None)
            self.battle_live = False
        cf, bf = self.camp.fresh(), self.batt.fresh()
        if bf and doc.get("phase") != "complete":
            t = doc.get("t") or 0
            if (not self.battle_live
                    or (self.battle_last_t is not None and t < self.battle_last_t)):
                self.retire_battle(keep_epoch=None)
                self.battle_epoch += 1
                self.battle_live = True
                log("battle #%d begins (t=%s, phase=%s)"
                    % (self.battle_epoch, t, doc.get("phase")))
            self.battle_last_t = t
            self.world = "battle"
        elif self.battle_live:
            if cf:
                log("battle #%d over (campaign feed resumed) -> retiring" % self.battle_epoch)
                self.retire_battle(keep_epoch=None)
                self.battle_live = False
                self.world = "campaign"
            elif time.time() - self.batt.mtime > 180:
                # no complete-snapshot and no campaign resume (quit to menu?):
                # a real pause rarely lasts this long -- stop pause-scanning.
                log("battle #%d: feed silent >3min -> treating as over"
                    % self.battle_epoch)
                self.retire_battle(keep_epoch=None)
                self.battle_live = False
                self.world = "idle"
            else:
                self.world = "paused"    # battle paused (or results screen)
        elif cf:
            self.world = "campaign"
        else:
            self.world = "idle"          # menu / loading

    def retire_battle(self, keep_epoch):
        gone = [k for k, t in self.targets.items()
                if t.domain == "battle" and t.epoch != keep_epoch]
        for k in gone:
            t = self.targets[k]
            if t.status == PINNED:
                log("  retired pinned %s (dump kept in _pins/)" % k)
            del self.targets[k]
        if gone:
            self.dirty = True

    # -- registry --
    def update_targets(self):
        specs = {}
        if self.camp.doc and self.camp.fresh():
            specs.update(campaign_oracles(self.camp.doc))
        if self.battle_live and self.batt.doc:
            specs.update(battle_oracles(self.batt.doc, self.battle_epoch))
        for t in self.targets.values():
            t.settled_bits = None    # only targets present in THIS feed narrow
        for key, spec in specs.items():
            t = self.targets.get(key)
            if t is None:
                t = Target(key, spec)
                self.targets[key] = t
                self.dirty = True
            gen = self.camp.gen if t.domain == "campaign" else self.batt.gen
            t.settled_bits = t.observe(spec, gen)
            t.last_seen_gen = gen
        # retire campaign targets whose oracle left a LIVE feed long ago
        # (army destroyed, settlement lost); verdicts and pins stay.
        if self.camp.fresh():
            for key in list(self.targets):
                t = self.targets[key]
                if (t.domain == "campaign"
                        and t.status not in (PINNED, DERIVED, LOST)
                        and t.last_seen_gen is not None
                        and self.camp.gen - t.last_seen_gen > PRUNE_GENS):
                    log("%s: oracle left the feed -- retired" % key)
                    del self.targets[key]
                    self.dirty = True

    # -- narrowing --
    def narrow_allowed(self, t):
        if t.domain == "campaign":
            return self.world == "campaign" and self.quiescent
        return self.world == "battle"    # settled rule provides battle safety

    def narrow_phase(self):
        deadline = time.time() + NARROW_SLICE
        # second strikes first, under the SAME gates that created the suspects,
        # and only once the oracle produced a NEW settled generation (a stale
        # feed would re-judge suspects against the same stale bits)
        for t in list(self.targets.values()):
            if not (len(t.suspects) and t.status in (NARROWING, PINNED)
                    and self.narrow_allowed(t)):
                continue
            gen = self.camp.gen if t.domain == "campaign" else self.batt.gen
            if t.suspect_gen is not None and gen <= t.suspect_gen:
                continue
            if t.settled_bits is None:
                continue
            if not t.quiescent():
                continue          # re-judge suspects only on a still value, as
                                  # with the narrow that created them
            if t.domain == "campaign" and not self.canary_ok():
                continue
            self.second_strike(t)
        done_one = False
        for t in sorted(self.targets.values(), key=lambda x: x.prio):
            # PINNED targets keep narrowing too: free ongoing verification,
            # and a freed/moved object demotes itself back to WAITING.
            if t.status not in (NARROWING, PINNED) or t.settled_bits is None:
                continue
            if t.settled_bits == t.expected:
                continue
            if not t.quiescent():
                # value still moving faster than our read lag: narrowing now
                # would chase a stale number and drop the true cell (the old
                # false-DERIVED on melee men/ammo). Wait for it to hold still.
                t.note = "value too volatile to narrow yet (stable %d/%d gens)" % (
                    t.stable_gens, STABLE_GENS)
                continue
            if len(t.suspects):
                continue          # resolve pending strikes before a new narrow
            if not self.narrow_allowed(t):
                continue
            if done_one and time.time() > deadline:
                break             # settled_bits persists; resume next cycle
            if t.domain == "campaign" and not self.canary_ok():
                break             # quiescence lost mid-cycle: defer, never eliminate
            self.narrow(t, t.settled_bits)
            done_one = True

    def narrow(self, t, new_bits):
        reads = read_many(self.mem, t.cands)
        if len(t.cands) and not any(b is not None for b in reads.values()):
            self.read_fail(t, "narrow")
            return
        t.read_fails = 0
        want = bits_to_f32(new_bits) if t.typ == "f32" else None
        keep = array.array("Q")
        susp = array.array("Q")
        for a in t.cands:
            bits = reads.get(a)
            if bits is not None and (bits == new_bits or
                                     (want is not None and f32_close(bits, want))):
                keep.append(a)
            else:
                susp.append(a)     # strike 1 (incl. unreadable): re-check next cycle
        before = len(t.cands)
        t.cands, t.suspects = keep, susp
        t.suspect_gen = self.camp.gen if t.domain == "campaign" else self.batt.gen
        t.expected = new_bits
        t.changes += 1
        t.narrows_this_scan += 1
        self.dirty = True
        log("%s: narrow %d -> %d keep +%d suspect  (change #%d, value=%s)"
            % (t.key, before, len(keep), len(susp), t.changes, t.fval()))
        if not len(susp):
            self.after_narrow(t)

    def read_fail(self, t, what):
        """A whole set unreadable: either the process is transitioning (defer)
        or the object was freed while its oracle lives on. Three consecutive
        failures with the game alive = freed: requeue with NO death strike --
        unreadable memory is not evidence that a value is derived."""
        t.read_fails += 1
        if t.read_fails < 3 or am.find_pid() != self.pid:
            log("%s: every read failed -- %s deferred (game transitioning?)"
                % (t.key, what))
            return
        t.read_fails = 0
        t.cands = array.array("Q")
        t.suspects = array.array("Q")
        t.expected = None
        t.changes = 0
        was_pin = t.status == PINNED
        t.status = WAITING
        t.note = ("pin address became unreadable (freed) -- rescan" if was_pin
                  else "candidates unreadable (freed) -- rescan, no verdict")
        log("%s: %s unreadable 3x with game alive -> rescan (no death strike)"
            % (t.key, "pin" if was_pin else "candidate set"))
        self.dirty = True

    def second_strike(self, t):
        reads = read_many(self.mem, t.suspects)
        if len(t.suspects) and not any(b is not None for b in reads.values()):
            self.read_fail(t, "second strike")
            return
        t.read_fails = 0
        # accept against the bits we narrowed to AND the oracle's current bits
        # (memory may legitimately be one step ahead of the feed)
        acc = set()
        if t.expected is not None:
            acc.add(t.expected)
        if t.settled_bits is not None:
            acc.add(t.settled_bits)   # caller guarantees a settled oracle
        accf = [bits_to_f32(b) for b in acc] if t.typ == "f32" else []
        back = 0
        for a in t.suspects:
            bits = reads.get(a)
            if bits is None:
                continue           # readable process, unreadable page: freed
            if bits in acc or any(f32_close(bits, f) for f in accf):
                t.cands.append(a)
                back += 1
        dropped = len(t.suspects) - back
        t.suspects = array.array("Q")
        if back or dropped:
            log("%s: second strike -> %d recovered, %d dropped (%d candidates)"
                % (t.key, back, dropped, len(t.cands)))
        self.dirty = True
        self.after_narrow(t)

    def after_narrow(self, t):
        if t.status == PINNED:
            if not len(t.cands):
                t.status = WAITING
                t.changes = 0      # a fresh candidate set must earn its own pin
                t.expected = None
                t.note = "pin lost (object moved/freed) -- rescan"
                log("%s: pin LOST -> rescan" % t.key)
            else:
                t.pin_addrs = sorted(set(t.cands))
            return
        if len(t.cands) == 0:
            if t.domain != "campaign":
                # No battle field (men/ammo/position) is computed-on-demand --
                # they are ALL stored, so a battle collapse is NEVER evidence of
                # "no cell". Feed lag also means quiescent() can't fully prove
                # memory==feed for a fast-mover. So battle collapses just requeue
                # and rescan -- never a death, never DERIVED.
                t.status = WAITING
                t.expected = None
                t.note = "battle collapse -- value moved, rescan (no verdict)"
                log("%s: battle collapse -> rescan (battle values are stored; "
                    "no derived verdict)" % t.key)
                return
            if t.narrows_this_scan <= 2:
                # this collapse is trustworthy: we only scan/narrow a QUIESCENT
                # value (held >= STABLE_GENS), so a vanish means no cell held it
                # while it sat still -- genuine evidence of a computed value.
                t.deaths += 1
                if t.deaths >= 2:
                    t.status = DERIVED
                    t.note = "no stored cell (2 collapses of a value held still)"
                    log("%s: VERDICT DERIVED -- vanished twice while the value was "
                        "held still; computed on demand, not stored" % t.key)
                else:
                    t.status = WAITING
                    t.note = "collapse #1 on a quiescent value -- one more to confirm"
                    log("%s: collapsed on narrow #%d (value was quiescent) -> "
                        "one more to confirm derived (death 1/2)"
                        % (t.key, t.narrows_this_scan))
            else:
                t.status = WAITING
                t.note = "mature set collapsed -- race suspected, rescanning"
                log("%s: mature candidate set collapsed (change #%d) -- race "
                    "suspected, NOT counting toward derived" % (t.key, t.changes))
            t.expected = None
            return
        if len(t.cands) <= PIN_MAX and t.changes >= PIN_CHANGES:
            self.pin(t)

    # -- pinning --
    def pin(self, t, label="PIN"):
        t.status = PINNED
        t.deaths = 0               # a pin refutes any earlier collapse strikes
        t.note = ""
        t.pin_addrs = sorted(set(t.cands))
        addrs = ", ".join("0x%08X" % a for a in t.pin_addrs[:8])
        log("*** %s %s = %s @ [%s]%s" % (
            label, t.key, t.fval(), addrs,
            "" if len(t.pin_addrs) <= 8 else " +%d more" % (len(t.pin_addrs) - 8)))
        log("    durable path: python aai_scan.py ptrscan 0x%08X" % t.pin_addrs[0])
        for a in t.pin_addrs[:4]:
            self.dump_neighborhood(t, a)
        pins = self.learned.setdefault("pins", {})
        pins[t.key] = {
            "value": t.value, "addrs": t.pin_addrs[:16],
            "when": time.strftime("%Y-%m-%d %H:%M:%S")}
        if len(pins) > 60:    # battle keys churn per epoch; keep newest 40
            for old_k in sorted(pins,
                                key=lambda x: pins[x].get("when", ""))[:len(pins) - 40]:
                del pins[old_k]
        if t.key.startswith("men.") and t.unit and label == "PIN":
            self.learn_stride(t)   # organic pins only: no pin->stride recursion
        if t.kind == "triple" and _isnum(t.bearing):
            self.find_bearing(t)
        self.dirty = True

    def dump_neighborhood(self, t, addr, span=0x200):
        os.makedirs(PINS_DIR, exist_ok=True)
        name = "%s_0x%08X.txt" % (t.key.replace(".", "_"), addr)
        rows = ["pin dump: %s = %s @ 0x%08X   %s" % (
            t.key, t.fval(), addr, time.strftime("%Y-%m-%d %H:%M:%S")),
            "changes survived: %d   candidates at pin: %s" % (
                t.changes, ", ".join("0x%08X" % a for a in t.pin_addrs[:16])),
            "", "  offset      u32          i32            f32"]
        try:
            buf = self.mem.read(addr - span, 2 * span + 4)
            lo = addr - span
        except OSError:
            try:
                buf = self.mem.read(addr, span + 4)
                lo = addr
            except OSError:
                return
        for off in range(0, len(buf) - 3, 4):
            a = lo + off
            u = struct.unpack_from("<I", buf, off)[0]
            iv = struct.unpack_from("<i", buf, off)[0]
            fv = struct.unpack_from("<f", buf, off)[0]
            mark = "  <== %s" % t.key if a == addr else ""
            rows.append("  %+07X  %08X  %12d  %14.4f%s"
                        % (a - addr, u, iv, fv, mark))
        try:
            atomic_write(os.path.join(PINS_DIR, name), "\n".join(rows) + "\n")
            log("    neighborhood dumped -> _pins/%s" % name)
        except OSError as ex:
            log("    dump failed: %s" % ex)
        self.prune_pins()

    def prune_pins(self):
        try:
            files = sorted((os.path.join(PINS_DIR, f) for f in os.listdir(PINS_DIR)),
                           key=os.path.getmtime)
            while len(files) > PINS_MAX_FILES:
                os.remove(files.pop(0))
        except OSError:
            pass

    def find_bearing(self, t):
        """A pinned pos-triple is a transform struct; bearing should live in
        its neighborhood -- no solo scan needed."""
        if abs(t.bearing) < 0.01:
            return    # 0.0 bits match any zeroed dword -- garbage offsets
        nb = struct.pack("<I", f32_bits(t.bearing))
        for a in t.pin_addrs[:4]:
            try:
                buf = self.mem.read(a - 0x100, 0x204)
            except OSError:
                continue
            i = buf.find(nb)
            while i != -1:
                if (a - 0x100 + i) % 4 == 0 and (a - 0x100 + i) != a:
                    off = (a - 0x100 + i) - a
                    log("    bearing bits found at pos.x%+#x (%s)" % (off, t.key))
                    offs = self.learned.setdefault("bearing_offsets", [])
                    if off not in offs:
                        offs.append(off)
                    break
                i = buf.find(nb, i + 1)

    def learn_stride(self, t):
        """Two pinned units of one army reveal the unit-array stride; predict
        every sibling's address and confirm with one read each. This is how
        battle #2+ pins whole armies with zero scans."""
        if len(t.pin_addrs) != 1 or not t.unit:
            return
        ai, ri, i = t.unit
        sibs = []
        for x in self.targets.values():
            if (x is not t and x.status == PINNED and x.unit
                    and x.key.startswith("men.")
                    and x.epoch == t.epoch and x.unit[0] == ai
                    and x.unit[1] == ri and len(x.pin_addrs) == 1):
                sibs.append((x.unit[2], x.pin_addrs[0]))
        sibs.append((i, t.pin_addrs[0]))
        sibs.sort()
        if len(sibs) < 2:
            return
        (i1, a1), (i2, a2) = sibs[0], sibs[1]
        if i2 == i1 or (a2 - a1) % (i2 - i1):
            return
        stride = (a2 - a1) // (i2 - i1)
        if not (16 <= abs(stride) <= 0x10000):
            return
        strides = self.learned.setdefault("unit_stride", [])
        if stride not in strides:
            strides.append(stride)
            log("    unit-array stride learned: 0x%X -- predicting siblings" % stride)
        doc = self.batt.doc or {}
        try:
            units = doc["alliances"][ai]["armies"][ri]["units"]
        except (KeyError, IndexError, TypeError):
            return
        for u in units:
            ui = u.get("i")
            men = u.get("men")
            if ui is None or not isinstance(men, int):
                continue
            key = "men.b%d.a%d.%d.u%s" % (t.epoch, ai, ri, ui)
            ex = self.targets.get(key)
            if ex is not None and ex.status == PINNED:
                continue           # live check, not a stale precomputed set
            pred = a1 + stride * (int(ui) - i1)
            try:
                got = struct.unpack("<i", self.mem.read(pred, 4))[0]
            except OSError:
                continue
            if got == men:
                nt = ex or Target(key, dict(
                    typ="i32", value=men, kind="scalar", prio=0,
                    domain="battle", epoch=t.epoch, unit=(ai, ri, int(ui))))
                self.targets[key] = nt
                nt.value = men
                nt.cands = array.array("Q", [pred])
                nt.suspects = array.array("Q")
                nt.expected = i32_bits(men)
                nt.changes = PIN_CHANGES
                self.pin(nt, label="STRIDE-PIN")

    # -- first scans --
    def scan_eligible(self, t):
        if t.status != WAITING or t.deaths >= 2:
            return None
        if t.domain == "campaign":
            if self.world != "campaign" or not self.quiescent:
                return None
            bits = t.settled_bits
        elif not self.battle_live:
            return None
        elif self.world == "battle":
            bits = t.settled_bits
        elif self.world == "paused" and t.hist:
            bits = t.hist[-1][1]    # frozen memory: last value is exact
        else:
            return None
        if bits is None or not t.distinctive_bits(bits):
            return None
        if t.no_scan_bits is not None and bits == t.no_scan_bits:
            return None    # this exact value already proved unscannable
        if self.world != "paused" and not t.quiescent():
            return None    # value still moving faster than the read lag; a scan
                           # now would miss the true (already-moved) cell
        return bits

    def scan_phase(self, deadline):
        if self.scanpass is None:
            jobs = {}
            for t in sorted(self.targets.values(), key=lambda x: x.prio):
                if len(jobs) >= MAX_NEEDLES:
                    break
                bits = self.scan_eligible(t)
                if bits is None:
                    continue
                t.status = SCANNING
                t.scan_bits = bits
                jobs[t.key] = struct.pack("<I", bits)
            if jobs:
                self.scanpass = ScanPass(self.mem, jobs,
                                         paused=(self.world == "paused"))
                log("scan pass: %d target(s), %d needle(s)%s: %s"
                    % (len(jobs), len(self.scanpass.keys_by_needle),
                       " [paused]" if self.scanpass.paused else "",
                       ", ".join(sorted(jobs))))
            else:
                return
        sp = self.scanpass
        camp_jobs = any(k in self.targets and self.targets[k].domain == "campaign"
                        for ks in sp.keys_by_needle.values() for k in ks)
        if camp_jobs and (self.world != "campaign" or not self.quiescent):
            log("scan pass aborted: campaign world/quiescence lost mid-pass")
            self.abort_scanpass("scan aborted (world moved)")
            return
        while time.time() < deadline and not sp.done():
            sp.step(self.mem)
        if sp.done():
            self.seed_from_pass(sp)
            self.scanpass = None

    def abort_scanpass(self, note):
        for ks in self.scanpass.keys_by_needle.values():
            for k in ks:
                t = self.targets.get(k)
                if t and t.status == SCANNING:
                    t.status = WAITING
                    t.note = note
        self.scanpass = None

    def seed_from_pass(self, sp):
        coverage = sp.scanned / max(1.0, float(sp.total))
        if am.find_pid() != self.pid or coverage < MIN_COVERAGE:
            # the pass raced a dying/loading process: its emptiness is not
            # evidence about the game's memory. Requeue with NO death strikes.
            log("scan pass DISCARDED (coverage %.0f%%) -- targets requeued, "
                "no verdicts drawn" % (100 * coverage))
            for ks in sp.keys_by_needle.values():
                for k in ks:
                    t = self.targets.get(k)
                    if t and t.status == SCANNING:
                        t.status = WAITING
                        t.note = "scan discarded (incomplete coverage)"
            return
        log("scan pass done: %.0f MB in %.1fs%s"
            % (sp.scanned / 1e6, time.time() - sp.t0,
               " [paused-seeded]" if sp.paused else ""))
        for nb, keys in sp.keys_by_needle.items():
            hits = sp.hits[nb]
            for key in keys:
                t = self.targets.get(key)
                if t is None or t.status != SCANNING:
                    continue
                if nb in sp.aborted:
                    # one common VALUE is not a verdict on the FIELD: retry
                    # when the value changes; 3 dense values = neighbor-grade.
                    t.dense_aborts += 1
                    t.no_scan_bits = t.scan_bits
                    if t.dense_aborts >= 3:
                        t.deaths = 2
                        t.status = LOST
                        t.note = ("too common at 3 distinct values -- "
                                  "find as a struct neighbor")
                        log("%s: too common at 3 distinct values -- "
                            "neighbor-grade" % key)
                    else:
                        t.status = WAITING
                        t.note = ("too common at value %s -- retry when it "
                                  "changes" % t.fval())
                    continue
                if t.hist and t.hist[-1][1] != t.scan_bits:
                    t.status = WAITING
                    t.note = "value moved mid-scan -- rescan"
                    continue
                if t.kind in ("pair", "triple"):
                    self.seed_pair(t, hits, sp.paused)
                else:
                    self.seed_scalar(t, hits, sp.paused)
        total = sum(len(t.cands) for t in self.targets.values())
        if total > CAP_GLOBAL:
            for t in sorted(self.targets.values(),
                            key=lambda x: (-x.prio, -len(x.cands))):
                if total <= CAP_GLOBAL:
                    break
                if t.status == NARROWING and t.prio >= 3:
                    total -= len(t.cands)
                    t.cands = array.array("Q")
                    t.status = WAITING
                    t.note = "dropped (RAM budget) -- will rescan"

    def seed_scalar(self, t, hits, paused=False):
        t.cands = array.array("Q", hits)
        t.suspects = array.array("Q")
        t.expected = t.scan_bits
        t.narrows_this_scan = 0
        t.changes = 0              # a new candidate set earns its own pin
        if not len(t.cands):
            if paused:
                t.status = WAITING
                t.no_scan_bits = t.scan_bits   # don't re-scan the frozen value
                t.note = "0 hits from pause-seeded scan -- retry on a new value"
                return             # frozen-or-freed memory proves nothing
            self.after_narrow(t)   # clean 0-hit scan = one collapse strike
            return
        t.status = NARROWING
        t.note = ""
        log("%s: first scan -> %d candidates (value=%s)"
            % (t.key, len(t.cands), t.fval()))

    def seed_pair(self, t, hits, paused=False):
        survivors = self.adjacency_gate(t, hits)
        log("%s: %d x-hits -> %d with %s adjacency (value=%s)"
            % (t.key, len(hits), len(survivors),
               "y" if t.kind == "pair" else "y+z", t.fval()))
        t.suspects = array.array("Q")
        if not survivors:
            t.status = WAITING
            if paused:
                t.no_scan_bits = t.scan_bits
                t.note = "pause-seeded pair scan empty -- retry on a new value"
                return
            t.deaths += 1
            if t.deaths >= 2:
                t.status = LOST
                t.note = "no co-located pair found twice"
            return
        t.cands = array.array("Q", survivors)
        t.expected = t.scan_bits
        t.narrows_this_scan = 0
        t.changes = 0
        if paused:
            # a "pause" may be a results screen over freed-but-unzeroed heap:
            # never immediate-pin from one; demand live confirmation instead.
            t.status = NARROWING
            t.note = "pause-seeded -- needs live confirmation"
        elif t.static or t.kind == "triple":
            # static settlement pair / live battle triple: adjacency IS the proof
            t.changes = PIN_CHANGES
            self.pin(t, label="PAIR-PIN (%d copies)" % len(survivors))
        else:
            t.status = NARROWING     # moving pos: confirm via settled changes

    def adjacency_gate(self, t, hits, window=0x44):
        """Keep x-hits whose extra components' exact bits appear within
        +/-window at a distinct 4-aligned slot (same feed generation)."""
        out = []
        for a in hits[:20000]:
            try:
                buf = self.mem.read(a - window, 2 * window + 4)
                lo = a - window
            except OSError:
                try:
                    buf = self.mem.read(a, window + 4)
                    lo = a
                except OSError:
                    continue
            ok = True
            for eb in t.extras:
                nb = struct.pack("<I", eb)
                found = False
                i = buf.find(nb)
                while i != -1:
                    if (lo + i) % 4 == 0 and (lo + i) != a:
                        found = True
                        break
                    i = buf.find(nb, i + 1)
                if not found:
                    ok = False
                    break
            if ok:
                out.append(a)
        return out

    # -- faction-block watch (subsumes _track.py) --
    def block_watch(self):
        turn = (self.camp.doc or {}).get("turn")
        if turn is None or turn == self.block_turn or not self.quiescent:
            return
        try:
            base = self.mem.resolve(GOLD_PATH) - 0xDC
            buf = self.mem.read(base, 0x180)
        except OSError:
            return
        if self.block_snap is not None and self.block_turn is not None:
            old = self.block_snap
            diffs = []
            for off in range(0, min(len(old), len(buf)) - 3, 4):
                a = struct.unpack_from("<i", old, off)[0]
                b = struct.unpack_from("<i", buf, off)[0]
                if a != b:
                    diffs.append("+0x%X: %d->%d" % (off, a, b))
            if diffs:
                log("faction-block diff turn %s->%s: %s"
                    % (self.block_turn, turn, "; ".join(diffs[:24])))
        self.block_snap = buf
        self.block_turn = turn
        self.dirty = True

    # -- status / persistence --
    def write_status(self):
        hf = human_faction(self.camp.doc)
        rows = [
            "AAI FIELD HUNTER   %s   world=%s" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), self.world),
            "game: %s   canary(gold): live=%s feed=%s -> %s" % (
                ("pid %s" % self.pid) if self.mem else "NOT RUNNING (waiting)",
                self.canary_live, hf.get("gold") if hf else None,
                "QUIESCENT (safe to narrow)" if self.quiescent else "flapping/stale"),
            "turn: %s   battle: %s" % (
                (self.camp.doc or {}).get("turn"),
                ("#%d live" % self.battle_epoch) if self.battle_live else "none"),
            "",
            "%-26s %-4s %12s %9s %8s %7s  %s" % (
                "target", "typ", "value", "cands", "suspect", "changes", "status"),
            "-" * 92,
        ]
        shown = 0
        for t in sorted(self.targets.values(),
                        key=lambda x: (STATUS_RANK.get(x.status, 9), x.prio, x.key)):
            if shown >= 60:
                rows.append("  ... %d more targets" % (len(self.targets) - shown))
                break
            extra = t.note
            if t.status == PINNED and t.pin_addrs:
                extra = "@ " + ", ".join("0x%08X" % a for a in t.pin_addrs[:3]) + (
                    " +%d" % (len(t.pin_addrs) - 3) if len(t.pin_addrs) > 3 else "")
            rows.append("%-26s %-4s %12s %9d %8d %7d  %s %s" % (
                t.key[:26], t.typ, t.fval()[:12], len(t.cands),
                len(t.suspects), t.changes, t.status, extra))
            shown += 1
        if self.scanpass:
            pct = 100.0 * self.scanpass.scanned / max(1, self.scanpass.total)
            rows.append("")
            rows.append("scan pass in flight: %.0f%% of memory" % pct)
        rows += [
            "",
            "not hunted (by design): tax/net = DERIVED (no cell) | gold/income ="
            " already pinned (targets.py) | at_war/allies/ap/season = too-common"
            " ints, mined from pin dumps instead",
            "statuses: waiting->scanning->narrowing->PINNED | derived ="
            " computed-on-demand (no cell) | lost = unscannable, mine as neighbor."
            " Pins dump to _pins/ and want a ptrscan for a restart-stable path.",
        ]
        try:
            atomic_write(STATUS, "\n".join(rows) + "\n")
        except OSError:
            pass

    def write_status_json(self):
        """Machine-readable mirror of the scoreboard for the standalone
        dashboard (aai_hunt_viz.py). Written every cycle."""
        hf = human_faction(self.camp.doc)
        counts = {}
        for t in self.targets.values():
            counts[t.status] = counts.get(t.status, 0) + 1
        rows = []
        for t in sorted(self.targets.values(),
                        key=lambda x: (STATUS_RANK.get(x.status, 9), x.prio, x.key)):
            rows.append({
                "key": t.key, "typ": t.typ, "value": t.fval(),
                "cands": len(t.cands), "suspects": len(t.suspects),
                "changes": t.changes, "deaths": t.deaths,
                "status": t.status, "domain": t.domain, "note": t.note,
                "pins": ["0x%08X" % a for a in t.pin_addrs[:6]],
            })
        doc = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "world": self.world,
            "attached": bool(self.mem),
            "turn": (self.camp.doc or {}).get("turn"),
            "battle": ("#%d" % self.battle_epoch) if self.battle_live else None,
            "canary": {"live": self.canary_live,
                       "feed": hf.get("gold") if hf else None,
                       "quiescent": self.quiescent},
            "scan_pct": (round(100.0 * self.scanpass.scanned
                               / max(1, self.scanpass.total), 1)
                         if self.scanpass else None),
            "counts": counts, "total": len(self.targets), "targets": rows,
        }
        try:
            atomic_write(STATUS_JSON, json.dumps(doc))
        except OSError:
            pass

    def maybe_save(self, force=False):
        if self.resume_meta is not None:
            return    # never clobber saved state that was never merged
        if not self.dirty and not force:
            return
        if not force and time.time() - self.last_save < 5.0:
            return
        meta = {
            "pid": self.pid, "modbase": self.modbase, "ctime": self.ctime,
            "battle_epoch": self.battle_epoch,
            "block_turn": self.block_turn,
            "block_snap": self.block_snap.hex() if self.block_snap else None,
            "learned": self.learned,
            "targets": {},
        }
        cands = {}
        for k, t in self.targets.items():
            meta["targets"][k] = {
                "typ": t.typ, "kind": t.kind, "prio": t.prio,
                "domain": t.domain, "static": t.static, "epoch": t.epoch,
                "unit": list(t.unit) if t.unit else None,
                "status": t.status, "changes": t.changes, "deaths": t.deaths,
                "narrows": t.narrows_this_scan,
                "expected": t.expected, "value": t.value, "note": t.note,
                "pin_addrs": [int(a) for a in t.pin_addrs],
            }
            if 0 < len(t.cands) <= CAP_PERSIST:
                cands[k] = (t.cands.tobytes(), t.suspects.tobytes())
        try:
            atomic_write(STATE_META, json.dumps(meta))
            atomic_write(STATE_CANDS, pickle.dumps(cands, protocol=4), mode="wb")
            self.dirty = False
            self.last_save = time.time()
            self.last_save_err = None
        except OSError as ex:
            if str(ex) != self.last_save_err:
                self.last_save_err = str(ex)
                log("state save failed: %s" % ex)

    def load_state(self):
        if not os.path.exists(STATE_META):
            return
        try:
            self.resume_meta = json.load(open(STATE_META, encoding="utf-8"))
        except (OSError, ValueError):
            self.resume_meta = None

    def apply_resume(self):
        meta, self.resume_meta = self.resume_meta, None
        if not meta:
            return
        self.learned = meta.get("learned") or {}
        same = (meta.get("pid") == self.pid
                and meta.get("modbase") == self.modbase
                and meta.get("ctime") == self.ctime
                and self.ctime is not None
                and self.quiescent_probe())
        cands = {}
        if same and os.path.exists(STATE_CANDS):
            try:
                cands = pickle.load(open(STATE_CANDS, "rb"))
            except Exception as ex:
                log("cands sidecar unreadable (%s) -- candidates dropped" % ex)
                cands = {}
        restored = 0
        for k, m in (meta.get("targets") or {}).items():
            if m["domain"] == "battle":
                continue           # battle targets always rebuild
            if not same and m["status"] not in (DERIVED, LOST):
                continue           # different session: only verdicts survive
            t = Target(k, dict(typ=m["typ"], value=m["value"], kind=m["kind"],
                               prio=m["prio"], domain=m["domain"],
                               static=m.get("static", False)))
            t.status = m["status"]
            t.changes = m.get("changes", 0)
            t.deaths = m.get("deaths", 0)
            t.narrows_this_scan = m.get("narrows", 0)
            t.expected = m.get("expected")
            t.note = m.get("note", "")
            t.pin_addrs = m.get("pin_addrs") or []
            t.last_seen_gen = 0    # prunable if its oracle never reappears
            if k in cands:
                try:
                    t.cands = array.array("Q")
                    t.cands.frombytes(cands[k][0])
                    t.suspects = array.array("Q")
                    t.suspects.frombytes(cands[k][1])
                except Exception:
                    t.cands = array.array("Q")
                    t.suspects = array.array("Q")
            # transient states don't round-trip: a SCANNING target has no scan
            # pass anymore, and NARROWING without its candidate set (too big to
            # persist / sidecar lost) would fake an instant collapse.
            if t.status == SCANNING or (t.status == NARROWING and not len(t.cands)):
                t.status = WAITING
                t.expected = None
                t.narrows_this_scan = 0
                t.note = "resume: requeued (transient state)"
            self.targets[k] = t
            restored += 1
        if same:
            self.battle_epoch = meta.get("battle_epoch", 0)
            self.block_turn = meta.get("block_turn")
            if meta.get("block_snap"):
                self.block_snap = bytes.fromhex(meta["block_snap"])
        log("resume: %s session -> restored %d target(s)%s"
            % ("SAME" if same else "new", restored,
               "" if same else " (verdicts only; addresses were stale)"))

    def quiescent_probe(self):
        """Resume trust check: does the gold canary read at all?"""
        try:
            self.mem.resolve(GOLD_PATH)
            return True
        except OSError:
            return False

    # -- main cycle --
    def cycle(self):
        if not self.connect():
            self.world = "no-game"
            self.write_status()
            time.sleep(2.0)
            return
        self.camp.poll()
        self.batt.poll()
        self.detect_reload()
        self.detect_world()
        self.check_canary()
        self.update_targets()
        self.block_watch()
        self.narrow_phase()
        self.scan_phase(time.time() + SCAN_SLICE)
        self.write_status()
        self.write_status_json()
        self.maybe_save()


def main():
    os.makedirs(PINS_DIR, exist_ok=True)
    log("=" * 70)
    log("aai_hunter starting (hunter pid %d). Scoreboard: _hunt_status.txt"
        % os.getpid())
    h = Hunter()
    h.load_state()
    try:
        while True:
            t0 = time.time()
            try:
                h.cycle()
            except Exception:
                log("cycle error:\n" + traceback.format_exc())
                time.sleep(2)
            time.sleep(max(0.2, INTERVAL - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        h.maybe_save(force=True)
        log("hunter stopped; state saved.")


if __name__ == "__main__":
    main()
