"""ml/host/train.py -- behavioral cloning, teacher-forced (ML_DESIGN training spec).

Stateless BC on shuffled ticks: every batch is one backward pass through
all heads + encoder, per-head losses weighted by TrainKnobs, no_change
down-weighted, masks applied, mask-violating labels counted and skipped
(the mask self-test law -- a nonzero count on real data is a masks.legal
bug, never noise to ignore). Checkpoints carry config + spec version and
refuse to resume across a spec drift.

Real battle shards come from the future extraction pipeline (format in
data.py); until then --synthetic N fabricates N label-consistent battles
so the whole loop runs end to end:

  py train.py --synthetic 2 --epochs 2 --d-model 64 --layers 2   # smoke
  py train.py --data <shard root> --out <run dir>                # real

Runs from ml/host/ (bare imports, house style). Outputs land under
ml/runs/<name>/ -- ckpt_latest.pt every epoch + metrics.jsonl.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import time

import torch
from torch.utils.data import DataLoader

import data as data_mod
import spec
from net import BattlePolicy, NetConfig, TrainKnobs

ML_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data", default=os.path.join(ML_DIR, "data", "battles"),
                   help="root holding one subdir per battle shard")
    p.add_argument("--synthetic", type=int, default=0, metavar="N",
                   help="fabricate N synthetic battles under --data instead")
    p.add_argument("--out", default=None, help="run dir (default ml/runs/<stamp>)")
    p.add_argument("--resume", default=None, help="checkpoint to resume from")
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--wd", type=float, default=0.01)
    p.add_argument("--clip", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--val-battles", type=int, default=0,
                   help="hold out the last N battle dirs for validation")
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--log-every", type=int, default=10, metavar="STEPS")
    # architecture overrides (defaults = NetConfig = the spec's numbers)
    p.add_argument("--d-model", type=int, default=None)
    p.add_argument("--layers", type=int, default=None)
    p.add_argument("--heads", type=int, default=None)
    # loss knobs
    p.add_argument("--no-change-weight", type=float, default=None)
    p.add_argument("--sigma", type=float, default=None,
                   help="stage-B Gaussian label-smoothing sigma (px)")
    return p.parse_args()


def build_datasets(args) -> tuple:
    if args.synthetic:
        ds = data_mod.synthetic_dataset(args.data, battles=args.synthetic,
                                        seed=args.seed)
        dirs = ds.dirs
    else:
        dirs = sorted(os.path.join(args.data, d) for d in os.listdir(args.data)
                      if os.path.isfile(os.path.join(args.data, d, "actions.json")))
        if not dirs:
            raise SystemExit(f"no battle shards under {args.data} "
                             "(need <battle>/static.npz + ticks.npz + actions.json)")
        ds = None
    n_val = min(args.val_battles, len(dirs) - 1)
    tr_dirs, va_dirs = dirs[:len(dirs) - n_val or None], dirs[len(dirs) - n_val:]
    if args.synthetic:
        mk = lambda dd: data_mod.BattleDataset(  # noqa: E731
            dd, mask_fn=data_mod.permissive_masks,
            fine_mask_fn=data_mod.permissive_fine_mask)
    else:
        mk = data_mod.BattleDataset  # masks.legal, the real one
    return mk(tr_dirs), (mk(va_dirs) if n_val else None)


def run_epoch(policy, loader, knobs, device, optim=None, clip=1.0,
              log=None, log_every=10, step0=0) -> tuple[dict, int]:
    training = optim is not None
    policy.train(training)
    agg: dict[str, float] = {}
    n_batches, step = 0, step0
    for batch in loader:
        obs, labels, masks = data_mod.collate(batch, device=device)
        with torch.set_grad_enabled(training):
            total, metrics = policy.losses(obs, labels, masks, knobs)
        if training:
            optim.zero_grad(set_to_none=True)
            total.backward()
            torch.nn.utils.clip_grad_norm_(policy.parameters(), clip)
            optim.step()
            step += 1
        for k, v in metrics.items():
            agg[k] = agg.get(k, 0.0) + float(v)
        n_batches += 1
        if training and log and step % log_every == 0:
            line = {"step": step, **{k: round(float(v), 5) for k, v in metrics.items()}}
            log.write(json.dumps(line) + "\n")
            log.flush()
            print(f"  step {step:5d}  total {metrics['loss/total']:.4f}  "
                  f"verb {metrics.get('loss/verb', 0):.4f}  "
                  f"violations {metrics['mask_violations']}")
    return {k: v / max(n_batches, 1) for k, v in agg.items()}, step


def main() -> None:
    args = parse_args()
    torch.manual_seed(args.seed)
    out = args.out or os.path.join(ML_DIR, "runs", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)

    cfg = NetConfig()
    for flag, fld in (("d_model", "d_model"), ("layers", "n_layers"),
                      ("heads", "n_heads")):
        if getattr(args, flag) is not None:
            setattr(cfg, fld, getattr(args, flag))
    knobs = TrainKnobs()
    if args.no_change_weight is not None:
        knobs.no_change_weight = args.no_change_weight
    if args.sigma is not None:
        knobs.loc_smooth_sigma = args.sigma

    policy = BattlePolicy(cfg).to(args.device)
    optim = torch.optim.AdamW(policy.parameters(), lr=args.lr,
                              weight_decay=args.wd)
    epoch0, step = 0, 0
    if args.resume:
        ck = torch.load(args.resume, map_location=args.device, weights_only=False)
        if ck["spec_version"] != spec.SPEC_VERSION:
            raise SystemExit(f"checkpoint is spec {ck['spec_version']}, "
                             f"mirror is {spec.SPEC_VERSION} -- retrain, not resume")
        policy.load_state_dict(ck["model"])
        optim.load_state_dict(ck["optim"])
        epoch0, step = ck["epoch"] + 1, ck["step"]

    train_ds, val_ds = build_datasets(args)
    n_params = sum(p.numel() for p in policy.parameters())
    print(f"[train] {len(train_ds)} samples"
          + (f" + {len(val_ds)} val" if val_ds else "")
          + f", {n_params / 1e6:.1f}M params, device {args.device}, out {out}")

    def loader(ds):  # shuffled ticks per the stateless-BC spec
        return DataLoader(ds, batch_size=args.batch, shuffle=True,
                          collate_fn=lambda b: b, num_workers=0)

    with open(os.path.join(out, "metrics.jsonl"), "a", encoding="utf-8") as log:
        for epoch in range(epoch0, args.epochs):
            t0 = time.time()
            tr, step = run_epoch(policy, loader(train_ds), knobs, args.device,
                                 optim=optim, clip=args.clip, log=log,
                                 log_every=args.log_every, step0=step)
            msg = (f"epoch {epoch:3d}  train {tr['loss/total']:.4f}  "
                   f"violations {tr['mask_violations']:.1f}/batch  "
                   f"{time.time() - t0:.1f}s")
            if val_ds:
                va, _ = run_epoch(policy, loader(val_ds), knobs, args.device)
                msg += f"  val {va['loss/total']:.4f}"
            print(msg)
            log.write(json.dumps({"epoch": epoch, **{f"train/{k}": v for k, v in tr.items()}}) + "\n")
            torch.save({
                "model": policy.state_dict(), "optim": optim.state_dict(),
                "cfg": dataclasses.asdict(cfg), "knobs": dataclasses.asdict(knobs),
                "epoch": epoch, "step": step,
                "spec_version": spec.SPEC_VERSION,
            }, os.path.join(out, "ckpt_latest.pt"))
    print(f"[train] done -- {os.path.join(out, 'ckpt_latest.pt')}")


if __name__ == "__main__":
    main()
