"""ml/host/model.py -- the ML seat. EMPTY BY DESIGN.

The pipeline's only contract with the model: one Observation (+ masks)
in, one list of per-unit Actions out, once per decision tick. Encoder,
heads, decode procedure, referee -- all of ML_DESIGN's OUTPUT section --
land here later; nothing outside this module may care.
"""

from __future__ import annotations

from codec import Action, Observation


class Model:
    """Placeholder for the battle model (ML_DESIGN OUTPUT spec)."""

    def load(self, checkpoint_path: str | None = None) -> None:
        raise NotImplementedError("todo: the model")

    def infer(self, obs: Observation, masks: dict) -> list[Action]:
        """One decision tick: <= 1 action per friendly slot; omitting a
        slot means no_change. Includes the exclusive-target referee."""
        raise NotImplementedError("todo: the model")


class NoChangeModel(Model):
    """Trivial stand-in: does nothing, every tick. Lets the IPC loop run
    end to end before any ML exists."""

    def load(self, checkpoint_path: str | None = None) -> None:
        pass

    def infer(self, obs: Observation, masks: dict) -> list[Action]:
        return []
