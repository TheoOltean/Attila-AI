"""ml/host/run.py -- the host process loop.

Owns its own dispatch (v1 lesson: order flow must never depend on a
browser poll). Per battle: wait for a hello, validate the spec mirror,
load map assets, then per obs frame: decode -> masks -> model.infer ->
encode -> atomic act write. A stale obs (game paused / loading) is
normal -- idle patiently; a battle_id change resets everything.

Usage: py ml/host/run.py
"""

from __future__ import annotations

import time

import channel as channel_mod
import codec
import masks as masks_mod
import spec
from model import NoChangeModel

POLL_S = 0.1  # obs poll cadence (obs cadence itself is 1 s sim time)


def serve_battle(ch: channel_mod.Channel, hello: dict) -> None:
    """One battle: decode/infer/act until the battle_id changes or the
    feed goes terminal (phase complete)."""
    raise NotImplementedError(
        "todo: spec.validate_hello(hello); assets = codec.MapAssets(); "
        "assets.load(hello['map']['key']); "
        "loop: ch.poll_obs -> codec.decode_obs(payload, hello, assets) -> "
        "masks_mod.legal -> model.infer -> codec.encode_act -> ch.write_act; "
        "track ack ring for logging"
    )


def main() -> None:
    ch = channel_mod.Channel()
    print(f"[ml-host] data dir: {ch.data_dir}")
    model = NoChangeModel()
    model.load()
    last_battle_id = None
    while True:
        # todo: hello, age = ch.read_json("hello"); on a NEW battle_id
        # (and a live feed) -> serve_battle(ch, hello); else idle.
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
