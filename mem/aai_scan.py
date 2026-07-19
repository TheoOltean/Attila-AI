"""aai_scan.py -- a command-line "cheat engine" for Total War: Attila.

Everything Cheat Engine's GUI does for us -- value scanning + pointer scanning --
done from the shell, keeping scan state in a file between invocations. Runs on
WINDOWS Python (imports aai_mem, which does the Win32 RPM/WPM). No GUI.

WORKFLOW (mirrors CE):
  # 1. find the address holding a known value (e.g. faction gold = 5000)
  python aai_scan.py new 5000 --type i32
  #    ...spend/earn gold in-game so it changes to, say, 4200...
  python aai_scan.py next 4200
  #    repeat until one address remains:
  python aai_scan.py list
  # 2. read / write it live to confirm it's the real one
  python aai_scan.py read  0x1A2B3C4
  python aai_scan.py write 0x1A2B3C4 99999
  # 3. find a RESTART-STABLE pointer path to it, ready for aai_mem.TARGETS
  python aai_scan.py ptrscan 0x1A2B3C4 --depth 4 --max-offset 0x800

Scan state persists in aai_scan_state.pkl next to this file, so `new` then `next`
across separate shell calls works. `new`/`reset` clears it.

Honest scope vs Cheat Engine: value scanning is on par. The pointer scanner is a
bounded reverse-BFS -- fine for finding A stable path to one value, but it lacks
CE's advanced filters and gets slow/greedy at high depth. Keep --depth <= 5 and
--max-offset <= 0x1000, and validate a found path across a game restart with
`verify`.
"""

import argparse
import array
import bisect
import json
import os
import pickle
import struct
import sys
import time

import aai_mem as am

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aai_scan_state.pkl")
# where the Lua mod writes its live JSON feeds (the "answer key" for autonarrow)
FEED_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\Total War Attila\data"

# ---- memory-region enumeration (VirtualQueryEx) ----------------------------
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}   # RO, RW, WC, XR, XRW, XWC
WRITABLE = {0x04, 0x08, 0x40, 0x80}               # RW, WC, XRW, XWC
MAX_ADDR_32 = 0x100000000                          # 32-bit target: below 4 GB

C, W = am.C, am.W


class MBI(C.Structure):
    # 64-bit MEMORY_BASIC_INFORMATION (our host Python is 64-bit)
    _fields_ = [("BaseAddress", C.c_void_p), ("AllocationBase", C.c_void_p),
                ("AllocationProtect", W.DWORD), ("__a1", W.DWORD),
                ("RegionSize", C.c_size_t), ("State", W.DWORD),
                ("Protect", W.DWORD), ("Type", W.DWORD), ("__a2", W.DWORD)]


am.k32.VirtualQueryEx.argtypes = [W.HANDLE, W.LPCVOID, C.POINTER(MBI), C.c_size_t]
am.k32.VirtualQueryEx.restype = C.c_size_t


def regions(mem, want_writable=False):
    """Yield (base, size, protect) for committed, readable regions of target."""
    addr = 0
    mbi = MBI()
    while addr < MAX_ADDR_32:
        if not am.k32.VirtualQueryEx(mem.h, W.LPCVOID(addr), C.byref(mbi), C.sizeof(mbi)):
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize
        prot = mbi.Protect
        nxt = base + size
        if (mbi.State == MEM_COMMIT and prot in READABLE
                and not (prot & PAGE_GUARD) and prot != PAGE_NOACCESS):
            if not want_writable or prot in WRITABLE:
                yield base, size, prot
        if nxt <= addr:
            break
        addr = nxt


# ---- value scanning --------------------------------------------------------
FMT = {"i32": "<i", "u32": "<I", "float": "<f", "f32": "<f"}


def _pack(typ, value):
    if typ in ("float", "f32"):
        return struct.pack("<f", float(value))
    return struct.pack("<i" if typ == "i32" else "<I", int(value, 0) if isinstance(value, str) else int(value))


def _unpack(typ, raw):
    return struct.unpack(FMT[typ], raw)[0]


def save_state(st):
    with open(STATE, "wb") as f:
        pickle.dump(st, f, protocol=4)


def load_state():
    if not os.path.exists(STATE):
        return None
    with open(STATE, "rb") as f:
        return pickle.load(f)


