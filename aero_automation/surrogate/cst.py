"""CST(Class-Shape Transformation, Kulfan 2008) 형상 계수.

    y(x) = C(x) · S(x) + x · Δz_te,   C(x) = √x (1 − x),   S(x) = Σ_i A_i · K_i x^i (1 − x)^(n−i)

윗면과 아랫면에 각각 n+1개 계수를 둔다. 기획서 기본은 면당 8개(Bernstein 7차) = 16개,
실험 1의 축소 형상은 면당 4개(3차) = 8개를 다시 피팅한다.
뒷전 두께 Δz_te는 좌표의 뒷전 위치로 고정하고 특징량에는 넣지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb
from pathlib import Path

import numpy as np

from ..geometry import load_section


@dataclass
class CSTFit:
    upper: np.ndarray   # 윗면 계수 (n+1,)
    lower: np.ndarray   # 아랫면 계수 (n+1,)
    te_upper: float     # 뒷전 윗면 y (Δz_te 윗면)
    te_lower: float     # 뒷전 아랫면 y
    rms: float          # 피팅 오차 RMS [코드 기준]

    @property
    def coefficients(self) -> np.ndarray:
        return np.concatenate([self.upper, self.lower])

    def surface(self, x: np.ndarray, side: str) -> np.ndarray:
        a, te = (self.upper, self.te_upper) if side == "upper" else (self.lower, self.te_lower)
        return _basis(x, len(a) - 1) @ a + x * te

    def camber(self, x: np.ndarray) -> np.ndarray:
        return 0.5 * (self.surface(x, "upper") + self.surface(x, "lower"))

    def thickness(self, x: np.ndarray) -> np.ndarray:
        return self.surface(x, "upper") - self.surface(x, "lower")


def _basis(x: np.ndarray, order: int) -> np.ndarray:
    """(len(x), order+1) 행렬: C(x) · K_i x^i (1−x)^(order−i)."""
    x = np.clip(np.asarray(x, float), 0.0, 1.0)
    cls = np.sqrt(x) * (1.0 - x)
    cols = [comb(order, i) * x**i * (1.0 - x) ** (order - i) for i in range(order + 1)]
    return cls[:, None] * np.column_stack(cols)


def split_surfaces(xy: np.ndarray, le_index: int) -> tuple[np.ndarray, np.ndarray]:
    """Selig 순서 정규화 좌표 → (윗면, 아랫면), 각각 앞전 → 뒷전 순."""
    return xy[: le_index + 1][::-1], xy[le_index:]


def fit_cst(xy: np.ndarray, le_index: int, n_per_side: int = 8) -> CSTFit:
    """정규화 좌표(앞전 원점, 코드 1)에 CST 계수를 최소제곱으로 맞춘다."""
    order = n_per_side - 1
    upper, lower = split_surfaces(xy, le_index)
    coefs, tes, resid = [], [], []
    for pts in (upper, lower):
        x, y = pts[:, 0], pts[:, 1]
        te = float(y[np.argmax(x)])
        A = _basis(x, order)
        a, *_ = np.linalg.lstsq(A, y - x * te, rcond=None)
        coefs.append(a)
        tes.append(te)
        resid.append(A @ a + x * te - y)
    rms = float(np.sqrt(np.mean(np.concatenate(resid) ** 2)))
    return CSTFit(coefs[0], coefs[1], tes[0], tes[1], rms)


def zero_lift_angle(fit: CSTFit, n: int = 4000) -> float:
    """얇은 익형 이론의 무양력 받음각 α0 [deg].

    α0 = −(1/π) ∫₀^π (dy_c/dx)(cos θ − 1) dθ,  x = (1 − cos θ)/2
    """
    theta = (np.arange(n) + 0.5) * np.pi / n            # 중점 규칙 (양 끝 특이점 회피)
    x = 0.5 * (1 - np.cos(theta))
    h = 1e-6
    dyc = (fit.camber(np.clip(x + h, 0, 1)) - fit.camber(np.clip(x - h, 0, 1))) / (
        np.clip(x + h, 0, 1) - np.clip(x - h, 0, 1))
    alpha0 = -np.sum(dyc * (np.cos(theta) - 1)) * (np.pi / n) / np.pi
    return float(np.degrees(alpha0))


def shape_features(spec: str, root: Path, n_sets: tuple[int, ...] = (8, 4)) -> dict:
    """익형 하나의 CST 계수(면당 개수별)와 요약 지표.

    반환 키: cst{2n}_u0.., cst{2n}_l0.. (예: cst16_u0..u7, cst16_l0..l7, cst8_u0..u3, cst8_l0..l3),
    cst{2n}_rms, max_camber, x_max_camber, max_thickness, alpha0_deg
    """
    el = load_section(spec, root, "main")
    out: dict = {}
    fits = {}
    for n in n_sets:
        fit = fit_cst(el.xy, el.le_index, n)
        fits[n] = fit
        tag = f"cst{2 * n}"
        out.update({f"{tag}_u{i}": float(v) for i, v in enumerate(fit.upper)})
        out.update({f"{tag}_l{i}": float(v) for i, v in enumerate(fit.lower)})
        out[f"{tag}_rms"] = fit.rms
    ref = fits[max(n_sets)]
    xs = 0.5 * (1 - np.cos(np.linspace(0, np.pi, 401)))
    camber = ref.camber(xs)
    i = int(np.argmax(np.abs(camber)))
    out["max_camber"] = float(camber[i])
    out["x_max_camber"] = float(xs[i])
    out["max_thickness"] = float(ref.thickness(xs).max())
    out["alpha0_deg"] = zero_lift_angle(ref)
    return out


def feature_names(n_coef: int) -> list[str]:
    """CST 특징량 열 이름. n_coef = 전체 계수 수 (16 또는 8)."""
    half = n_coef // 2
    tag = f"cst{n_coef}"
    return [f"{tag}_u{i}" for i in range(half)] + [f"{tag}_l{i}" for i in range(half)]
