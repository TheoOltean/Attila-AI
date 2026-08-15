"""ml/host/net/policy.py -- BattlePolicy: losses (training) + decode (inference).

Teacher forcing per ML_DESIGN: every head is evaluated against the
label GIVEN the label's earlier choices -- a is built from the pro's
verb, the stage-B crop sits at the pro's true cell, l comes from the
pro's true x, y. Heads outside the label verb's arg list get no loss
and no gradient. Masks force illegal probabilities to exactly 0 in both
paths; a label the mask forbids is by law a MASK BUG -- counted in the
metrics (`mask_violations`), excluded from the loss, never trained on.

Decode is the spec's inference procedure verbatim: per-unit sequential
masked sampling (verb -> args), joint log-prob tracked per unit, then
the exclusive-target referee: collisions keep the unit with the highest
joint probability, losers revert to no_change [G]. Verb->arg routing
derives from spec.VERBS -- never a hand-synced list.

Joint stance convention (defined HERE): index = stance * 2 + on.

Label tensors (built by data.py, -1/-inf = absent): verb [B,40] with
-1 marking untrainable slots (nonexistent unit); per-arg targets
loc_cell/loc_pixel [B,40], loc_off/loc_xy [B,40,2], facing/width/run/
enemy/struct/vehicle/shot/stance/formation/ability [B,40]; crop_index
[B,40] into the crops tensor; crops [N,6,64,64] + loc_fine_mask [N,4096].
"""

from __future__ import annotations

from dataclasses import dataclass, field

import torch
from torch import nn
from torch.nn import functional as TF

import convert
import spec
from .config import NetConfig
from .encoder import UNIT_EXISTS, Encoder, fourier_xy
from .heads import Heads

NO_CHANGE = spec.VERB_INDEX["no_change"]
ARGS_OF = {i: frozenset(args) for i, (_k, args) in enumerate(spec.VERBS)}
VERBS_WITH = {
    arg: torch.tensor(sorted(i for i, a in ARGS_OF.items() if arg in a))
    for arg in spec.ARG_TOKENS
}
# Exclusive-target resources for the referee [G -- spec leaves the set open]:
# one crew per vehicle; structure entry treated exclusive until capacity lands.
EXCLUSIVE = {"occupy_vehicle": "vehicle", "enter_structure": "struct"}
NEG = -1e9  # masked-logit fill; softmax(NEG) == exactly 0 in f32


@dataclass
class TrainKnobs:
    """Loss shaping -- every value [G] until pro-data tuning."""

    weights: dict = field(default_factory=lambda: {
        "verb": 1.0, "loc_coarse": 1.0, "loc_fine": 1.0, "loc_offset": 1.0,
        "facing": 0.5, "width": 0.5, "run": 0.25, "enemy": 1.0,
        "struct": 1.0, "vehicle": 1.0, "shot": 0.5, "stance": 0.5,
        "formation": 0.5, "ability": 0.5,
    })
    no_change_weight: float = 0.05   # [G] verb-CE down-weight to match pro APM
    loc_smooth_sigma: float = 1.0    # [G] stage-B Gaussian label smoothing, px
    huber_delta: float = 1.0         # offset regression


@dataclass
class Decision:
    """One unit's sampled action, index-form (wire conversion is the
    runtime's job: meters/deg/width_m need map dims + unit frontage)."""

    u: int
    verb: int
    logp: float                       # joint log-prob of every sampled step
    cell: int | None = None
    pixel: int | None = None
    offset: tuple[float, float] | None = None
    xy: tuple[float, float] | None = None   # normalized map coords
    facing_bin: int | None = None
    width_bin: int | None = None
    run: int | None = None
    enemy: int | None = None
    struct: int | None = None
    vehicle: int | None = None
    shot: int | None = None
    stance: int | None = None
    on: int | None = None
    formation: int | None = None
    ability: int | None = None

    @property
    def verb_key(self) -> str:
        return spec.VERBS[self.verb][0]


def masked(logits: torch.Tensor, mask: torch.Tensor | None) -> torch.Tensor:
    return logits if mask is None else logits.masked_fill(~mask, NEG)


