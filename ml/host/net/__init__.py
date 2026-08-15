"""ml/host/net/ -- the battle model (ML_DESIGN's OUTPUT section, real).

The ML seat's substrate: model.py will import THIS when the wiring
lands; until then the package is standalone -- nothing in the runtime
pipeline requires it, and it requires only the contract level (spec.py,
convert.py) plus torch/numpy. Layer position: between the model.py/
masks.py seat and spec.py; imports point strictly down.

  config.py   every dimension and [G] knob in one dataclass
  encoder.py  token featurizers + transformer body + per-family keys
  heads.py    the 13 heads: MLPs, pointer dot-products, stage-B FiLM CNN
  policy.py   BattlePolicy = teacher-forced losses + inference decode + referee

Torch lives only under this package (and data/train); the runtime host
loop stays torch-free until the model is actually seated.
"""

from .config import NetConfig
from .policy import BattlePolicy, Decision, TrainKnobs

__all__ = ["NetConfig", "BattlePolicy", "Decision", "TrainKnobs"]
