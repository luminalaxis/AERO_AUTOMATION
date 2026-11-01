"""평가: MAE, 형상 단위 부트스트랩 95% 신뢰구간, 예측 구간 포함률, 조건별 성능, 경계 사례 후보, 그래프."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .dataset import TARGETS

LABELS = {"cl": "Cl", "log10cd": "log10(Cd)"}


def shape_bootstrap(values: np.ndarray, shapes: np.ndarray, n_boot: int, rng: np.random.Generator):
    """형상을 복원 추출해 평균의 95% 신뢰구간을 구한다 (같은 형상의 케이스끼리는 상관이 있으므로).

    반환: (평균, 하한, 상한)
    """
    values = np.asarray(values, float)
    if len(values) == 0:
        return float("nan"), float("nan"), float("nan")
    uniq, inv = np.unique(shapes, return_inverse=True)
    sums = np.bincount(inv, weights=values)
    counts = np.bincount(inv).astype(float)
    if len(uniq) < 2 or n_boot <= 0:
        return float(values.mean()), float("nan"), float("nan")
    idx = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
    boot = sums[idx].sum(1) / counts[idx].sum(1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return float(values.mean()), float(lo), float(hi)


def metrics(pred: dict, mask: np.ndarray, n_boot: int, seed: int = 0) -> dict:
    """pred: {"y": (n,2), "mean": (n,2), "lo", "hi", "airfoil": (n,)}."""
    rng = np.random.default_rng(seed)
    shapes = pred["airfoil"][mask]
    out = {"n": int(mask.sum()), "n_shapes": int(len(set(shapes)))}
    if not out["n"]:
        return out
    for j, t in enumerate(TARGETS):
        err = np.abs(pred["mean"][mask, j] - pred["y"][mask, j])
        m, lo, hi = shape_bootstrap(err, shapes, n_boot, rng)
        out[f"{t}_mae"], out[f"{t}_mae_ci"] = m, [lo, hi]
        out[f"{t}_rmse"] = float(np.sqrt(np.mean(err**2)))
        if pred.get("lo") is not None:
            y = pred["y"][mask, j]
            inside = ((y >= pred["lo"][mask, j]) & (y <= pred["hi"][mask, j])).astype(float)
            m, lo, hi = shape_bootstrap(inside, shapes, n_boot, rng)
            out[f"{t}_coverage"], out[f"{t}_coverage_ci"] = m, [lo, hi]
            out[f"{t}_pi_width"] = float(np.mean(pred["hi"][mask, j] - pred["lo"][mask, j]))
    return out


def conformal_offsets(y: np.ndarray, lo: np.ndarray, hi: np.ndarray, interval: float) -> np.ndarray:
    """분할 등각(split conformal, CQR) 보정량. 검증 세트에서 구간 밖으로 벗어난 정도의 분위수.

    보정 구간 = [lo − q, hi + q]. q < 0 이면 구간을 좁힌다. 반환: 타깃별 q (k,)
    """
    n = len(y)
    score = np.maximum(lo - y, y - hi)
    level = min(1.0, np.ceil((n + 1) * interval) / n)
    return np.quantile(score, level, axis=0, method="higher")


def aoa_band(aoa: np.ndarray) -> np.ndarray:
    """입력만으로 정할 수 있는 받음각 구간 (CFD 전에 판별 가능)."""
    return np.where(aoa < 0, "a<0", np.where(aoa <= 10, "0<=a<=10", "a>10"))


def width_quartile(pred: dict) -> np.ndarray | None:
    """Cl 예측 구간 폭의 사분위 (모델 스스로의 불확실성 표시, CFD 전에 판별 가능)."""
    if pred.get("lo") is None:
        return None
    w = pred["hi"][:, 0] - pred["lo"][:, 0]
    q = np.quantile(w, [0.25, 0.5, 0.75])
    return np.array([f"Q{int(np.searchsorted(q, x, side='right')) + 1}" for x in w], object)


def condition_table(pred: dict, sets: dict[str, np.ndarray], n_boot: int, seed: int = 0) -> list[dict]:
    """세트(test, ood_holdout 등) × 조건 변수 × 값별 성능과 표본 수.

    조건 변수:
      regime_truth (조건 ①, 정답 필요 → 평가 전용), aoa_band, reynolds, source (CFD 전 판별 가능),
      pi_width (예측 구간 폭 사분위, 모델 자체 신호 → CFD 전 판별 가능)
    """
    conds = {"regime_truth": pred["regime_truth"], "aoa_band": aoa_band(pred["aoa"]),
             "reynolds": np.array([f"{r:.3g}" for r in pred["reynolds"]], object), "source": pred["source"]}
    wq = width_quartile(pred)
    if wq is not None:
        conds["pi_width"] = wq
    rows = []
    for set_name, smask in sets.items():
        for cname, values in conds.items():
            for v in sorted(set(values[smask])):
                mask = smask & (values == v)
                m = metrics(pred, mask, n_boot, seed)
                rows.append({"set": set_name, "condition": cname, "value": v, **_flat(m)})
    return rows


def _flat(m: dict) -> dict:
    out = {}
    for k, v in m.items():
        if isinstance(v, list):
            out[f"{k}_lo"], out[f"{k}_hi"] = v
        else:
            out[k] = v
    return out


def boundary_candidates(pred: dict, mask: np.ndarray, turb: dict | None = None, per_kind: int = 4) -> list[dict]:
    """경계 사례 분석(총 10개 고르기)용 후보. 실속 부근 최악/최선, 난류 모델 민감, 전체 최악/최선."""
    idx = np.nonzero(mask)[0]
    err = np.abs(pred["mean"][:, 0] - pred["y"][:, 0])
    near = idx[np.isin(pred["regime_truth"][idx], ["near_stall", "post_stall"])]
    picks: list[tuple[str, int]] = []
    order = near[np.argsort(-err[near])]
    picks += [("stall_worst", i) for i in order[:per_kind]]
    picks += [("stall_best", i) for i in order[::-1][:per_kind]]
    order = idx[np.argsort(-err[idx])]
    picks += [("overall_worst", i) for i in order[:per_kind]]
    picks += [("overall_best", i) for i in order[::-1][:per_kind]]
    if turb:
        sens = [i for i in idx if pred["case"][i] in turb]
        sens.sort(key=lambda i: -abs(turb[pred["case"][i]]["dcl"]))
        picks += [("turbulence_sensitive", i) for i in sens[:per_kind]]
    rows, seen = [], set()
    for kind, i in picks:
        if (kind, i) in seen:
            continue
        seen.add((kind, i))
        r = {"kind": kind, "case": pred["case"][i], "set": pred["set"][i], "airfoil": pred["airfoil"][i],
             "reynolds": pred["reynolds"][i], "aoa": pred["aoa"][i], "regime_truth": pred["regime_truth"][i],
             "cl_true": pred["y"][i, 0], "cl_pred": pred["mean"][i, 0], "cl_abs_err": err[i],
             "log10cd_true": pred["y"][i, 1], "log10cd_pred": pred["mean"][i, 1]}
        if pred.get("lo") is not None:
            r["cl_lo"], r["cl_hi"] = pred["lo"][i, 0], pred["hi"][i, 0]
        if turb and pred["case"][i] in turb:
            r["cl_compare_model"] = turb[pred["case"][i]]["cl_compare"]
        rows.append(r)
    return rows


# ---------------------------------------------------------------- 그래프

def plot_parity(pred: dict, sets: dict[str, np.ndarray], path: Path, title: str = "") -> Path:
    from ..plots import _save, plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 5))
    colors = {"test": "tab:blue", "ood": "tab:red", "ood_holdout": "tab:red", "val": "tab:gray"}
    for j, (ax, t) in enumerate(zip(axes, TARGETS)):
        lim = [np.inf, -np.inf]
        for name, mask in sets.items():
            if not mask.any() or (name == "ood" and "ood_holdout" in sets):
                continue
            y, p = pred["y"][mask, j], pred["mean"][mask, j]
            ax.scatter(y, p, s=6, alpha=0.5, color=colors.get(name), label=f"{name} (n={mask.sum()})")
            lim = [min(lim[0], y.min(), p.min()), max(lim[1], y.max(), p.max())]
        if np.isfinite(lim).all():
            ax.plot(lim, lim, "k--", lw=1)
        ax.set_xlabel(f"Fluent {LABELS[t]}")
        ax.set_ylabel(f"predicted {LABELS[t]}")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle(title, fontsize=10)
    return _save(fig, path)


def plot_polars(pred: dict, mask: np.ndarray, path: Path, max_shapes: int = 6, title: str = "") -> Path | None:
    """세트 안 형상 몇 개의 Cl-α, log10Cd-α (정답 vs 예측 + 예측 구간)."""
    from ..plots import _save, plt

    shapes = sorted(set(pred["airfoil"][mask]))[:max_shapes]
    if not shapes:
        return None
    fig, axes = plt.subplots(2, len(shapes), figsize=(3.2 * len(shapes), 6), squeeze=False)
    for c, shape in enumerate(shapes):
        for res in sorted(set(pred["reynolds"][mask & (pred["airfoil"] == shape)])):
            m = mask & (pred["airfoil"] == shape) & (pred["reynolds"] == res)
            order = np.argsort(pred["aoa"][m])
            a = pred["aoa"][m][order]
            for j in range(2):
                ax = axes[j, c]
                line, = ax.plot(a, pred["mean"][m, j][order], "-", lw=1.2, label=f"Re {res:.2g} pred")
                ax.plot(a, pred["y"][m, j][order], "o", ms=3, color=line.get_color(), label=f"Re {res:.2g} CFD")
                if pred.get("lo") is not None:
                    ax.fill_between(a, pred["lo"][m, j][order], pred["hi"][m, j][order],
                                    color=line.get_color(), alpha=0.15)
        axes[0, c].set_title(shape, fontsize=9)
        axes[1, c].set_xlabel("aoa [deg]")
        for j in range(2):
            axes[j, c].grid(alpha=0.3)
    axes[0, 0].set_ylabel("Cl")
    axes[1, 0].set_ylabel("log10(Cd)")
    axes[0, 0].legend(fontsize=6)
    fig.suptitle(title, fontsize=10)
    return _save(fig, path)


def turbulence_index(rows: list[dict]) -> dict:
    """turbulence_comparison.csv 행 → {케이스 이름: 행}."""
    return {r["case"]: r for r in rows}
