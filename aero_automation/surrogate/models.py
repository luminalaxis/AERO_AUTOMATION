"""회귀 모델. 모두 같은 인터페이스:

    model.fit(train: Table, val: Table, weight: np.ndarray | None)
    model.predict(table) -> {"mean": (n, 2), "lo": (n, 2) | None, "hi": (n, 2) | None}

출력 열 순서는 TARGETS = (cl, log10cd). lo/hi는 interval(기본 90%) 예측 구간.

- ThinAirfoilBaseline : Cl = 2π(α − α0), α0는 CST 캠버선에서 계산. log10Cd = 학습 데이터 평균.
- LGBMQuantile        : 타깃별 LightGBM 3개 (점 예측 L2 + 하한/상한 분위 회귀). 검증 세트로 조기 종료.
- MLPEnsemble         : PyTorch MLP를 시드만 바꿔 5개 학습 (deep ensemble).
                        각 망은 평균과 분산을 같이 출력(가우시안 NLL). 예측 분산 = 망별 분산 평균 + 평균들의 분산.
"""

from __future__ import annotations

import inspect
import math
import os
from statistics import NormalDist

import numpy as np

from ..config import ConfigError
from .dataset import TARGETS, Table


def _targets(t: Table) -> np.ndarray:
    return t.matrix(list(TARGETS))


def _z(interval: float) -> float:
    return NormalDist().inv_cdf(0.5 + interval / 2)


class ThinAirfoilBaseline:
    name = "baseline"

    def __init__(self, features: list[str] | None = None, interval: float = 0.9):
        self.features = features or []
        self.interval = interval

    def fit(self, train: Table, val: Table | None = None, weight=None) -> "ThinAirfoilBaseline":
        self.cd_mean = float(np.mean(train["log10cd"]))
        return self

    def predict(self, t: Table) -> dict:
        cl = 2 * math.pi * np.radians(t["aoa"] - t["alpha0_deg"])
        mean = np.column_stack([cl, np.full(t.n, self.cd_mean)])
        return {"mean": mean, "lo": None, "hi": None}

    def describe(self) -> dict:
        return {"model": self.name, "log10cd_mean": self.cd_mean}


class LGBMQuantile:
    name = "lgbm"

    def __init__(self, features: list[str], interval: float = 0.9, seed: int = 0, n_estimators: int = 3000,
                 learning_rate: float = 0.03, num_leaves: int = 31, min_child_samples: int = 10,
                 early_stopping: int = 200):
        try:
            import lightgbm  # noqa: F401
        except ImportError as exc:
            raise ConfigError("LightGBM이 설치되어 있지 않습니다: pip install -e \".[ml]\"") from exc
        self.features = features
        self.interval = interval
        self.params = dict(n_estimators=n_estimators, learning_rate=learning_rate, num_leaves=num_leaves,
                           min_child_samples=min_child_samples, subsample=0.8, subsample_freq=1,
                           colsample_bytree=0.9, random_state=seed, verbose=-1,
                           n_jobs=min(4, os.cpu_count() or 1))
        self.early_stopping = early_stopping
        self.models: dict = {}

    def fit(self, train: Table, val: Table, weight=None) -> "LGBMQuantile":
        import lightgbm as lgb

        X, Y = train.matrix(self.features), _targets(train)
        Xv, Yv = val.matrix(self.features), _targets(val)
        q_lo, q_hi = 0.5 - self.interval / 2, 0.5 + self.interval / 2
        # 최신 LightGBM은 eval_X/eval_y, 이전 버전은 eval_set
        new_api = "eval_X" in inspect.signature(lgb.LGBMRegressor.fit).parameters
        for j, target in enumerate(TARGETS):
            evals = ({"eval_X": (Xv,), "eval_y": (Yv[:, j],)} if new_api else {"eval_set": [(Xv, Yv[:, j])]})
            for kind, extra in (("mean", {"objective": "regression"}),
                                ("lo", {"objective": "quantile", "alpha": q_lo}),
                                ("hi", {"objective": "quantile", "alpha": q_hi})):
                m = lgb.LGBMRegressor(**self.params, **extra)
                m.fit(X, Y[:, j], sample_weight=weight, **evals,
                      callbacks=[lgb.early_stopping(self.early_stopping, verbose=False)])
                self.models[(target, kind)] = m
        return self

    def predict(self, t: Table) -> dict:
        X = t.matrix(self.features)
        out = {k: np.column_stack([self.models[(tg, k)].predict(X) for tg in TARGETS]) for k in ("mean", "lo", "hi")}
        lo, hi = np.minimum(out["lo"], out["hi"]), np.maximum(out["lo"], out["hi"])  # 분위 교차 보정
        return {"mean": out["mean"], "lo": lo, "hi": hi}

    def describe(self) -> dict:
        return {"model": self.name, "params": self.params,
                "best_iterations": {f"{t}_{k}": int(m.best_iteration_ or m.n_estimators)
                                    for (t, k), m in self.models.items()}}


