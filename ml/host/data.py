"""ml/host/data.py -- training samples: shard format, dataset, collation.

One battle = one directory (the extraction pipeline's output target,
synthetic generator below until that exists):

  static.npz    terrain [11,128,128], terrain_hires [6,2048,2048],
                map_w, map_h  (per-battle constants; hires read as crops)
  ticks.npz     tick [T] + every per-tick obs array stacked on axis 0
  actions.json  serialized action records (ML_DESIGN wire form: tick, u,
                verb key + that verb's continuous args)

One sample = (obs arrays at tick t) -> one label per friendly slot: the
pro's order to that unit in [t, t+1) as (verb, args), else no_change;
several orders in one window keep the LAST [G]. Nonexistent slots get
verb -1 (no loss). Actions are stored CONTINUOUS (meters/deg/width_m)
and become bin targets here through convert.py -- the same converter
that turns model output back into orders. Masks are built per sample by
mask_fn (masks.legal once it lands -- shared verbatim with inference,
never stored); until then callers may inject one (synthetic does).

Width labels: "width_bin" direct, or width_m + wmin + wmax through the
converter (per-unit frontage source is an open spec question).
"""

from __future__ import annotations

import json
import os

import numpy as np
import torch

import convert
import spec
from net.encoder import (STRUCT_EXISTS, STRUCT_X, STRUCT_Y, UNIT_EXISTS,
                         UNIT_X, UNIT_Y, VEH_EXISTS, VEH_X, VEH_Y)

OBS_KEYS = ("global", "friendly_type", "friendly_feat", "enemy_type",
            "enemy_feat", "structures", "vehicles", "terrain")
LABEL_IDX_KEYS = ("loc_cell", "loc_pixel", "facing", "width", "run", "enemy",
                  "struct", "vehicle", "shot", "stance", "formation",
                  "ability", "crop_index")
P = spec.LOC_PATCH


def _labels_for_tick(records: list[dict], arrays: dict, map_w: float,
                     map_h: float) -> dict:
    lab = {k: np.full(spec.MAX_FRIENDLY, -1, np.int64) for k in LABEL_IDX_KEYS}
    lab["loc_off"] = np.zeros((spec.MAX_FRIENDLY, 2), np.float32)
    lab["loc_xy"] = np.zeros((spec.MAX_FRIENDLY, 2), np.float32)
    exists = arrays["friendly_feat"][:, UNIT_EXISTS] > 0
    lab["verb"] = np.where(exists, spec.VERB_INDEX["no_change"], -1).astype(np.int64)

    for r in records:  # in-order overwrite = keep-last [G]
        u = r["u"]
        if not exists[u]:
            continue  # extraction bug or death race; never train on it
        verb = spec.VERB_INDEX[r["verb"]]
        lab["verb"][u] = verb
        args = dict(spec.VERBS)[r["verb"]]
        if "loc" in args:
            cell, pixel, off = convert.xy_to_loc(r["x"], r["y"], map_w, map_h)
            lab["loc_cell"][u], lab["loc_pixel"][u] = cell, pixel
            lab["loc_off"][u] = off
            lab["loc_xy"][u] = (r["x"] / map_w, r["y"] / map_h)
        if "facing" in args and "deg" in r:
            lab["facing"][u] = convert.deg_to_facing_bin(r["deg"])
        if "width" in args:
            if "width_bin" in r:
                lab["width"][u] = r["width_bin"]
            elif "width_m" in r and "wmin" in r:
                lab["width"][u] = convert.width_m_to_bin(
                    r["width_m"], r["wmin"], r["wmax"])
        if "run" in args and "run" in r:
            lab["run"][u] = int(r["run"])
        if "stance" in args:
            lab["stance"][u] = r["stance"] * 2 + int(r["on"])  # policy's joint
        for k in ("enemy", "struct", "vehicle", "shot", "formation", "ability"):
            if k in args and k in r:
                lab[k][u] = r[k]
    return lab