def scan_new(mem, typ, value, aligned=True):
    """First scan: return (addrs, vals, scanned_bytes) for every match of value."""
    needle = _pack(typ, value)
    addrs = array.array("Q")
    vals = array.array("I")  # raw 4-byte LE as u32 (works for i32/u32/float bits)
    scanned = 0
    for base, size, _ in regions(mem):
        off = 0
        while off < size:
            n = min(4 << 20, size - off)  # 4 MB chunk
            try:
                buf = mem.read(base + off, n)
            except OSError:
                break
            scanned += len(buf)
            i = buf.find(needle)
            while i != -1:
                a = base + off + i
                if not aligned or a % 4 == 0:
                    addrs.append(a)
                    vals.append(struct.unpack("<I", buf[i:i+4])[0])
                i = buf.find(needle, i + 1)
            if len(buf) < n:
                break
            # Advance. Keep a 3-byte overlap ONLY for unaligned scans (so a match
            # can't be missed straddling a chunk boundary), and ONLY while it
            # still moves forward: a <=3-byte tail with overlap gives step<=0,
            # which infinite-looped the first scan (off += len(buf)-3 == 0).
            step = len(buf) - (3 if not aligned else 0)
            if step <= 0:
                break
            off += step
    return addrs, vals, scanned


def scan_next(mem, st, needle_u32=None, mode=None):
    """Filter the candidate set. needle_u32 = exact match; else mode in
    changed/unchanged/increased/decreased. Returns (addrs, vals)."""
    typ = st["type"]
    keep_a = array.array("Q")
    keep_v = array.array("I")
    for a, old in zip(st["addrs"], st["vals"]):
        try:
            raw = mem.read(a, 4)
        except OSError:
            continue
        cur = struct.unpack("<I", raw)[0]
        if needle_u32 is not None:
            ok = cur == needle_u32
        elif mode == "changed":
            ok = cur != old
        elif mode == "unchanged":
            ok = cur == old
        elif mode == "increased":
            ok = _unpack(typ, raw) > _unpack(typ, struct.pack("<I", old))
        elif mode == "decreased":
            ok = _unpack(typ, raw) < _unpack(typ, struct.pack("<I", old))
        else:
            raise SystemExit("give a value or one of --changed/--unchanged/--increased/--decreased")
        if ok:
            keep_a.append(a); keep_v.append(cur)
    return keep_a, keep_v


def cmd_new(mem, args):
    t = time.time()
    addrs, vals, scanned = scan_new(mem, args.type, args.value, not args.unaligned)
    save_state({"type": args.type, "addrs": addrs, "vals": vals})
    print("first scan: value=%s type=%s -> %d matches  (%.0f MB in %.1fs)" % (
        args.value, args.type, len(addrs), scanned / 1e6, time.time() - t))
    if len(addrs) <= 20:
        _print_list(mem, {"type": args.type, "addrs": addrs, "vals": vals})


def cmd_next(mem, args):
    st = load_state()
    if not st:
        sys.exit("no scan in progress -- run `new` first")
    if args.value is not None:
        needle = struct.unpack("<I", _pack(st["type"], args.value))[0]
        ka, kv = scan_next(mem, st, needle_u32=needle)
    else:
        mode = next((m for m in ("changed", "unchanged", "increased", "decreased")
                     if getattr(args, m)), None)
        ka, kv = scan_next(mem, st, mode=mode)
    st["addrs"], st["vals"] = ka, kv
    save_state(st)
    print("next scan -> %d matches remain" % len(ka))
    if len(ka) <= 20:
        _print_list(mem, st)


def _print_list(mem, st, limit=40):
    typ = st["type"]
    for j, a in enumerate(st["addrs"][:limit]):
        try:
            v = _unpack(typ, mem.read(a, 4))
        except OSError:
            v = "?"
        base = _module_of(mem, a)
        print("  0x%08X = %s%s" % (a, v, ("  [%s]" % base) if base else ""))
    if len(st["addrs"]) > limit:
        print("  ... %d more" % (len(st["addrs"]) - limit))


def _module_of(mem, addr):
    b, s = mem.module(am.ENGINE_MODULE)
    if b <= addr < b + s:
        return "%s+0x%X" % (am.ENGINE_MODULE, addr - b)
    return None


def cmd_list(mem, args):
    st = load_state()
    if not st:
        sys.exit("no scan in progress")
    print("%d candidates (type=%s):" % (len(st["addrs"]), st["type"]))
    _print_list(mem, st, args.limit)


