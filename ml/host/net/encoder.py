"""ml/host/net/encoder.py -- observation -> token embeddings.

One transformer over one token sequence, fixed layout:

  [ global | 40 friendly | 40 enemy | 512 struct | 32 veh | 1024 map ]

exists = 0 slots are dropped from attention via key padding (the spec's
"dropped before the encoder"); map tokens are the terrain raster conv-
stemmed 128 -> 32, so the 32x32 map-token grid IS the coarse location
grid -- K_map keys index it directly, 1 token = 1 coarse cell. Every
positioned token gets the shared Fourier x/y encoding (normalized map
coords, the same fourier() the heads use for l).

Feature columns are derived from spec.FEAT, never hand-typed numbers.
"""

from __future__ import annotations

import math

import torch
from torch import nn

import spec
from .config import NetConfig

# Column indices, derived from the spec's block table (ML_DESIGN order:
# LIVE opens with x, y and closes with exists; structures/vehicles put
# x, y after their class one-hot and exists last).
_LIVE0 = spec.FEAT["LIVE"][0]
UNIT_X, UNIT_Y = _LIVE0, _LIVE0 + 1
UNIT_EXISTS = spec.FEAT["LIVE"][1] - 1
UNIT_IS_GENERAL = 42  # static card, ML_DESIGN [0:44]
STRUCT_X, STRUCT_Y = len(spec.STRUCT_CLASSES), len(spec.STRUCT_CLASSES) + 1
STRUCT_EXISTS = spec.STRUCT_DIM - 1
VEH_X, VEH_Y = len(spec.VEH_TYPES), len(spec.VEH_TYPES) + 1
VEH_EXISTS = spec.VEH_DIM - 1

# Token-sequence layout offsets.
N_MAP = spec.COARSE_LOC * spec.COARSE_LOC
TOK_GLOBAL = 0
TOK_FRIENDLY = 1
TOK_ENEMY = TOK_FRIENDLY + spec.MAX_FRIENDLY
TOK_STRUCT = TOK_ENEMY + spec.MAX_ENEMY
TOK_VEH = TOK_STRUCT + spec.MAX_STRUCT
TOK_MAP = TOK_VEH + spec.MAX_VEH
N_TOKENS = TOK_MAP + N_MAP


def fourier_xy(xy: torch.Tensor, bands: int) -> torch.Tensor:
    """[..., 2] normalized coords -> [..., 4*bands] sin/cos features."""
    freqs = 2.0 ** torch.arange(bands, device=xy.device, dtype=xy.dtype)
    ang = 2.0 * math.pi * xy.unsqueeze(-1) * freqs  # [..., 2, bands]
    out = torch.cat([torch.sin(ang), torch.cos(ang)], dim=-1)  # [..., 2, 2b]
    return out.flatten(-2)


