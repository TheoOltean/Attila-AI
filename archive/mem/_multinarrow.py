"""Religion-% full-memory narrower, v3. Religion is a faction-wide weighted
average (drops when you conquer a different-faith settlement) -- may be CACHED
(pinnable) or COMPUTED live (no cell). This tells us which: if candidates
collapse to a stable address that keeps matching, it's cached; if they evaporate
to 0, it's derived.

Robust: TOLERANCE float match (feed rounds to 0.1) + candidate state PERSISTED to
_relstate.json each turn, so an exit/restart resumes instead of rescanning.
Run under Windows python.exe."""
import aai_mem as am, struct, json, time, sys, os
import aai_scan as sc

FEED = r"C:\Program Files (x86)\Steam\steamapps\common\Total War Attila\data\aai_campaign.json"
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_relstate.json")
TOL = 0.2
mem = am.Mem()
ticks = int(sys.argv[1]) if len(sys.argv) > 1 else 1200

def religion():
    try:
        d = json.load(open(FEED, encoding="utf-8", errors="replace"))
        hf = [f for f in d.get("factions", []) if f.get("human")]
        if hf:
            return d.get("turn"), round(hf[0].get("religion_pct") or 0, 3)
    except Exception:
        pass
    return None, None

def load():
    if os.path.exists(STATE):
        d = json.load(open(STATE))
        return d.get("addrs"), d.get("last"), d.get("seen")
    return None, None, None

def save(addrs, last, seen):
    json.dump({"addrs": addrs, "last": last, "seen": seen}, open(STATE, "w"))

def first_scan(target):
    out = set()
    for t in (target - 0.1, target, target + 0.1):
        a, _, _ = sc.scan_new(mem, "f32", round(t, 3), aligned=True)
        out.update(int(x) for x in a)
    return sorted(out)

def narrow(addrs, target):
    keep = []
    for a in addrs:
        try:
            v = struct.unpack("<f", mem.read(a, 4))[0]
        except OSError:
            continue
        if abs(v - target) <= TOL:
            keep.append(a)
    return keep

addrs, last, seen = load()
print("religion narrower v3 (persisted). resumed=%s candidates" %
      (len(addrs) if addrs is not None else "none"), flush=True)

for _ in range(ticks):
    turn, rv = religion()
    if turn is not None and turn != seen:
        if addrs is None:
            addrs = first_scan(rv); last = rv
            print("turn %s: religion=%s  first scan -> %d candidates" % (turn, rv, len(addrs)), flush=True)
        elif abs(rv - last) > 0.001:
            before = len(addrs); addrs = narrow(addrs, rv); last = rv
            print("turn %s: religion=%s  %d -> %d candidates" % (turn, rv, before, len(addrs)), flush=True)
            if 0 < len(addrs) <= 6:
                print("   >> CANDIDATES: %s" % ", ".join("0x%08X" % a for a in addrs), flush=True)
            elif len(addrs) == 0:
                print("   >> collapsed to 0 -> religion is COMPUTED (no stored cell)", flush=True)
        else:
            print("turn %s: religion=%s  (unchanged; %d candidates)" % (turn, rv, len(addrs)), flush=True)
        seen = turn
        save(addrs, last, seen)
    time.sleep(2)
print("done", flush=True)