class BattleDataset:
    """Map-style dataset over battle directories; index = (battle, tick).

    Hires rasters are large, so battles are cached (2 deep) -- sample
    sequentially per battle (shuffle battle order, not the flat index)
    until a smarter shard layout is needed.
    """

    def __init__(self, battle_dirs: list[str], mask_fn=None, fine_mask_fn=None):
        if mask_fn is None or fine_mask_fn is None:
            import masks as masks_mod  # the runtime module, verbatim
            mask_fn = mask_fn or masks_mod.legal
            fine_mask_fn = fine_mask_fn or masks_mod.fine_mask_for_crop
        self.mask_fn, self.fine_mask_fn = mask_fn, fine_mask_fn
        self.dirs = battle_dirs
        self._cache: dict[str, tuple] = {}
        self.index: list[tuple[str, int]] = []
        for d in battle_dirs:
            n = len(np.load(os.path.join(d, "ticks.npz"))["tick"])
            self.index += [(d, i) for i in range(n)]

    def __len__(self) -> int:
        return len(self.index)

    def _battle(self, d: str) -> tuple:
        if d not in self._cache:
            if len(self._cache) >= 2:
                self._cache.pop(next(iter(self._cache)))
            static = dict(np.load(os.path.join(d, "static.npz")))
            ticks = dict(np.load(os.path.join(d, "ticks.npz")))
            with open(os.path.join(d, "actions.json"), encoding="utf-8") as f:
                by_tick: dict[int, list] = {}
                for r in json.load(f):
                    by_tick.setdefault(r["tick"], []).append(r)
            self._cache[d] = (static, ticks, by_tick)
        return self._cache[d]

    def __getitem__(self, i: int) -> dict:
        d, row = self.index[i]
        static, ticks, by_tick = self._battle(d)
        arrays = {k: ticks[k][row] for k in OBS_KEYS if k != "terrain"}
        arrays["terrain"] = static["terrain"]
        tick = int(ticks["tick"][row])
        labels = _labels_for_tick(by_tick.get(tick, []), arrays,
                                  float(static["map_w"]), float(static["map_h"]))

        # Crops + per-crop fine masks for the loc-labeled units.
        crops, fine = [], []
        hires = static["terrain_hires"]
        for u in np.nonzero(labels["loc_cell"] >= 0)[0]:
            labels["crop_index"][u] = len(crops)
            r0, c0 = convert.cell_crop_origin(int(labels["loc_cell"][u]))
            crops.append(hires[:, r0:r0 + P, c0:c0 + P].astype(np.float32))
            fine.append(np.asarray(
                self.fine_mask_fn(arrays, int(labels["loc_cell"][u])), bool))
        labels["crops"] = (np.stack(crops) if crops
                           else np.zeros((0, spec.TERRAIN_HIRES_CH, P, P), np.float32))
        labels["loc_fine_mask"] = (np.stack(fine) if fine
                                   else np.zeros((0, P * P), bool))
        return {"obs": arrays, "labels": labels, "masks": self.mask_fn(arrays)}


def collate(samples: list[dict], device=None) -> tuple[dict, dict, dict]:
    """Samples -> batched torch tensors (obs, labels, masks) for
    BattlePolicy.losses. Crop indices are re-based into the batch concat."""
    def stack(dicts, key, dtype=None):
        t = torch.from_numpy(np.stack([s[key] for s in dicts]))
        t = t.to(dtype) if dtype else t
        return t.to(device) if device else t

    obs_list = [s["obs"] for s in samples]
    obs = {k: stack(obs_list, k, torch.float32) for k in OBS_KEYS
           if not k.endswith("_type")}
    obs |= {k: stack(obs_list, k, torch.long)
            for k in ("friendly_type", "enemy_type")}

    lab_list = [s["labels"] for s in samples]
    labels = {k: stack(lab_list, k) for k in ("verb", *LABEL_IDX_KEYS)}
    labels |= {k: stack(lab_list, k) for k in ("loc_off", "loc_xy")}
    offset, crops, fine = 0, [], []
    for i, s in enumerate(samples):
        n = s["labels"]["crops"].shape[0]
        ix = labels["crop_index"][i]
        labels["crop_index"][i] = torch.where(ix >= 0, ix + offset, ix)
        crops.append(s["labels"]["crops"])
        fine.append(s["labels"]["loc_fine_mask"])
        offset += n
    labels["crops"] = torch.from_numpy(
        np.concatenate(crops) if offset else
        np.zeros((0, spec.TERRAIN_HIRES_CH, P, P), np.float32))
    labels["loc_fine_mask"] = torch.from_numpy(
        np.concatenate(fine) if offset else np.zeros((0, P * P), bool))
    if device:
        labels["crops"] = labels["crops"].to(device)
        labels["loc_fine_mask"] = labels["loc_fine_mask"].to(device)

    mask_list = [s["masks"] for s in samples]
    masks = {k: stack(mask_list, k, torch.bool) for k in spec.MASK_SHAPES
             if k != "loc_fine_mask"}
    return obs, labels, masks


# --- synthetic battles (smoke fuel until the extraction pipeline exists) ---

def permissive_masks(arrays: dict) -> dict:
    """Everything legal except the universal existence rule. Synthetic
    stand-in for masks.legal -- NEVER used on real data."""
    fr_exists = arrays["friendly_feat"][:, UNIT_EXISTS] > 0
    m = {
        "verb_mask": np.zeros(spec.MASK_SHAPES["verb_mask"], bool),
        "enemy_target_mask": arrays["enemy_feat"][:, UNIT_EXISTS] > 0,
        "struct_target_mask": np.tile(
            arrays["structures"][:, STRUCT_EXISTS] > 0, (spec.MAX_FRIENDLY, 1)),
        "vehicle_target_mask": arrays["vehicles"][:, VEH_EXISTS] > 0,
        "loc_coarse_mask": np.ones(spec.MASK_SHAPES["loc_coarse_mask"], bool),
    }
    for k in ("shot_type_mask", "stance_mask", "formation_mask", "ability_mask"):
        m[k] = np.ones(spec.MASK_SHAPES[k], bool) & fr_exists[:, None]
    m["verb_mask"][fr_exists] = True
    m["verb_mask"][:, spec.VERB_INDEX["no_change"]] = True
    return m