class MLPEnsemble:
    name = "mlp"

    def __init__(self, features: list[str], hidden=(128, 128), n_models: int = 5, interval: float = 0.9,
                 seed: int = 0, epochs: int = 2000, batch_size: int = 256, lr: float = 1e-3,
                 weight_decay: float = 1e-5, patience: int = 200, warmup: int = 100, threads: int = 4):
        try:
            import torch  # noqa: F401
        except ImportError as exc:
            raise ConfigError("PyTorch가 설치되어 있지 않습니다: pip install -e \".[ml]\"") from exc
        self.features = features
        self.hidden = tuple(int(h) for h in hidden)
        self.n_models = n_models
        self.interval = interval
        self.seed = seed
        self.epochs, self.batch_size, self.lr = epochs, batch_size, lr
        self.weight_decay, self.patience, self.warmup = weight_decay, patience, warmup
        self.threads = threads
        self.nets: list = []
        self.history: list[dict] = []

    def _net(self, n_in: int):
        import torch.nn as nn

        layers, prev = [], n_in
        for h in self.hidden:
            layers += [nn.Linear(prev, h), nn.SiLU()]
            prev = h
        layers.append(nn.Linear(prev, 2 * len(TARGETS)))  # 평균 2개 + log 분산 2개
        return nn.Sequential(*layers)

    def fit(self, train: Table, val: Table, weight=None) -> "MLPEnsemble":
        import torch

        torch.set_num_threads(self.threads)
        X, Y = train.matrix(self.features), _targets(train)
        self.x_mean, self.x_std = X.mean(0), X.std(0) + 1e-12
        self.y_mean, self.y_std = Y.mean(0), Y.std(0) + 1e-12
        to_t = lambda a: torch.tensor(a, dtype=torch.float32)  # noqa: E731
        Xt, Yt = to_t((X - self.x_mean) / self.x_std), to_t((Y - self.y_mean) / self.y_std)
        Xv = to_t((val.matrix(self.features) - self.x_mean) / self.x_std)
        Yv = to_t((_targets(val) - self.y_mean) / self.y_std)
        w = to_t(np.ones(len(X)) if weight is None else np.asarray(weight, float) / np.mean(weight))
        k = len(TARGETS)

        def nll(out, y, sw=None):
            mu, logvar = out[:, :k], out[:, k:].clamp(-12, 6)
            loss = 0.5 * (logvar + (y - mu) ** 2 / logvar.exp())
            loss = loss.mean(1)
            return (loss * sw).mean() if sw is not None else loss.mean()

        self.nets, self.history = [], []
        for m in range(self.n_models):
            torch.manual_seed(self.seed + m)
            gen = torch.Generator().manual_seed(self.seed + m)
            net = self._net(X.shape[1])
            opt = torch.optim.Adam(net.parameters(), lr=self.lr, weight_decay=self.weight_decay)
            best, best_state, best_epoch, wait = math.inf, None, 0, 0
            for epoch in range(self.epochs):
                net.train()
                perm = torch.randperm(len(Xt), generator=gen)
                for i in range(0, len(Xt), self.batch_size):
                    idx = perm[i:i + self.batch_size]
                    out = net(Xt[idx])
                    if epoch < self.warmup:  # 처음에는 평균만 MSE로 맞춰 분산 출력이 발산하지 않게
                        loss = (((out[:, :k] - Yt[idx]) ** 2).mean(1) * w[idx]).mean()
                    else:
                        loss = nll(out, Yt[idx], w[idx])
                    opt.zero_grad()
                    loss.backward()
                    opt.step()
                if epoch < self.warmup:
                    continue
                net.eval()
                with torch.no_grad():
                    v = float(nll(net(Xv), Yv))
                if v < best - 1e-5:
                    best, best_epoch, wait = v, epoch, 0
                    best_state = {kk: vv.clone() for kk, vv in net.state_dict().items()}
                else:
                    wait += 1
                    if wait >= self.patience:
                        break
            if best_state is not None:
                net.load_state_dict(best_state)
            net.eval()
            self.nets.append(net)
            self.history.append({"seed": self.seed + m, "best_epoch": best_epoch, "val_nll": best})
        return self

    def predict(self, t: Table) -> dict:
        import torch

        k = len(TARGETS)
        X = torch.tensor((t.matrix(self.features) - self.x_mean) / self.x_std, dtype=torch.float32)
        mus, vars_ = [], []
        with torch.no_grad():
            for net in self.nets:
                out = net(X).numpy()
                mus.append(out[:, :k] * self.y_std + self.y_mean)
                vars_.append(np.exp(np.clip(out[:, k:], -12, 6)) * self.y_std**2)
        mus, vars_ = np.array(mus), np.array(vars_)
        mean = mus.mean(0)
        sigma = np.sqrt(vars_.mean(0) + mus.var(0))
        z = _z(self.interval)
        return {"mean": mean, "lo": mean - z * sigma, "hi": mean + z * sigma, "sigma": sigma,
                "epistemic_sigma": np.sqrt(mus.var(0))}

    def describe(self) -> dict:
        return {"model": self.name, "hidden": list(self.hidden), "n_models": self.n_models,
                "lr": self.lr, "batch_size": self.batch_size, "members": self.history}


def make_model(kind: str, features: list[str], interval: float, seed: int = 0, hidden=(128, 128),
               n_models: int = 5, epochs: int | None = None):
    if kind == "baseline":
        return ThinAirfoilBaseline(features, interval)
    if kind == "lgbm":
        return LGBMQuantile(features, interval, seed)
    if kind == "mlp":
        kw = {"epochs": epochs} if epochs else {}
        return MLPEnsemble(features, hidden=hidden, n_models=n_models, interval=interval, seed=seed, **kw)
    raise ConfigError(f"모델 종류는 baseline, lgbm, mlp 중 하나입니다: {kind!r}")