class Encoder(nn.Module):
    def __init__(self, cfg: NetConfig):
        super().__init__()
        self.cfg = cfg
        d = cfg.d_model

        self.type_embed = nn.Embedding(spec.TYPE_VOCAB, cfg.type_embed, padding_idx=0)
        self.unit_in = nn.Linear(cfg.type_embed + spec.UNIT_FEAT_DIM, d)
        self.struct_in = nn.Linear(spec.STRUCT_DIM, d)
        self.veh_in = nn.Linear(spec.VEH_DIM, d)
        self.global_in = nn.Linear(spec.GLOBAL_DIM, d)
        self.pos_in = nn.Linear(cfg.fourier_dim, d)  # shared x/y positional map
        self.family_embed = nn.Embedding(6, d)  # global/friendly/enemy/struct/veh/map

        # Terrain stem: [11, 128, 128] -> [d, 32, 32] map tokens.
        s = cfg.map_stem_ch
        self.map_stem = nn.Sequential(
            nn.Conv2d(spec.TERRAIN_CH, s, 3, stride=2, padding=1), nn.GELU(),
            nn.Conv2d(s, 2 * s, 3, stride=2, padding=1), nn.GELU(),
            nn.Conv2d(2 * s, d, 1),
        )

        layer = nn.TransformerEncoderLayer(
            d_model=d, nhead=cfg.n_heads, dim_feedforward=cfg.ff_mult * d,
            dropout=cfg.dropout, activation="gelu", batch_first=True,
            norm_first=True)
        self.body = nn.TransformerEncoder(layer, cfg.n_layers,
                                          norm=nn.LayerNorm(d))

        # Per-family key projections for the pointer heads (decode's
        # "K_* = W_k . tokens", computed once per tick).
        self.k_map = nn.Linear(d, d)
        self.k_enemy = nn.Linear(d, d)
        self.k_struct = nn.Linear(d, d)
        self.k_veh = nn.Linear(d, d)

        # Map-cell centers in normalized coords, fixed (col <-> x, row <-> y).
        c = (torch.arange(spec.COARSE_LOC).float() + 0.5) / spec.COARSE_LOC
        yy, xx = torch.meshgrid(c, c, indexing="ij")
        self.register_buffer("map_xy", torch.stack([xx, yy], dim=-1).reshape(N_MAP, 2))

    def forward(self, obs: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
        """obs: spec.SHAPES-named tensors (minus terrain_hires), batch-first.

        Returns token outputs sliced per family plus the pointer keys and
        the attention pad mask (True = nonexistent slot).
        """
        cfg = self.cfg
        B = obs["global"].shape[0]

        def unit_tokens(t: torch.Tensor, f: torch.Tensor) -> torch.Tensor:
            e = torch.cat([self.type_embed(t), f], dim=-1)
            return self.unit_in(e) + self.pos_in(
                fourier_xy(f[..., [UNIT_X, UNIT_Y]], cfg.fourier_bands))

        fr = unit_tokens(obs["friendly_type"], obs["friendly_feat"])
        en = unit_tokens(obs["enemy_type"], obs["enemy_feat"])
        st = self.struct_in(obs["structures"]) + self.pos_in(
            fourier_xy(obs["structures"][..., [STRUCT_X, STRUCT_Y]], cfg.fourier_bands))
        vh = self.veh_in(obs["vehicles"]) + self.pos_in(
            fourier_xy(obs["vehicles"][..., [VEH_X, VEH_Y]], cfg.fourier_bands))
        gl = self.global_in(obs["global"]).unsqueeze(1)
        mp = self.map_stem(obs["terrain"]).flatten(2).transpose(1, 2)  # [B, 1024, d]
        mp = mp + self.pos_in(fourier_xy(self.map_xy, cfg.fourier_bands)).unsqueeze(0)

        fam = self.family_embed.weight
        seq = torch.cat([
            gl + fam[0], fr + fam[1], en + fam[2],
            st + fam[3], vh + fam[4], mp + fam[5],
        ], dim=1)

        pad = torch.zeros(B, N_TOKENS, dtype=torch.bool, device=seq.device)
        pad[:, TOK_FRIENDLY:TOK_ENEMY] = obs["friendly_feat"][..., UNIT_EXISTS] <= 0
        pad[:, TOK_ENEMY:TOK_STRUCT] = obs["enemy_feat"][..., UNIT_EXISTS] <= 0
        pad[:, TOK_STRUCT:TOK_VEH] = obs["structures"][..., STRUCT_EXISTS] <= 0
        pad[:, TOK_VEH:TOK_MAP] = obs["vehicles"][..., VEH_EXISTS] <= 0

        out = self.body(seq, src_key_padding_mask=pad)

        enemy_out = out[:, TOK_ENEMY:TOK_STRUCT]
        struct_out = out[:, TOK_STRUCT:TOK_VEH]
        veh_out = out[:, TOK_VEH:TOK_MAP]
        map_out = out[:, TOK_MAP:]
        return {
            "global": out[:, TOK_GLOBAL],
            "u": out[:, TOK_FRIENDLY:TOK_ENEMY],  # u_i, the per-unit embeddings
            "K_map": self.k_map(map_out),
            "K_enemy": self.k_enemy(enemy_out),
            "K_struct": self.k_struct(struct_out),
            "K_veh": self.k_veh(veh_out),
            "pad": pad,
        }
