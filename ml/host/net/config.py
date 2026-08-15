"""ml/host/net/config.py -- every architectural dimension in one place.

Defaults are ML_DESIGN's numbers; fields marked [G] there are guesses,
exposed here so a scaling run is a config change, never an edit. The
~7M-weight point is d_model 256 / 6 layers; knobs 384/8 ~= 16M,
512/8 ~= 30M (body ~= 12*L*D^2 dominates params; map tokens dominate
FLOPs). Vocab/slot sizes come from spec and are NOT repeated here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NetConfig:
    d_model: int = 256        # [G] token width; u_i is this size
    n_layers: int = 6         # [G]
    n_heads: int = 8          # [G]
    ff_mult: int = 4
    dropout: float = 0.0

    type_embed: int = 64      # [G] TYPE_EMBED (unit-type vocab embedding)
    verb_embed: int = 32      # [G] E_verb; a = u_i (+) E_verb(V)

    fourier_bands: int = 8    # per axis; fourier(x,y) = 4*bands dims
    map_stem_ch: int = 64     # terrain conv stem width (128 -> 32 grid)
    crop_ch: int = 64         # stage-B CNN channels (spec: 64x64x64 features)

    head_hidden: int = 256    # MLP head hidden width
    offset_hidden: int = 64   # MLP_off hidden width
    value_hidden: int = 128   # value head hidden width

    @property
    def fourier_dim(self) -> int:
        return 4 * self.fourier_bands  # sin+cos per band, two axes

    @property
    def a_dim(self) -> int:
        return self.d_model + self.verb_embed

    @property
    def l_dim(self) -> int:
        return self.fourier_dim + self.crop_ch  # fourier(x,y) (+) mean(F)
