"""External AI process, v1 stub — proof of the game<->process IO loop.

Spawned by src/battle/ai_link.lua at battle load (runs on Windows python,
cwd = game root). Perception: parses data/attila_ai_battle_state.txt
(written by battle telemetry at 4 Hz). Action: writes order batches to
data/aai_orders.txt, which ai_link applies through unit controllers.

Behaviour: every DECIDE_S seconds, order every enemy squad to march to a
grid formation centred on the player army's centroid. Kill this process
and the enemy stops reacting -- that is the point of the demo.
"""

import os
import re
import sys
import time

DECIDE_S = 5.0
POLL_S = 1.0
STALE_EXIT_S = 20.0
GRID_COLS = 5
GRID_SPACING = 25.0


def find_game_dir():
    cwd = os.getcwd()
    if os.path.isfile(os.path.join(cwd, "data", "attila_ai_log.txt")):
        return cwd
    return r"C:\Program Files (x86)\Steam\steamapps\common\Total War Attila"


GAME = find_game_dir()
DATA = os.path.join(GAME, "data")
STATE_PATH = os.path.join(DATA, "attila_ai_battle_state.txt")
ORDERS_PATH = os.path.join(DATA, "aai_orders.txt")
LINK_PATH = os.path.join(DATA, "aai_link.txt")
LOG_PATH = os.path.join(DATA, "aai_stub_log.txt")

UNIT_RE = re.compile(r"^\s*\d+\s+.*x=\s*([-\d.]+)\s+y=\s*[-\d.?]+\s+z=\s*([-\d.]+)")
PHASE_RE = re.compile(r"phase=(\w+)")


def log(msg):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(time.strftime("[%H:%M:%S] ") + msg + "\n")


def read_link():
    try:
        with open(LINK_PATH, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None, 0
    m = re.search(r"squads (\d+)", text)
    return text.splitlines()[0] if text else None, int(m.group(1)) if m else 0


def read_state():
    """Returns (phase, [(x, z), ...player unit positions]) or (None, [])."""
    try:
        with open(STATE_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None, []
    phase = None
    units = []
    for line in lines:
        pm = PHASE_RE.search(line)
        if pm:
            phase = pm.group(1)
        um = UNIT_RE.match(line)
        if um:
            units.append((float(um.group(1)), float(um.group(2))))
    return phase, units


def write_orders(seq, orders):
    tmp = ORDERS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("seq %d\n" % seq)
        for line in orders:
            f.write(line + "\n")
    os.replace(tmp, ORDERS_PATH)


def grid_targets(cx, cz, n):
    """Formation slots centred on (cx, cz)."""
    targets = []
    rows = (n + GRID_COLS - 1) // GRID_COLS
    for i in range(n):
        col = i % GRID_COLS
        row = i // GRID_COLS
        x = cx + (col - (GRID_COLS - 1) / 2.0) * GRID_SPACING
        z = cz + (row - (rows - 1) / 2.0) * GRID_SPACING
        targets.append((x, z))
    return targets


def main():
    log("stub up: pid=%d game=%s" % (os.getpid(), GAME))
    status, squads = read_link()
    if status != "battle_start" or squads < 1:
        log("no live battle handshake (status=%r squads=%d) - exiting" % (status, squads))
        return
    log("handshake: %d enemy squads controllable" % squads)

    seq = 0
    last_decide = 0.0
    while True:
        time.sleep(POLL_S)

        status, _ = read_link()
        if status == "battle_over":
            log("battle over - exiting")
            return
        try:
            stale = time.time() - os.path.getmtime(STATE_PATH)
        except OSError:
            stale = 1e9
        if stale > STALE_EXIT_S:
            log("state file stale %.0fs - exiting" % stale)
            return

        phase, units = read_state()
        if phase != "conflict" or not units:
            continue

        now = time.time()
        if now - last_decide < DECIDE_S:
            continue
        last_decide = now

        cx = sum(u[0] for u in units) / len(units)
        cz = sum(u[1] for u in units) / len(units)
        seq += 1
        orders = ["move %d %.1f %.1f run" % (i + 1, x, z)
                  for i, (x, z) in enumerate(grid_targets(cx, cz, squads))]
        write_orders(seq, orders)
        log("seq %d: %d squads -> player centroid (%.0f, %.0f)" % (seq, squads, cx, cz))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        log("CRASH:\n" + traceback.format_exc())
        sys.exit(1)