def cmd_read(mem, args):
    typ = args.type
    a = int(args.addr, 0)
    print("0x%08X = %s (%s)" % (a, _unpack(typ, mem.read(a, 4)), typ))


def cmd_write(mem, args):
    a = int(args.addr, 0)
    before = _unpack(args.type, mem.read(a, 4))
    mem.write(a, _pack(args.type, args.value))
    after = _unpack(args.type, mem.read(a, 4))
    print("0x%08X: %s -> %s (%s)" % (a, before, after, args.type))


# ---- pointer scanning (bounded reverse-BFS) --------------------------------
def cmd_ptrscan(mem, args):
    """Memory-frugal reverse-BFS pointer scan. No full index (that OOM'd at
    2.2 GB); instead one linear pass over writable memory PER DEPTH LEVEL, using
    a fixed 128 MB bitmap for O(1) "is this value in a target's window" tests, so
    RAM stays flat regardless of the game's committed size."""
    target = int(args.addr, 0)
    modbase, modsize = mem.module(args.module)
    maxoff = int(args.max_offset, 0) if isinstance(args.max_offset, str) else args.max_offset
    maxdepth = args.depth

    wregions = [(b, s) for b, s, _ in regions(mem, want_writable=True)]
    totmb = sum(s for _, s in wregions) / 1e6
    print("ptrscan -> 0x%08X  module %s @0x%08X  depth<=%d  off<=0x%X  writable=%.0f MB"
          % (target, args.module, modbase, maxdepth, maxoff, totmb))

    BM_BYTES = (MAX_ADDR_32 >> 2) >> 3           # 1 bit per 4-aligned addr = 128 MB
    paths = []
    level = {target: []}                          # {addr: reverse-forward offset suffix}
    seen = {target}                               # dedupe addresses across all levels

    for depth in range(maxdepth):
        if not level or len(paths) >= args.max_paths:
            break
        targs = sorted(level)
        bm = bytearray(BM_BYTES)                   # fresh zeroed bitmap each level
        for t in targs:                            # mark 4-aligned v in [t-maxoff, t]
            v = (t - maxoff) if t > maxoff else 0
            v = (v + 3) & ~3
            while v <= t:
                idx = v >> 2
                bm[idx >> 3] |= 1 << (idx & 7)
                v += 4
        found = {}
        hits = 0
        t0 = time.time()
        for base, size in wregions:
            off = 0
            while off < size:
                n = min(4 << 20, size - off)
                try:
                    buf = mem.read(base + off, n)
                except OSError:
                    break
                m = len(buf) & ~3
                words = struct.unpack("<%dI" % (m >> 2), buf[:m])
                b0 = base + off
                for k, v in enumerate(words):
                    if v & 3:                      # only 4-aligned pointer values
                        continue
                    idx = v >> 2
                    if bm[idx >> 3] >> (idx & 7) & 1:
                        ti = bisect.bisect_left(targs, v)
                        if ti < len(targs) and targs[ti] <= v + maxoff:
                            hits += 1
                            t = targs[ti]
                            L = b0 + (k << 2)
                            new_offs = level[t] + [t - v]
                            if modbase <= L < modbase + modsize:      # static root
                                paths.append([L - modbase] + list(reversed(new_offs)))
                                if len(paths) >= args.max_paths:
                                    break
                            elif L not in seen and len(found) < args.max_nodes:
                                seen.add(L); found[L] = new_offs
                if len(buf) < n or len(paths) >= args.max_paths:
                    break
                off += len(buf)
            if len(paths) >= args.max_paths:
                break
        print("  depth %d: %d targets -> %d hits, %d next nodes (%.1fs)"
              % (depth + 1, len(targs), hits, len(found), time.time() - t0))
        level = found

    if not paths:
        print("no static path found. Try --depth %d or --max-offset 0x%X."
              % (maxdepth + 1, maxoff * 2))
        return
    paths.sort(key=len)
    print("found %d path(s) (shallowest first):" % len(paths))
    for p in paths[:args.max_paths]:
        try:
            got = mem.resolve(p, args.module)
            ok = "OK" if got == target else "->0x%08X?" % got
        except OSError:
            ok = "unresolvable-now"
        print("  %s+0x%X  offsets=[%s]  %s"
              % (args.module, p[0], ", ".join("0x%X" % o for o in p), ok))
    print("\nPaste into aai_mem.TARGETS, e.g.:")
    print('  "gold": (%r, "i32"),' % [hex(o) for o in paths[0]])


