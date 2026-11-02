"""회귀 모델 학습·평가 (aero-auto train).

입력: 형상 계수(cst16 = 면당 8개 × 2, 또는 cst8 = 면당 4개 × 2) + 받음각 aoa [deg] + log10(Re)
목표: cl, log10_cd

모델
  baseline : 얇은 익형 이론 CL = 2π(α − α0) (α0는 형상의 캠버선으로 계산), log10 CD = 학습 데이터 평균
  lgbm     : LightGBM. 목표별로 평균 모델 + 분위 회귀(10%, 90%) 모델 → 80% 예측 구간
  mlp      : PyTorch 다층 퍼셉트론. 시드만 다른 n_seeds개 앙상블, 평균 = 예측, 표준편차 → 80% 예측 구간

평가 (runs/ml/<이름>/metrics.csv)
  분할(val, test, test_ood)마다 조건별 표본 수와 MAE(cl), MAE(log10_cd), 예측 구간 폭·포함률
    all          학습에 쓰는 수렴 상태(train_states)의 전체 케이스
    near_stall   형상·Re별 CL 최대 받음각 ± near_stall_window
    attached     그 밖의 받음각
    state=<상태> 수렴 상태별 (converged / oscillating / diverged)
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field

import numpy as np

from .config import ConfigError, ProjectConfig
from .dataset import MLConfig, dataset_dir, load_dataset

TARGETS = ("cl", "log10_cd")
Z80 = 1.2816  # 정규분포 80% 구간 (10%~90%)
ML_DIR = "ml"


@dataclass
class TrainOptions:
    models: list = field(default_factory=lambda: ["baseline", "lgbm", "mlp"])
    features: str = "cst16"           # cst16 / cst8
    hidden: list = field(default_factory=lambda: [64, 64])
    n_seeds: int = 5
    epochs: int = 2000
    patience: int = 100
    lr: float = 1e-3
    batch_size: int = 256
    stall_weight: float = 1.0         # 실속 부근 학습 케이스 가중치 (개선 실험)
    ood_train_fraction: float = 0.0   # test_ood 형상 중 이 비율을 학습에 추가 (개선 실험)
    name: str | None = None


def feature_columns(rows: list[dict], features: str) -> list[str]:
    shape = [k for k in rows[0] if k.startswith(f"{features}_") and k[len(features) + 1] in "ul"
             and k[len(features) + 2:].isdigit()]
    if not shape:
        raise ConfigError(f"데이터셋에 {features}_* 열이 없습니다. [ml] cst_orders를 확인하고 dataset을 다시 만드세요.")
    return shape + ["aoa", "log10_re"]


def _matrix(rows: list[dict], cols: list[str]) -> np.ndarray:
    return np.array([[r[c] for c in cols] for r in rows], dtype=float)


# ---------------------------------------------------------------- 모델

class Baseline:
    name = "baseline"

    def fit(self, train: list[dict], val: list[dict], cols: list[str], weights: np.ndarray) -> None:
        self.cd = float(np.mean([r["log10_cd"] for r in train]))

    def predict(self, rows: list[dict]) -> dict:
        a = np.radians([r["aoa"] - r["alpha0_thin_deg"] for r in rows])
        return {"cl": 2 * np.pi * a, "log10_cd": np.full(len(rows), self.cd)}


class LGBMModel:
    name = "lgbm"
    params = dict(learning_rate=0.03, num_leaves=31, min_data_in_leaf=10, bagging_fraction=0.8,
                  bagging_freq=1, feature_fraction=0.9, seed=0, verbosity=-1)
    max_rounds = 3000

    def fit(self, train, val, cols, weights) -> None:
        try:
            import lightgbm as lgb
        except ImportError as exc:
            raise ConfigError("lightgbm이 없습니다: pip install -e \".[ml]\"") from exc
        self.cols = cols
        x, xv = _matrix(train, cols), _matrix(val, cols)
        self.models = {}
        for t in TARGETS:
            y, yv = np.array([r[t] for r in train]), np.array([r[t] for r in val])
            for kind, extra in (("mean", {"objective": "regression"}),
                                ("lo", {"objective": "quantile", "alpha": 0.1}),
                                ("hi", {"objective": "quantile", "alpha": 0.9})):
                dtrain = lgb.Dataset(x, y, weight=weights, free_raw_data=False)
                dval = lgb.Dataset(xv, yv, reference=dtrain)
                self.models[(t, kind)] = lgb.train(
                    {**self.params, **extra}, dtrain, num_boost_round=self.max_rounds, valid_sets=[dval],
                    callbacks=[lgb.early_stopping(100, verbose=False)])

    def predict(self, rows):
        x = _matrix(rows, self.cols)
        out = {}
        for t in TARGETS:
            pred = {k: self.models[(t, k)].predict(x, num_iteration=self.models[(t, k)].best_iteration)
                    for k in ("mean", "lo", "hi")}
            out[t] = pred["mean"]
            out[f"{t}_lo"], out[f"{t}_hi"] = np.minimum(pred["lo"], pred["hi"]), np.maximum(pred["lo"], pred["hi"])
        return out


class MLPEnsemble:
    name = "mlp"

    def __init__(self, opts: TrainOptions):
        self.opts = opts

    def fit(self, train, val, cols, weights) -> None:
        try:
            import torch
        except ImportError as exc:
            raise ConfigError("PyTorch가 없습니다: pip install -e \".[ml]\"") from exc
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        self.cols = cols
        x, xv = _matrix(train, cols), _matrix(val, cols)
        y = np.array([[r[t] for t in TARGETS] for r in train])
        yv = np.array([[r[t] for t in TARGETS] for r in val])
        self.xm, self.xs = x.mean(0), x.std(0) + 1e-12
        self.ym, self.ys = y.mean(0), y.std(0) + 1e-12
        tx = torch.tensor((x - self.xm) / self.xs, dtype=torch.float32)
        ty = torch.tensor((y - self.ym) / self.ys, dtype=torch.float32)
        tw = torch.tensor(weights / weights.mean(), dtype=torch.float32)
        vx = torch.tensor((xv - self.xm) / self.xs, dtype=torch.float32)
        vy = torch.tensor((yv - self.ym) / self.ys, dtype=torch.float32)
        self.nets, self.history = [], []
        for seed in range(self.opts.n_seeds):
            net, best = self._train_one(torch, seed, tx, ty, tw, vx, vy)
            self.nets.append(net)
            self.history.append(best)

    def _net(self, torch, n_in: int):
        layers, width = [], n_in
        for h in self.opts.hidden:
            layers += [torch.nn.Linear(width, h), torch.nn.SiLU()]
            width = h
        layers.append(torch.nn.Linear(width, len(TARGETS)))
        return torch.nn.Sequential(*layers)

    def _train_one(self, torch, seed, tx, ty, tw, vx, vy):
        torch.manual_seed(seed)
        gen = torch.Generator().manual_seed(seed)
        net = self._net(torch, tx.shape[1])
        opt = torch.optim.Adam(net.parameters(), lr=self.opts.lr, weight_decay=1e-5)
        best, best_state, wait = math.inf, None, 0
        n = len(tx)
        for epoch in range(self.opts.epochs):
            net.train()
            perm = torch.randperm(n, generator=gen)
            for i in range(0, n, self.opts.batch_size):
                idx = perm[i:i + self.opts.batch_size]
                loss = ((net(tx[idx]) - ty[idx]) ** 2).mean(1).mul(tw[idx]).mean()
                opt.zero_grad()
                loss.backward()
                opt.step()
            net.eval()
            with torch.no_grad():
                v = float(((net(vx) - vy) ** 2).mean()) if len(vx) else float(loss)
            if v < best - 1e-6:
                best, wait = v, 0
                best_state = {k: t.clone() for k, t in net.state_dict().items()}
            else:
                wait += 1
                if wait >= self.opts.patience:
                    break
        net.load_state_dict(best_state)
        return net, {"seed": seed, "val_loss": best, "epochs": epoch + 1}

    def predict(self, rows):
        import torch

        x = torch.tensor((_matrix(rows, self.cols) - self.xm) / self.xs, dtype=torch.float32)
        with torch.no_grad():
            preds = np.stack([net(x).numpy() * self.ys + self.ym for net in self.nets])
        mean, std = preds.mean(0), preds.std(0)
        out = {}
        for k, t in enumerate(TARGETS):
            out[t] = mean[:, k]
            out[f"{t}_lo"], out[f"{t}_hi"] = mean[:, k] - Z80 * std[:, k], mean[:, k] + Z80 * std[:, k]
        return out


# ---------------------------------------------------------------- 평가

def conditions(row: dict, train_states: set[str]) -> list[str]:
    out = [f"state={row['conv_state']}"]
    if row["conv_state"] in train_states:
        out += ["all", "near_stall" if row["near_stall"] else "attached"]
    return out


def evaluate(rows: list[dict], preds: dict[str, np.ndarray], model: str, train_states: set[str]) -> list[dict]:
    groups: dict[tuple, list[int]] = {}
    for i, r in enumerate(rows):
        for cond in conditions(r, train_states):
            groups.setdefault((r["eval_split"], cond), []).append(i)
    metrics = []
    for (split, cond), idx in sorted(groups.items()):
        entry = {"model": model, "split": split, "condition": cond, "n": len(idx),
                 "n_shapes": len({rows[i]["shape"] for i in idx})}
        for t in TARGETS:
            y = np.array([rows[i][t] for i in idx])
            p = preds[t][idx]
            entry[f"mae_{t}"] = float(np.mean(np.abs(p - y)))
            if f"{t}_lo" in preds:
                lo, hi = preds[f"{t}_lo"][idx], preds[f"{t}_hi"][idx]
                entry[f"width80_{t}"] = float(np.mean(hi - lo))
                entry[f"cover80_{t}"] = float(np.mean((y >= lo) & (y <= hi)))
        metrics.append(entry)
    return metrics


def _split_rows(rows: list[dict], ml: MLConfig, opts: TrainOptions) -> dict[str, list[dict]]:
    usable = set(ml.train_states)
    labeled = [r for r in rows if r.get("cl") is not None and r.get("log10_cd") is not None]
    ood_shapes = sorted({r["shape"] for r in labeled if r["split"] == "test_ood"})
    moved: set[str] = set()
    if opts.ood_train_fraction > 0 and ood_shapes:
        rng = np.random.default_rng(ml.seed)
        k = int(round(opts.ood_train_fraction * len(ood_shapes)))
        moved = set(rng.choice(ood_shapes, size=k, replace=False).tolist())
    for r in labeled:
        r["eval_split"] = "train" if r["shape"] in moved else r["split"]
    return {
        "train": [r for r in labeled if r["eval_split"] == "train" and r["conv_state"] in usable],
        "val": [r for r in labeled if r["eval_split"] == "val" and r["conv_state"] in usable],
        "eval": [r for r in labeled if r["eval_split"] in ("val", "test", "test_ood")],
        "moved": sorted(moved),
    }


def train(cfg: ProjectConfig, opts: TrainOptions) -> dict:
    ml = MLConfig.from_dict(cfg.ml)
    rows = load_dataset(dataset_dir(cfg) / "dataset.csv")
    if not rows:
        raise ConfigError("데이터셋이 비어 있습니다 (완료된 케이스가 없음).")
    cols = feature_columns(rows, opts.features)
    parts = _split_rows(rows, ml, opts)
    train_rows, val_rows, eval_rows = parts["train"], parts["val"], parts["eval"]
    if not train_rows or not val_rows:
        raise ConfigError(f"학습/검증 케이스가 부족합니다 (train {len(train_rows)}, val {len(val_rows)}).")
    weights = np.array([opts.stall_weight if r["near_stall"] else 1.0 for r in train_rows])

    name = opts.name or f"{opts.features}_h{'x'.join(map(str, opts.hidden))}"
    out_dir = cfg.output_dir / ML_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    usable = set(ml.train_states)

    makers = {"baseline": Baseline, "lgbm": LGBMModel, "mlp": lambda: MLPEnsemble(opts)}
    unknown = set(opts.models) - set(makers)
    if unknown:
        raise ConfigError(f"알 수 없는 모델: {', '.join(sorted(unknown))} (baseline, lgbm, mlp)")
    metrics, predictions, extra = [], {}, {}
    for m in opts.models:
        model = makers[m]()
        model.fit(train_rows, val_rows, cols, weights)
        preds = model.predict(eval_rows)
        predictions[m] = preds
        metrics += evaluate(eval_rows, preds, m, usable)
        if m == "mlp":
            extra["mlp_members"] = model.history

    from .dataset import _write_csv
    _write_csv(out_dir / "metrics.csv", metrics)
    pred_rows = []
    for i, r in enumerate(eval_rows):
        row = {k: r[k] for k in ("case", "shape", "family", "eval_split", "aoa", "reynolds",
                                 "conv_state", "near_stall", "aoa_clmax", "cl", "log10_cd")}
        for m, p in predictions.items():
            for key, arr in p.items():
                row[f"{m}_{key}"] = float(arr[i])
        pred_rows.append(row)
    _write_csv(out_dir / "predictions.csv", pred_rows)
    (out_dir / "run.json").write_text(json.dumps({
        "options": asdict(opts), "features": cols, "targets": list(TARGETS),
        "n_train": len(train_rows), "n_val": len(val_rows),
        "n_train_shapes": len({r["shape"] for r in train_rows}),
        "ood_shapes_moved_to_train": parts["moved"], **extra,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"dir": out_dir, "metrics": metrics, "n_train": len(train_rows), "n_val": len(val_rows),
            "features": cols, "moved": parts["moved"]}