class BattlePolicy(nn.Module):
    def __init__(self, cfg: NetConfig | None = None):
        super().__init__()
        self.cfg = cfg or NetConfig()
        self.encoder = Encoder(self.cfg)
        self.heads = Heads(self.cfg)

    # ---------------- training ----------------

    def losses(self, obs: dict, labels: dict, masks: dict, knobs: TrainKnobs,
               ) -> tuple[torch.Tensor, dict]:
        """One teacher-forced pass -> (total loss, metrics). One backward
        on the total updates all heads + encoder."""
        enc = self.encoder(obs)
        valid = labels["verb"] >= 0                      # [B, 40]
        bidx = valid.nonzero(as_tuple=True)[0]           # batch of each row
        n_valid = int(valid.sum())
        sums: dict[str, torch.Tensor] = {}
        counts: dict[str, int] = {}
        violations = 0
        dev = enc["u"].device

        def ce(name: str, logits: torch.Tensor, mask: torch.Tensor | None,
               target: torch.Tensor, weight: torch.Tensor | None = None):
            """Masked CE with the mask-bug self-test: illegal targets are
            counted and dropped, never trained on."""
            nonlocal violations
            if mask is not None:
                ok = mask.gather(1, target.unsqueeze(1)).squeeze(1)
                violations += int((~ok).sum())
                logits, target = masked(logits, mask)[ok], target[ok]
                if weight is not None:
                    weight = weight[ok]
            if target.numel() == 0:
                return
            loss = TF.cross_entropy(logits, target, reduction="none")
            if weight is not None:
                loss = loss * weight
            sums[name] = loss.sum()
            counts[name] = int(target.numel())

        # Verb head: every trainable slot, no_change down-weighted.
        u = enc["u"][valid]                              # [Nv, d]
        verb_t = labels["verb"][valid]
        w = torch.where(verb_t == NO_CHANGE,
                        torch.tensor(knobs.no_change_weight, device=dev),
                        torch.tensor(1.0, device=dev))
        ce("verb", self.heads.verb(u), masks["verb_mask"][valid], verb_t, w)

        # a from the PRO's verb (teacher forcing), for every arg head.
        a = self.heads.a(u, verb_t)

        def takes(arg: str) -> torch.Tensor:
            return torch.isin(verb_t, VERBS_WITH[arg].to(dev))

        # --- location (stage A + B + offset), then l for facing/width ---
        crop_ix = labels["crop_index"][valid]
        loc_sel = takes("loc") & (labels["loc_cell"][valid] >= 0) & (crop_ix >= 0)
        l_feat = None
        loc_pos = torch.full_like(verb_t, -1)
        if loc_sel.any():
            loc_pos[loc_sel] = torch.arange(int(loc_sel.sum()), device=dev)
            a_loc, b_loc = a[loc_sel], bidx[loc_sel]
            ce("loc_coarse",
               self.heads.pointer(self.heads.q_loc, a_loc, enc["K_map"][b_loc]),
               masks["loc_coarse_mask"][b_loc],
               labels["loc_cell"][valid][loc_sel])

            crops = labels["crops"][crop_ix[loc_sel]]
            fine_mask = labels["loc_fine_mask"][crop_ix[loc_sel]]
            pix_t = labels["loc_pixel"][valid][loc_sel]
            F = self.heads.crop.features(crops, a_loc)
            self._fine_ce(F, fine_mask, pix_t, knobs, sums, counts)
            off = self.heads.crop.offset(F, pix_t)
            off_t = labels["loc_off"][valid][loc_sel]
            sums["loc_offset"] = TF.huber_loss(
                off, off_t, delta=knobs.huber_delta, reduction="none").sum()
            counts["loc_offset"] = int(pix_t.numel())
            # l from the pro's TRUE x, y (+) mean-pooled crop features.
            l_feat = torch.cat([
                fourier_xy(labels["loc_xy"][valid][loc_sel], self.cfg.fourier_bands),
                F.mean(dim=(2, 3))], dim=-1)

        for name in ("facing", "width"):
            t = labels[name][valid]
            sel = takes(name) & (t >= 0) & (loc_pos >= 0)
            if sel.any():
                al = torch.cat([a[sel], l_feat[loc_pos[sel]]], dim=-1)
                ce(name, getattr(self.heads, name)(al), None, t[sel])

        t = labels["run"][valid]
        sel = takes("run") & (t >= 0)
        if sel.any():
            ce("run", self.heads.run(a[sel]), None, t[sel])

        # --- pointer targets ---
        for name, key, q, mask_key, per_unit in (
                ("enemy", "K_enemy", self.heads.q_enemy, "enemy_target_mask", False),
                ("struct", "K_struct", self.heads.q_struct, "struct_target_mask", True),
                ("vehicle", "K_veh", self.heads.q_veh, "vehicle_target_mask", False)):
            t = labels[name][valid]
            sel = takes(name) & (t >= 0)
            if sel.any():
                logits = self.heads.pointer(q, a[sel], enc[key][bidx[sel]])
                m = masks[mask_key][valid][sel] if per_unit else masks[mask_key][bidx[sel]]
                ce(name, logits, m, t[sel])

        # --- per-unit vocab heads ---
        for name, mask_key in (("shot", "shot_type_mask"), ("stance", "stance_mask"),
                               ("formation", "formation_mask"), ("ability", "ability_mask")):
            t = labels[name][valid]
            sel = takes(name) & (t >= 0)
            if sel.any():
                ce(name, getattr(self.heads, name)(a[sel]),
                   masks[mask_key][valid][sel], t[sel])

        denom = max(n_valid, 1)
        total = sum(knobs.weights[k] * v for k, v in sums.items()) / denom
        if not isinstance(total, torch.Tensor):  # zero trainable slots
            total = torch.zeros((), device=dev, requires_grad=True)
        metrics = {f"loss/{k}": (v / max(counts[k], 1)).item() for k, v in sums.items()}
        metrics["loss/total"] = float(total.item())
        metrics["mask_violations"] = violations
        metrics["units"] = n_valid
        return total, metrics

    def _fine_ce(self, F, fine_mask, pix_t, knobs, sums, counts):
        """Stage-B pixel CE, Gaussian-smoothed over neighboring pixels."""
        logits = masked(self.heads.crop.pixel_logits(F), fine_mask)
        sigma = knobs.loc_smooth_sigma
        if sigma <= 0:
            sums["loc_fine"] = TF.cross_entropy(logits, pix_t, reduction="sum")
        else:
            P = spec.LOC_PATCH
            rc = torch.stack([pix_t // P, pix_t % P], dim=-1).float()  # [N, 2]
            grid = torch.arange(P, device=F.device).float()
            yy, xx = torch.meshgrid(grid, grid, indexing="ij")
            d2 = ((yy.flatten()[None] - rc[:, :1]) ** 2
                  + (xx.flatten()[None] - rc[:, 1:]) ** 2)
            target = torch.exp(-d2 / (2 * sigma * sigma)) * fine_mask
            target = target / target.sum(dim=-1, keepdim=True).clamp_min(1e-8)
            sums["loc_fine"] = -(target * TF.log_softmax(logits, dim=-1)).sum()
        counts["loc_fine"] = int(pix_t.numel())

    def value(self, obs: dict) -> torch.Tensor:
        """Aux value head over the global token (no BC target in v0)."""
        return self.heads.value(self.encoder(obs)["global"]).squeeze(-1)

    # ---------------- inference ----------------

    @torch.no_grad()
    def decode(self, obs: dict, masks: dict, hires: torch.Tensor,
               fine_mask_fn=None, temperature: float = 1.0,
               generator: torch.Generator | None = None) -> list[Decision]:
        """One decision tick, batch of 1. hires = [6, 2048, 2048] tensor;
        fine_mask_fn(cell) -> bool[4096] (masks.fine_mask_for_crop when
        wired; None = unmasked). temperature 1.0 [G eval knob]; <= 0 = argmax.
        no_change and refereed-out units are simply omitted."""
        cfg = self.cfg
        enc = self.encoder(obs)
        alive = obs["friendly_feat"][0, :, UNIT_EXISTS] > 0

        def sample(logits: torch.Tensor, mask=None) -> tuple[int, float]:
            logits = masked(logits, mask)
            if temperature <= 0:
                i = int(logits.argmax())
            else:
                probs = TF.softmax(logits / temperature, dim=-1)
                i = int(torch.multinomial(probs, 1, generator=generator))
            return i, float(TF.log_softmax(logits, dim=-1)[i])

        out: list[Decision] = []
        for i in range(spec.MAX_FRIENDLY):
            if not alive[i]:
                continue
            u_i = enc["u"][0, i]
            verb, lp = sample(self.heads.verb(u_i), masks["verb_mask"][0, i])
            if verb == NO_CHANGE:
                continue
            d = Decision(u=i, verb=verb, logp=lp)
            args = ARGS_OF[verb]
            a = self.heads.a(u_i.unsqueeze(0), torch.tensor([verb], device=u_i.device))
            l_feat = None

            if "loc" in args:
                q = self.heads.pointer(self.heads.q_loc, a, enc["K_map"][:1])[0]
                d.cell, lp = sample(q, masks["loc_coarse_mask"][0]); d.logp += lp
                r0, c0 = convert.cell_crop_origin(d.cell)
                P = spec.LOC_PATCH
                crop = hires[:, r0:r0 + P, c0:c0 + P].unsqueeze(0).float()
                F = self.heads.crop.features(crop, a)
                fm = None
                if fine_mask_fn is not None:  # ndarray or tensor accepted
                    fm = torch.as_tensor(fine_mask_fn(d.cell),
                                         dtype=torch.bool, device=u_i.device)
                d.pixel, lp = sample(self.heads.crop.pixel_logits(F)[0], fm)
                d.logp += lp
                off = self.heads.crop.offset(
                    F, torch.tensor([d.pixel], device=u_i.device))[0]
                d.offset = (float(off[0]), float(off[1]))
                d.xy = convert.loc_to_xy(d.cell, d.pixel, d.offset, 1.0, 1.0)
                l_feat = torch.cat([
                    fourier_xy(torch.tensor([d.xy], device=u_i.device), cfg.fourier_bands),
                    F.mean(dim=(2, 3))], dim=-1)

            if "facing" in args:
                d.facing_bin, lp = sample(self.heads.facing(torch.cat([a, l_feat], -1))[0])
                d.logp += lp
            if "width" in args:
                d.width_bin, lp = sample(self.heads.width(torch.cat([a, l_feat], -1))[0])
                d.logp += lp
            if "run" in args:
                d.run, lp = sample(self.heads.run(a)[0]); d.logp += lp
            if "enemy" in args:
                d.enemy, lp = sample(
                    self.heads.pointer(self.heads.q_enemy, a, enc["K_enemy"][:1])[0],
                    masks["enemy_target_mask"][0])
                d.logp += lp
            if "struct" in args:
                d.struct, lp = sample(
                    self.heads.pointer(self.heads.q_struct, a, enc["K_struct"][:1])[0],
                    masks["struct_target_mask"][0, i])
                d.logp += lp
            if "vehicle" in args:
                d.vehicle, lp = sample(
                    self.heads.pointer(self.heads.q_veh, a, enc["K_veh"][:1])[0],
                    masks["vehicle_target_mask"][0])
                d.logp += lp
            if "shot" in args:
                d.shot, lp = sample(self.heads.shot(a)[0], masks["shot_type_mask"][0, i])
                d.logp += lp
            if "stance" in args:
                joint, lp = sample(self.heads.stance(a)[0], masks["stance_mask"][0, i])
                d.stance, d.on = joint // 2, joint % 2
                d.logp += lp
            if "formation" in args:
                d.formation, lp = sample(self.heads.formation(a)[0],
                                         masks["formation_mask"][0, i])
                d.logp += lp
            if "ability" in args:
                d.ability, lp = sample(self.heads.ability(a)[0],
                                       masks["ability_mask"][0, i])
                d.logp += lp
            out.append(d)
        return self._referee(out)

    @staticmethod
    def _referee(decisions: list[Decision]) -> list[Decision]:
        """Exclusive-target collisions: keep the highest joint probability,
        losers revert to no_change (dropped) [G]."""
        best: dict[tuple, Decision] = {}
        keep = []
        for d in decisions:
            arg = EXCLUSIVE.get(d.verb_key)
            if arg is None:
                keep.append(d)
                continue
            key = (arg, getattr(d, arg))
            prev = best.get(key)
            if prev is None or d.logp > prev.logp:
                best[key] = d
        return sorted(keep + list(best.values()), key=lambda d: d.u)
