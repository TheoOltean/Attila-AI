"""ml/host/watch.py -- live fixed-format viewer for the obs feed.

Driver-level dev tool, read-only: renders global[40] from the raw wire
payload (no codec/numpy -- what you see is what the game wrote). Every
field owns a fixed row and column; values update in place, nothing ever
shifts (ANSI home + full-width repaint). A stale feed (pause, loading)
is shown as age, never an error -- last-good stays on screen.

Usage: py ml/host/watch.py [data_dir] [--once]
  data_dir  override the game data dir (default: auto-detect)
  --once    render one frame and exit (testing)
"""

from __future__ import annotations

import os
import sys
import time

import channel as channel_mod

REFRESH_S = 0.25
STALE_S = 3.0
WIDTH = 78
BAR_W = 24

# Display labels for global[40] (presentation only -- layout truth is
# spec + ML_DESIGN). harvested=False rows carry the '·' marker: expected
# zero until their capability lands; a nonzero there is news.
BATTLE_TYPES = ["unwalled settl", "walled settl", "field battle"]
WEATHER = ["clear", "rain", "snow", "dust"]   # engine enum; no fog
VP_PARTS = ["exists", "owner ours", "owner theirs", "owner neutral", "progress"]


def rows() -> list[tuple[int, str, bool]]:
    r = [
        (0, "time elapsed /limit", True),
        (1, "time remaining /limit", True),
        (2, "we are attacker", False),
    ]
    r += [(3 + i, f"battle type: {n}", False) for i, n in enumerate(BATTLE_TYPES)]
    r += [(6 + i, f"weather: {n}", False) for i, n in enumerate(WEATHER)]
    r += [(10, "weather severity /2", False)]
    r += [
        (11, "own(AI) men alive /init", True),
        (12, "enemy(player) men alive /init", True),
        (13, "own(AI) men initial /6400", True),
        (14, "enemy(player) men initial /6400", True),
        (15, "own(AI) units alive /40", True),
        (16, "enemy(player) units alive /40", True),
    ]
    for s in range(3):
        r += [(17 + s * 5 + j, f"vp{s + 1}: {n}", False) for j, n in enumerate(VP_PARTS)]
    return r


ROWS = rows()


def bar(v) -> str:
    if not isinstance(v, (int, float)):
        return " " * BAR_W
    n = int(round(min(1.0, max(0.0, float(v))) * BAR_W))
    return "#" * n + "." * (BAR_W - n)


def fmt(v) -> str:
    return f"{v:8.4f}" if isinstance(v, (int, float)) else "      --"


def render(obs, age) -> list[str]:
    lines = []
    bid = obs.get("battle_id", "-") if obs else "-"
    seq = obs.get("seq", "-") if obs else "-"
    tick = obs.get("tick", "-") if obs else "-"
    if age is None:
        feed = "no feed yet"
    elif age > STALE_S:
        feed = f"STALE {age:6.1f}s (pause/loading is normal)"
    else:
        feed = f"LIVE  {age:6.1f}s"
    lines.append(f" ml obs feed -- global[40]")
    lines.append(f" battle {bid:<18} seq {seq!s:>6}  tick {tick!s:>6}  {feed}")
    lines.append(" " + "-" * (WIDTH - 2))
    g = (obs or {}).get("global") or []
    for idx, label, harvested in ROWS:
        v = g[idx] if idx < len(g) else None
        mark = " " if harvested else "·"
        lines.append(f" {idx:>2} {mark} {label:<27} {fmt(v)}  |{bar(v)}|")
    spare = [g[i] if i < len(g) else None for i in range(32, 40)]
    nz = sum(1 for v in spare if isinstance(v, (int, float)) and v != 0)
    lines.append(f" 32-39 · spare {'(all zero)' if nz == 0 else f'NONZERO x{nz}':<38}")
    lines.append(" " + "-" * (WIDTH - 2))
    lines.append(" · = not harvested yet (expected zero)")
    return lines


def paint(lines: list[str]) -> None:
    body = "\n".join(line[:WIDTH].ljust(WIDTH) for line in lines)
    sys.stdout.write("\x1b[H" + body + "\n")
    sys.stdout.flush()


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--once"]
    once = "--once" in sys.argv[1:]
    ch = channel_mod.Channel(args[0] if args else None)
    os.system("")  # enable VT processing on legacy conhost
    if once:
        obs, age = ch.read_json("obs")
        print("\n".join(render(obs, age)))
        return
    sys.stdout.write("\x1b[2J\x1b[?25l")  # clear once, hide cursor
    try:
        while True:
            obs, age = ch.read_json("obs")
            paint(render(obs, age))
            time.sleep(REFRESH_S)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
