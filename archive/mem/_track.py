"""Comprehensive per-turn tracker for campaign field discovery.
Logs the faction block (int AND float view) + all feed oracles, one row per turn.
After several turns, _analyze.py correlates each offset's trajectory with each
oracle's trajectory to auto-identify fields. Run under Windows python.exe."""
import aai_mem as am, struct, json, time, sys, os

FEED = r"C:\Program Files (x86)\Steam\steamapps\common\Total War Attila\data\aai_campaign.json"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_track.jsonl")
mem = am.Mem()
seen = set()
ticks = int(sys.argv[1]) if len(sys.argv) > 1 else 900

def feed():
    try:
        d = json.load(open(FEED, encoding="utf-8", errors="replace"))
        hf = [f for f in d.get("factions", []) if f.get("human")]
        if not hf: return None
        f = hf[0]
        return {"turn": d.get("turn"), "gold": f.get("gold"),
                "religion_pct": f.get("religion_pct"), "allies": f.get("allies"),
                "at_war": 1 if f.get("at_war") else 0,
                "num_forces": len(f.get("forces") or []),
                "num_settlements": len(f.get("settlements") or []),
                "total_units": sum((x.get("units") or 0) for x in (f.get("forces") or []))}
    except Exception:
        return None

with open(OUT, "w") as fout:
    print("comprehensive tracker -> %s" % OUT, flush=True)
    for _ in range(ticks):
        fd = feed()
        if fd and fd["turn"] is not None and fd["turn"] not in seen:
            gold = mem.resolve([0x21EFA14, 0x0, 0xDC], "empire.retail.dll")
            base = gold - 0xDC
            ints, floats = {}, {}
            for off in range(-0x40, 0x180, 4):
                try:
                    raw = mem.read(base + off, 4)
                    ints[str(off)] = struct.unpack("<i", raw)[0]
                    floats[str(off)] = round(struct.unpack("<f", raw)[0], 3)
                except OSError:
                    pass
            fout.write(json.dumps({"feed": fd, "ints": ints, "floats": floats}) + "\n")
            fout.flush()
            seen.add(fd["turn"])
            print("  turn %s  gold=%s forces=%s units=%s rel=%s" %
                  (fd["turn"], fd["gold"], fd["num_forces"], fd["total_units"], fd["religion_pct"]), flush=True)
        time.sleep(2)
print("done (%d turns)" % len(seen), flush=True)