def cmd_verify(mem, args):
    offs = [int(x, 0) for x in args.offsets]
    addr = mem.resolve(offs, args.module)
    print("resolve %s %s -> 0x%08X = %s" % (
        args.module, offs, addr, _unpack(args.type, mem.read(addr, 4))))


def cmd_reset(mem, args):
    if os.path.exists(STATE):
        os.remove(STATE)
    print("scan state cleared")


# ---- autonarrow: use the Lua feed as an oracle to scan hands-free ----------
def _read_oracle(which, unit, field):
    """Return (value:int|None, label) from the live JSON feed."""
    path = os.path.join(FEED_DIR, "aai_campaign.json" if which == "campaign" else "aai_battle.json")
    try:
        doc = json.load(open(path, "r", encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None, "feed unreadable"
    if which == "campaign":
        cf = field if (field and field != "men") else "gold"  # campaign default = gold
        for f in doc.get("factions", []):
            if f.get("human") and f.get(cf) is not None:
                return int(f[cf]), "turn %s %s %s" % (doc.get("turn"), f.get("name"), cf)
        return None, "no human faction %s in feed" % cf
    # battle: recursively collect unit dicts carrying `field`
    cands = []
    def walk(o):
        if isinstance(o, dict):
            if o.get(field) is not None and (unit is None or str(o.get("name")) == unit):
                cands.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(doc)
    if not cands:
        return None, "no unit with %s%s" % (field, "" if unit is None else " name=%s" % unit)
    c = max(cands, key=lambda d: d.get(field) or 0)  # biggest = a unit that will change
    return int(c[field]), "unit %s %s=%s" % (c.get("name"), field, c.get(field))


def cmd_autonarrow(mem, args):
    typ = args.type
    aligned = not args.unaligned
    ora = (args.field if (args.field and args.field != "men") else "gold") if args.feed == "campaign" else args.field
    print("autonarrow: watching %s feed (oracle=%s). Change %s; auto-narrows on each change."
          % (args.feed, ora,
             "the tax rate / end turns" if args.feed == "campaign" else "the battle"))
    print("  stop conditions: <= %d candidates, or %d observations. Ctrl-C to stop early.\n"
          % (args.until, args.max_obs))
    last_scanned = None      # oracle value at the most recent scan
    stable_val = None
    stable_count = 0
    obs = 0
    while obs < args.max_obs:
        val, label = _read_oracle(args.feed, args.unit, args.field)
        if val is None:
            time.sleep(args.interval); continue
        # settle: require the value to hold for --settle polls (kills mid-melee skew)
        if val == stable_val:
            stable_count += 1
        else:
            stable_val, stable_count = val, 1
        if stable_count >= args.settle:
            if last_scanned is None:
                _, _, sc = None, None, 0
                addrs, vals, sc = scan_new(mem, typ, val, aligned)
                save_state({"type": typ, "addrs": addrs, "vals": vals})
                obs += 1; last_scanned = val
                print("[obs %d] %s=%d -> first scan: %d candidates (%.0f MB)"
                      % (obs, label, val, len(addrs), sc / 1e6))
            elif val != last_scanned:
                st = load_state()
                needle = struct.unpack("<I", _pack(typ, val))[0]
                ka, kv = scan_next(mem, st, needle_u32=needle)
                st["addrs"], st["vals"] = ka, kv; save_state(st)
                obs += 1; last_scanned = val
                print("[obs %d] %s=%d -> narrowed to %d candidates" % (obs, label, val, len(ka)))
                if len(ka) <= args.until:
                    print("\ntarget reached (<= %d candidates)." % args.until); break
        time.sleep(args.interval)
    st = load_state()
    if st:
        print("\nfinal candidates:"); _print_list(mem, st)
        if 0 < len(st["addrs"]) <= 8:
            print("\nnext: pointer-scan one to get a stable path, e.g.")
            print("  python aai_scan.py ptrscan 0x%08X" % st["addrs"][0])


# ---- CLI -------------------------------------------------------------------
def _entity_from_feed(which, unit):
    """Return one entity dict from the live Lua feed (human faction, or a battle
    unit by name / the biggest by men)."""
    path = os.path.join(FEED_DIR, "aai_campaign.json" if which == "campaign" else "aai_battle.json")
    doc = json.load(open(path, "r", encoding="utf-8", errors="replace"))
    if which == "campaign":
        for f in doc.get("factions", []):
            if f.get("human"):
                return f
        return None
    cands = []
    def walk(o):
        if isinstance(o, dict):
            if o.get("men") is not None:
                cands.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(doc)
    if unit is not None:
        cands = [c for c in cands if str(c.get("name")) == unit]
    return max(cands, key=lambda d: d.get("men") or 0) if cands else None


def _floats_from(ent):
    """Pull labeled reference floats (position components, bearing) from an entity
    dict, tolerating pos as {x,y,z} or [x,y,z]."""
    refs = {}
    pos = ent.get("pos")
    if isinstance(pos, dict):
        for a in ("x", "y", "z"):
            if isinstance(pos.get(a), (int, float)):
                refs["pos." + a] = float(pos[a])
    elif isinstance(pos, (list, tuple)):
        for a, val in zip(("x", "y", "z"), pos):
            if isinstance(val, (int, float)):
                refs["pos." + a] = float(val)
    if isinstance(ent.get("bearing"), (int, float)):
        refs["bearing"] = float(ent["bearing"])
    return refs


def cmd_structmap(mem, args):
    """Locate an object's struct by fingerprinting its KNOWN Lua-feed values, then
    dump + auto-label the block so the unlabeled neighbors (morale/fatigue/...)
    stand out. Strategy: ONE full scan on the most distinctive field (the anchor),
    then confirm the other fields by reading a window around each candidate -- no
    full scans for small/common values."""
    ent = _entity_from_feed(args.feed, args.unit)
    if not ent:
        sys.exit("no entity found in the %s feed (is the game in that context?)" % args.feed)
    print("entity: " + ", ".join("%s=%s" % (k, ent.get(k)) for k in
          ("name", "type", "men", "men0", "ammo", "ammo0", "gold", "tax", "allies")
          if ent.get(k) is not None))

    if args.feed == "battle":
        fields = [(k, ent[k]) for k in ("men", "men0", "ammo", "ammo0")
                  if isinstance(ent.get(k), int) and ent[k] > 0]
    else:
        fields = [(k, ent[k]) for k in ("gold", "tax", "allies")
                  if isinstance(ent.get(k), int)]
    floatrefs = _floats_from(ent)
    if not fields:
        sys.exit("no integer fingerprint fields on this entity")

    # anchor on the largest value (most distinctive -> fewest matches)
    fields.sort(key=lambda kv: -abs(kv[1]))
    anchor_name, anchor_val = fields[0]
    others = fields[1:]
    print("anchor: %s=%d  (scanning)..." % (anchor_name, anchor_val))
    addrs, _, _ = scan_new(mem, "i32", anchor_val, aligned=True)
    print("  %d anchor matches; confirming other fields in a +/-0x%X window" % (len(addrs), args.window))

    W = args.window
    best = None
    for a in addrs:
        try:
            buf = mem.read(a - W, 2 * W)
        except OSError:
            continue
        found = [(anchor_name, a)]
        for nm, vl in others:
            needle = struct.pack("<i", vl)
            idx = buf.find(needle)
            while idx != -1:
                addr = (a - W) + idx
                if addr % 4 == 0 and addr != a:
                    found.append((nm, addr)); break
                idx = buf.find(needle, idx + 1)
        if best is None or len(found) > len(best):
            best = found
        if len(found) == len(fields):
            break
    if not best or len(best) < 2:
        sys.exit("could not co-locate >=2 fields (try --window bigger, or a different --unit)")

    base = min(addr for _, addr in best)
    labels = {addr - base: nm for nm, addr in best}
    print("\nSTRUCT base=0x%08X  (%d/%d fingerprint fields co-located):" % (base, len(best), len(fields)))
    for nm, addr in sorted(best, key=lambda x: x[1]):
        print("  +0x%04X  %s" % (addr - base, nm))

    print("\nDUMP [base-0x20 .. base+0x%X]   offset:  u32        i32          f32" % W)
    for off in range(-0x20, W, 4):
        try:
            raw = mem.read(base + off, 4)
        except OSError:
            continue
        u = struct.unpack("<I", raw)[0]; i = struct.unpack("<i", raw)[0]; f = struct.unpack("<f", raw)[0]
        lab = labels.get(off, "")
        if not lab:
            for fn, fv in floatrefs.items():
                if abs(f - fv) <= max(0.5, abs(fv) * 0.002):
                    lab = fn + "?"; break
        if not lab:
            if 0 < i <= 100:
                lab = "<candidate int 0..100 (morale/exp/fatigue?)"
            elif 0.0 < f < 1.0:
                lab = "<candidate unary 0..1 (fatigue/ammo-frac?)"
        print("  +0x%04X  %08X  %11d  %13.3f  %s" % (off, u, i, f, lab))
    print("\nnext: lock a durable path ->  python aai_scan.py ptrscan 0x%08X" % base)


def main():
    ap = argparse.ArgumentParser(description="CLI cheat-table for Attila (empire.retail.dll)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="first scan for a value (clears prior state)")
    p.add_argument("value"); p.add_argument("--type", default="i32", choices=FMT)
    p.add_argument("--unaligned", action="store_true"); p.set_defaults(fn=cmd_new)

    p = sub.add_parser("next", help="narrow candidates")
    p.add_argument("value", nargs="?");
    p.add_argument("--changed", action="store_true"); p.add_argument("--unchanged", action="store_true")
    p.add_argument("--increased", action="store_true"); p.add_argument("--decreased", action="store_true")
    p.set_defaults(fn=cmd_next)

    p = sub.add_parser("list", help="show current candidates")
    p.add_argument("--limit", type=int, default=40); p.set_defaults(fn=cmd_list)

    p = sub.add_parser("read", help="read one address")
    p.add_argument("addr"); p.add_argument("--type", default="i32", choices=FMT); p.set_defaults(fn=cmd_read)

    p = sub.add_parser("write", help="write one address")
    p.add_argument("addr"); p.add_argument("value")
    p.add_argument("--type", default="i32", choices=FMT); p.set_defaults(fn=cmd_write)

    p = sub.add_parser("ptrscan", help="find restart-stable pointer path(s) to an address")
    p.add_argument("addr"); p.add_argument("--module", default=am.ENGINE_MODULE)
    p.add_argument("--depth", type=int, default=4); p.add_argument("--max-offset", default="0x800")
    p.add_argument("--max-paths", type=int, default=20)
    p.add_argument("--max-nodes", type=int, default=200_000, help="per-level next-node cap (memory guard)")
    p.set_defaults(fn=cmd_ptrscan)

    p = sub.add_parser("verify", help="resolve+read a pointer path (cross-check after restart)")
    p.add_argument("offsets", nargs="+"); p.add_argument("--module", default=am.ENGINE_MODULE)
    p.add_argument("--type", default="i32", choices=FMT); p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("autonarrow", help="hands-free: watch the Lua feed and narrow on each change")
    p.add_argument("--feed", default="campaign", choices=["campaign", "battle"])
    p.add_argument("--type", default="i32", choices=FMT)
    p.add_argument("--unit", default=None, help="battle: unit name to track (default: biggest)")
    p.add_argument("--field", default="men", help="battle oracle field (men/ammo)")
    p.add_argument("--until", type=int, default=4, help="stop when <= this many candidates")
    p.add_argument("--max-obs", type=int, default=10, help="stop after this many observations")
    p.add_argument("--settle", type=int, default=1, help="polls the value must hold before scanning (battle: 2-3)")
    p.add_argument("--interval", type=float, default=1.0, help="feed poll seconds")
    p.add_argument("--unaligned", action="store_true")
    p.set_defaults(fn=cmd_autonarrow)

    p = sub.add_parser("structmap", help="fingerprint a struct from the Lua feed, dump+label its fields")
    p.add_argument("--feed", default="battle", choices=["campaign", "battle"])
    p.add_argument("--unit", default=None, help="battle: unit name (default: biggest by men)")
    p.add_argument("--window", type=lambda s: int(s, 0), default=0x300, help="bytes after base to dump")
    p.set_defaults(fn=cmd_structmap)

    sub.add_parser("reset", help="clear scan state").set_defaults(fn=cmd_reset)

    args = ap.parse_args()
    with am.Mem() as mem:
        args.fn(mem, args)


if __name__ == "__main__":
    main()
