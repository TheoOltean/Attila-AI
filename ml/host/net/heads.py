"""ml/host/net/heads.py -- the 13 heads (ML_DESIGN heads table).

Every discrete head is logits for a masked softmax (masking happens in
policy.py, where training and decode share it); the only regressions
are the stage-B sub-pixel offset and the value head. Pointer heads are
scaled dot products q(a) . K_family; K_* come from the encoder. Stage B
is the FiLM-conditioned CNN over one 64x64 HIRES crop: pixel softmax
over 4096 + a 2-d tanh offset read at one pixel, and mean-pooled crop
features that join fourier(x, y) as l for the facing/width heads.
"""

from __future__ import annotations

import math

import torch
from torch import nn

import spec
from .config import NetConfig


class MLP(nn.Module):
    def __init__(self, d_in: int, d_hidden: int, d_out: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(d_in, d_hidden), nn.GELU(), nn.Linear(d_hidden, d_out))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class FiLMBlock(nn.Module):
    """conv -> (1 + gamma) * h + beta -> GELU, gamma/beta from a."""

    def __init__(self, c_in: int, c_out: int, a_dim: int):
        super().__init__()
        self.conv = nn.Conv2d(c_in, c_out, 3, padding=1)
        self.film = nn.Linear(a_dim, 2 * c_out)
        nn.init.zeros_(self.film.weight)
        nn.init.zeros_(self.film.bias)

    def forward(self, x: torch.Tensor, a: torch.Tensor) -> torch.Tensor:
        h = self.conv(x)
        gamma, beta = self.film(a).chunk(2, dim=-1)
        h = h * (1.0 + gamma[..., None, None]) + beta[..., None, None]
        return torch.nn.functional.gelu(h)


class CropCNN(nn.Module):
    """Stage B: crop [6, 64, 64] + FiLM(a) -> F [crop_ch, 64, 64],
    pixel logits [4096], offset head read at a pixel, mean(F) for l."""

    def __init__(self, cfg: NetConfig):
        super().__init__()
        self.blocks = nn.ModuleList([
            FiLMBlock(spec.TERRAIN_HIRES_CH, cfg.crop_ch // 2, cfg.a_dim),
            FiLMBlock(cfg.crop_ch // 2, cfg.crop_ch, cfg.a_dim),
            FiLMBlock(cfg.crop_ch, cfg.crop_ch, cfg.a_dim),
        ])
        self.pix = nn.Conv2d(cfg.crop_ch, 1, 1)  # W_pix
        self.off = nn.Sequential(  # MLP_off; tanh in policy scales to half-pixel
            nn.Linear(cfg.crop_ch, cfg.offset_hidden), nn.GELU(),
            nn.Linear(cfg.offset_hidden, 2))

    def features(self, crop: torch.Tensor, a: torch.Tensor) -> torch.Tensor:
        h = crop
        for block in self.blocks:
            h = block(h, a)
        return h  # [N, crop_ch, 64, 64]

    def pixel_logits(self, F: torch.Tensor) -> torch.Tensor:
        return self.pix(F).flatten(1)  # [N, 4096]

    def offset(self, F: torch.Tensor, pixel: torch.Tensor) -> torch.Tensor:
        """tanh offset in [-1, 1]^2 read at F[p] (flat pixel index)."""
        n = torch.arange(F.shape[0], device=F.device)
        feat = F.flatten(2)[n, :, pixel]  # [N, crop_ch]
        return torch.tanh(self.off(feat))


class Heads(nn.Module):
    def __init__(self, cfg: NetConfig):
        super().__init__()
        d, a, l, h = cfg.d_model, cfg.a_dim, cfg.l_dim, cfg.head_hidden
        self.scale = 1.0 / math.sqrt(d)
        self.verb_embed = nn.Embedding(len(spec.VERBS), cfg.verb_embed)  # E_verb

        self.verb = MLP(d, h, len(spec.VERBS))
        self.q_loc = nn.Linear(a, d)
        self.q_enemy = nn.Linear(a, d)
        self.q_struct = nn.Linear(a, d)
        self.q_veh = nn.Linear(a, d)
        self.crop = CropCNN(cfg)
        self.facing = MLP(a + l, h, spec.FACING_BINS)
        self.width = MLP(a + l, h, spec.WIDTH_BINS)
        self.run = MLP(a, h, 2)
        self.shot = MLP(a, h, spec.SHOT_VOCAB)
        self.stance = MLP(a, h, 2 * spec.STANCE_VOCAB)  # joint (stance, on/off)
        self.formation = MLP(a, h, spec.FORMATION_VOCAB)
        self.ability = MLP(a, h, spec.ABILITY_VOCAB)
        self.value = MLP(d, cfg.value_hidden, 1)  # aux; no BC loss (v0)

    def a(self, u: torch.Tensor, verb: torch.Tensor) -> torch.Tensor:
        """a = u_i (+) E_verb(V)."""
        return torch.cat([u, self.verb_embed(verb)], dim=-1)

    def pointer(self, q: nn.Linear, a: torch.Tensor, K: torch.Tensor) -> torch.Tensor:
        """Scaled dot-product pointer logits: [N, a_dim] x [N, S, d] -> [N, S]."""
        return torch.einsum("nd,nsd->ns", q(a), K) * self.scale
