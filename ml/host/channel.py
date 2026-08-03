"""ml/host/channel.py -- file transport (host side).

The host half of the IPC contract (game half: ml/game/ipc/channel.lua):

- Reads game-written JSON (hello/obs/ack). Game-side Lua CANNOT replace
  a file atomically on Windows (tmp -> remove -> rename has a no-file
  window), so the reader contract is LAST-GOOD: on ENOENT *or* a
  truncated/mid-write parse failure, serve the cached copy with its old
  mtime-age. (v1's cockpit returned None on ENOENT and blanked the UI --
  that bug is fixed here by contract.)
- Writes the act file ATOMICALLY: tmp + os.replace, explicit utf-8.
  The game side may therefore parse any act file it sees as complete.
- Staleness is mtime-based (the game freezes on pause -- a stale obs is
  normal, never an error); freshness of CONTENT is the seq field.
- Seqs are small monotonic ints (game Lua numbers are float32); the act
  seq counter reseeds from the act file left on disk across restarts.
"""

from __future__ import annotations

import os

GAME_DATA = r"C:\Program Files (x86)\Steam\steamapps\common\Total War Attila\data"

PATH = {
    "hello": "aai_ml_hello.json",  # game -> host, once per battle
    "obs": "aai_ml_obs.json",      # game -> host, per decision tick
    "ack": "aai_ml_ack.json",      # game -> host, apply-results ring
    "act": "aai_ml_act.txt",       # host -> game, atomic
}


def find_game_dir() -> str:
    """The game data dir: cwd/data if it holds THIS tree's log
    (attila_ml_log.txt -- not v1's attila_ai_log.txt), else the Steam path.
    """
    cwd_data = os.path.join(os.getcwd(), "data")
    if os.path.isfile(os.path.join(cwd_data, "attila_ml_log.txt")):
        return cwd_data
    return GAME_DATA


class Channel:
    """One battle-agnostic transport over the game data dir."""

    def __init__(self, data_dir: str | None = None):
        self.data_dir = data_dir or find_game_dir()
        self._cache: dict = {}  # path -> (parsed, mtime)
        self._act_seq: int | None = None  # reseeded lazily from disk

    def read_json(self, name: str):
        """(payload, age_seconds) with the LAST-GOOD contract above.

        (None, None) only before the first successful read ever.
        """
        raise NotImplementedError("todo: mtime cache; ENOENT/parse-fail -> cached copy")

    def poll_obs(self, last_seq: int):
        """A NEW obs frame exactly once: payload iff seq > last_seq, else None."""
        raise NotImplementedError("todo: read_json(obs); gate on payload['seq']")

    def write_act(self, text: str) -> None:
        """Atomic write of one act frame (tmp + os.replace, utf-8)."""
        raise NotImplementedError("todo: tmp write + os.replace")

    def next_act_seq(self) -> int:
        """Next small monotonic act seq (reseed from the on-disk act file once)."""
        raise NotImplementedError("todo: parse existing act frame line for seq on first call")
