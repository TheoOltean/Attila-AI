"""External AI stub — the first process to play Total War from outside.

Runs on WINDOWS python (the game's files are hot in its page cache).
Reads the battle state snapshot streamed by battle/telemetry.lua,
computes an order (march the enemy army onto the player's centroid),
and atomically writes it to data/aai_cmd.txt where battle/mailbox.lua
picks it up.

Run:  python tools/ai_stub.py            (from anywhere; paths are absolute)
Stop: Ctrl+C, or it idles harmlessly when no battle is running.
"""

import os
import re
import time

GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Total War Attila"
STATE = os.path.join(GAME, "data", "attila_ai_battle_state.txt")
CMD = os.path.join(GAME, "data", "aai_cmd.txt")

DECIDE_EVERY = 5.0          # seconds between orders
POLL = 0.25                 # state poll interval

UNIT_RE = re.compile(r"x=\s*(-?[\d.]+) y=\s*(-?[\d.]+) z=\s*(-?[\d.]+) brg=")
PHASE_RE = re.compile(r"phase=(\w+)")


def read_state():
    """Return (phase, [(x, z), ...player unit positions]) or (None, [])."""
    try:
        with open(STATE, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None, []
    phase = None
    m = PHASE_RE.search(text)
    if m:
        phase = m.group(1)
    units = [(float(x), float(z)) for x, _y, z in UNIT_RE.findall(text)
             if "CAMERA" not in text[:0]]  # camera lines have no brg=, regex skips them
    return phase, units


def write_cmd(seq, lines):
    tmp = CMD + ".tmp"
    body = "SEQ %d\n%s\nEND %d\n" % (seq, "\n".join(lines), seq)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    os.replace(tmp, CMD)  # atomic on NTFS


def main():
    print("AI stub up. Watching", STATE)
    seq = int(time.time()) % 100000  # fresh seq space per run
    last_decide = 0.0
    last_phase = None
    while True:
        phase, units = read_state()
        if phase != last_phase:
            print("phase:", phase, "| player units visible:", len(units))
            last_phase = phase
        fresh = os.path.exists(STATE) and (time.time() - os.path.getmtime(STATE) < 3)
        if phase in ("deployment", "conflict", "loading") and units and fresh:
            if time.time() - last_decide >= DECIDE_EVERY:
                cx = sum(u[0] for u in units) / len(units)
                cz = sum(u[1] for u in units) / len(units)
                seq += 1
                write_cmd(seq, ["RALLY %.1f %.1f 0" % (cx, cz)])
                print("seq %d: RALLY enemy onto player centroid (%.0f, %.0f)"
                      % (seq, cx, cz))
                last_decide = time.time()
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("bye")