def permissive_fine_mask(_arrays: dict, _cell: int) -> np.ndarray:
    return np.ones(P * P, bool)


def make_synthetic_battle(out_dir: str, ticks: int = 32, n_units: int = 12,
                          seed: int = 0) -> None:
    """Random but label-consistent battle: obs in-range, actions always
    legal under permissive_masks (a clean run shows 0 mask violations)."""
    rng = np.random.default_rng(seed)
    map_w = map_h = 1300.0
    os.makedirs(out_dir, exist_ok=True)
    hires = np.zeros((spec.TERRAIN_HIRES_CH, spec.HIRES, spec.HIRES), np.float32)
    np.savez_compressed(
        os.path.join(out_dir, "static.npz"), map_w=map_w, map_h=map_h,
        terrain=rng.random((spec.TERRAIN_CH, spec.GRID, spec.GRID), np.float32) * 0.1,
        terrain_hires=hires)

    def units(n):
        t = np.zeros(spec.MAX_FRIENDLY, np.int64)
        f = np.zeros((spec.MAX_FRIENDLY, spec.UNIT_FEAT_DIM), np.float32)
        t[:n] = rng.integers(1, spec.TYPE_VOCAB, n)
        f[:n] = rng.random((n, spec.UNIT_FEAT_DIM)) * 0.5
        f[:n, UNIT_X], f[:n, UNIT_Y] = rng.random(n), rng.random(n)
        f[:n, UNIT_EXISTS] = 1.0
        return t, f

    T = ticks
    out = {"tick": np.arange(T),
           "global": rng.random((T, spec.GLOBAL_DIM), np.float32) * 0.5,
           "structures": np.zeros((T, spec.MAX_STRUCT, spec.STRUCT_DIM), np.float32),
           "vehicles": np.zeros((T, spec.MAX_VEH, spec.VEH_DIM), np.float32)}
    ft, ff = units(n_units)
    et, ef = units(n_units)
    for name, t, f in (("friendly", ft, ff), ("enemy", et, ef)):
        out[f"{name}_type"] = np.tile(t, (T, 1))
        out[f"{name}_feat"] = np.tile(f, (T, 1, 1))
    ns = 6
    out["structures"][:, :ns, STRUCT_X] = rng.random(ns)
    out["structures"][:, :ns, STRUCT_Y] = rng.random(ns)
    out["structures"][:, :ns, STRUCT_EXISTS] = 1.0
    out["vehicles"][:, :2, VEH_X] = rng.random(2)
    out["vehicles"][:, :2, VEH_Y] = rng.random(2)
    out["vehicles"][:, :2, VEH_EXISTS] = 1.0
    np.savez_compressed(os.path.join(out_dir, "ticks.npz"), **out)

    def rxy():
        return (round(float(rng.random()) * map_w, 1),
                round(float(rng.random()) * map_h, 1))

    actions = []
    for tick in range(T):
        for u in rng.choice(n_units, rng.integers(1, 5), replace=False):
            x, y = rxy()
            r = {"tick": tick, "u": int(u)}
            verb = rng.choice(["move", "move_formation", "attack_unit",
                               "attack_ground", "halt", "set_stance",
                               "set_formation", "change_shot_type",
                               "use_unit_ability"])
            r["verb"] = str(verb)
            if verb in ("move", "move_formation", "attack_ground"):
                r["x"], r["y"] = x, y
            if verb in ("move", "move_formation", "attack_unit"):
                r["run"] = int(rng.integers(2))
            if verb == "move_formation":
                r["deg"] = float(rng.integers(24) * 15)
                r["wmin"], r["wmax"] = 8.0, 90.0
                r["width_m"] = round(8.0 + float(rng.random()) * 82.0, 1)
            if verb == "attack_unit":
                r["enemy"] = int(rng.integers(n_units))
            if verb == "set_stance":
                r["stance"] = int(rng.integers(spec.STANCE_VOCAB))
                r["on"] = int(rng.integers(2))
            if verb == "set_formation":
                r["formation"] = int(rng.integers(spec.FORMATION_VOCAB))
            if verb == "change_shot_type":
                r["shot"] = int(rng.integers(spec.SHOT_VOCAB))
            if verb == "use_unit_ability":
                r["ability"] = int(rng.integers(spec.ABILITY_VOCAB))
            actions.append(r)
    with open(os.path.join(out_dir, "actions.json"), "w", encoding="utf-8") as f:
        json.dump(actions, f)


def synthetic_dataset(root: str, battles: int = 2, ticks: int = 32,
                      seed: int = 0) -> BattleDataset:
    dirs = []
    for b in range(battles):
        d = os.path.join(root, f"synthetic_{b}")
        if not os.path.isfile(os.path.join(d, "actions.json")):
            make_synthetic_battle(d, ticks=ticks, seed=seed + b)
        dirs.append(d)
    return BattleDataset(dirs, mask_fn=permissive_masks,
                         fine_mask_fn=permissive_fine_mask)
