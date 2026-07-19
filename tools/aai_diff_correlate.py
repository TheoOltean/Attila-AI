#!/usr/bin/env python3
"""Differential-mapper correlator (the "gold hunt" analysis).

Reads the two logs the in-process mapper produces during a battle:
  data/aai_diff_oracle.log  : per-tick per-unit  "tick key bits men ammo event"
  data/aai_diff_struct.log  : delta-encoded      "tick key 0xoffset old new"
Joins on (tick,key) and labels each struct offset by which game event its
changes track -- rediscovering field meanings by OBSERVATION (live fields only).

Usage: python3 tools/aai_diff_correlate.py
"""
import struct, collections, os, sys

DATA = "/mnt/c/Program Files (x86)/Steam/steamapps/common/Total War Attila/data"
ORACLE = os.path.join(DATA, "aai_diff_oracle.log")
STRUCT = os.path.join(DATA, "aai_diff_struct.log")

BIT = {1:"routing",2:"shattered",4:"moving",8:"moving_fast",16:"melee",32:"idle",
       64:"under_fire",128:"fire_at_will",256:"cavalry",512:"artillery",1024:"infantry"}
KNOWN = {0x44:"men(known)",0x1cb4:"fatigue(known)",0x20cc:"ammo(known)",
         0x1f28:"rout-state(known)",0x1b5c:"order-state(known)",0x212c:"rout-ctr(known)"}

def as_float(u):
    return struct.unpack('<f', struct.pack('<I', u & 0xffffffff))[0]

def infer_type(vals):
    s = set(vals)
    if s <= {0,1}: return "flag"
    if all(v < 0x10000 for v in s): return "int"
    fs = [as_float(v) for v in s]
    if all(f==0 or 1e-4 < abs(f) < 1e7 for f in fs): return "float"
    return "int/ptr"

def main():
    if not os.path.exists(STRUCT):
        sys.exit("no struct log yet -- run a battle with the differential mapper first")
    # oracle: key -> {tick: (bits,men,ammo,ev)}
    oracle = collections.defaultdict(dict)
    for ln in open(ORACLE):
        p = ln.split()
        if len(p) < 5: continue
        oracle[int(p[1])][int(p[0])] = (int(p[2]),int(p[3]),int(p[4]), p[5] if len(p)>5 else "-")
    # transitions per key per bit (ticks where the bit flips)
    trans = collections.defaultdict(lambda: collections.defaultdict(set))
    for key, series in oracle.items():
        ts = sorted(series)
        for b in BIT:
            prev = None
            for t in ts:
                cur = 1 if (series[t][0] & b) else 0
                if prev is not None and cur != prev: trans[key][b].add(t)
                prev = cur
    # struct changes: offset -> [(key,tick,old,new)]
    changes = collections.defaultdict(list)
    for ln in open(STRUCT):
        p = ln.split()
        if len(p) < 5: continue
        changes[int(p[2],16)].append((int(p[1]),int(p[0]),int(p[3],16),int(p[4],16)))

    rows = []
    for off, evs in changes.items():
        keys = set(k for k,_,_,_ in evs)
        ctick = collections.defaultdict(set)
        for k,t,o,n in evs: ctick[k].add(t)
        typ = infer_type([n for _,_,_,n in evs])
        # coincidence LIFT = P(offset changes | bit transition) - P(offset changes | any tick).
        # A field that changes every tick has base~1 -> lift~0 (de-noised); a flag that ONLY
        # changes on a rout transition has base~0, hit~1 -> lift~1.
        best_bit, best = None, 0.0
        for b, name in BIT.items():
            coinc = tot = base_chg = base_tot = 0
            for k in keys:
                tr = trans[k][b]; tot += len(tr); coinc += len(tr & ctick[k])
                base_chg += len(ctick[k]); base_tot += len(oracle[k])
            if tot >= 2:
                lift = (coinc/tot) - (base_chg/base_tot if base_tot else 0)
                if lift > best: best, best_bit = lift, name
        # held-rate: change-rate while a bit is HELD set vs clear (continuous fields)
        best_held, best_held_bit = 0.0, None
        for b, name in BIT.items():
            set_ticks = clr_ticks = set_chg = clr_chg = 0
            for k in keys:
                s = oracle[k]
                for t, (bits,_,_,_) in s.items():
                    held = bits & b
                    if held: set_ticks += 1; set_chg += (t in ctick[k])
                    else:    clr_ticks += 1; clr_chg += (t in ctick[k])
            if set_ticks >= 5 and clr_ticks >= 5:
                sr = set_chg/set_ticks; cr = clr_chg/clr_ticks
                lift = sr - cr
                if lift > best_held: best_held, best_held_bit = lift, name
        # men-tracking (health mirror)
        mh = mt = 0
        for k,t,o,n in evs:
            c = oracle[k].get(t)
            if c: mt += 1; mh += (n == c[1])
        men = mh/mt if mt else 0
        rows.append(dict(off=off, type=typ, chgs=len(evs), keys=len(keys),
                         coinc_bit=best_bit, coinc=round(best,2),
                         held_bit=best_held_bit, held=round(best_held,2),
                         men=round(men,2), sample="%x->%x" % (evs[0][2], evs[0][3])))
    # rank: strongest signal first
    rows.sort(key=lambda r: -(r["coinc"] + r["held"] + r["men"]))
    print(f"# offsets changed: {len(rows)}   oracle-keys(units): {len(oracle)}")
    print(f"{'offset':>7} {'type':>6} {'chgs':>6} {'u':>3}  {'coincide':>10} {'lift':>4}  {'held-with':>10} {'lift':>4} {'men':>4}  sample   known")
    for r in rows:
        k = KNOWN.get(r["off"], "")
        if r["coinc"] < 0.25 and r["held"] < 0.2 and r["men"] < 0.6 and not k:
            continue  # skip weak/noise unless it's a known anchor
        print(f"0x{r['off']:04x} {r['type']:>6} {r['chgs']:>6} {r['keys']:>3}  "
              f"{str(r['coinc_bit']):>10} {r['coinc']:>4}  {str(r['held_bit']):>10} {r['held']:>4} {r['men']:>4}  "
              f"{r['sample']:>9}  {k}")

if __name__ == "__main__":
    main()
