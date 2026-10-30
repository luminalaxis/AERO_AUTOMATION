"""2D 익형 형상 생성: NACA 4자리 / 좌표 파일 + 슬롯 플랩 배치.

좌표계: 메인익 시위 = 1, 앞전 = 원점, x는 뒷전 방향. 모든 길이는 메인익 시위 기준(c).
점 순서는 Selig 형식(뒷전 윗면 → 앞전 → 뒷전 아랫면)으로 통일한다.

플랩 배치 정의 (일반적인 고양력 장치 정의):
  - flap_chord      : 플랩 시위 / 메인익 시위
  - flap_deflection : 플랩 각도 [deg], 뒷전이 내려가는 방향이 +
  - overlap         : 메인익 뒷전 x - 플랩 앞전 x. +면 플랩 앞전이 메인익 뒷전보다 앞(아래)에 위치
  - gap             : 메인익 뒷전(아랫면 끝점)에서 플랩 표면까지의 최소 거리
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import ConfigError


@dataclass
class Element:
    name: str
    xy: np.ndarray  # (N, 2), Selig 순서
    le_index: int

    @property
    def sharp_te(self) -> bool:
        return bool(np.allclose(self.xy[0], self.xy[-1], atol=1e-9))

    @property
    def te_upper(self) -> np.ndarray:
        return self.xy[0]

    @property
    def te_lower(self) -> np.ndarray:
        return self.xy[-1]

    def transformed(self, scale=1.0, angle_deg=0.0, pivot=(0.0, 0.0), shift=(0.0, 0.0)) -> "Element":
        """pivot 기준 scale, 시계방향 angle_deg 회전 후 shift 만큼 이동."""
        a = np.radians(-angle_deg)
        rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        xy = (self.xy - pivot) * scale @ rot.T + pivot + shift
        return Element(self.name, xy, self.le_index)


@dataclass
class Geometry:
    elements: list[Element]
    info: dict  # 실제 gap/overlap, 최소 간격 등


# ---------------------------------------------------------------- 단면 생성

def naca4(code: str, n: int = 160, closed_te: bool = False) -> np.ndarray:
    """NACA 4자리 좌표 (Selig 순서). closed_te=False면 표준 식(뭉툭한 뒷전)."""
    if not re.fullmatch(r"\d{4}", code):
        raise ConfigError(f"NACA 4자리 코드가 아닙니다: {code!r}")
    m, p, t = int(code[0]) / 100, int(code[1]) / 10, int(code[2:]) / 100
    beta = np.linspace(0, np.pi, n)
    x = 0.5 * (1 - np.cos(beta))  # 앞전/뒷전 집중 분포
    a4 = -0.1036 if closed_te else -0.1015
    yt = 5 * t * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 + a4 * x**4)
    if m > 0 and p > 0:
        yc = np.where(x < p, m / p**2 * (2 * p * x - x**2), m / (1 - p) ** 2 * (1 - 2 * p + 2 * p * x - x**2))
        dyc = np.where(x < p, 2 * m / p**2 * (p - x), 2 * m / (1 - p) ** 2 * (p - x))
    else:
        yc = np.zeros_like(x)
        dyc = np.zeros_like(x)
    th = np.arctan(dyc)
    upper = np.column_stack([x - yt * np.sin(th), yc + yt * np.cos(th)])
    lower = np.column_stack([x + yt * np.sin(th), yc - yt * np.cos(th)])
    xy = np.vstack([upper[::-1], lower[1:]])
    if closed_te:
        xy[-1] = xy[0]
    return xy


def read_airfoil_file(path: Path) -> np.ndarray:
    """Selig 또는 Lednicer 형식 .dat 파일."""
    if not path.is_file():
        raise ConfigError(f"익형 좌표 파일을 찾을 수 없습니다: {path}")
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.replace(",", " ").split()
        if len(parts) < 2:
            continue
        try:
            rows.append([float(parts[0]), float(parts[1])])
        except ValueError:
            continue
    if len(rows) < 10:
        raise ConfigError(f"좌표 점이 너무 적습니다: {path}")
    pts = np.array(rows)
    if pts[0, 0] > 1.5 and pts[0, 1] > 1.5:  # Lednicer: 첫 줄이 (윗면 점 수, 아랫면 점 수)
        nu = int(pts[0, 0])
        upper, lower = pts[1:1 + nu], pts[1 + nu:]
        pts = np.vstack([upper[::-1], lower[1:] if np.allclose(lower[0], upper[0]) else lower])
    return pts


def normalize(xy: np.ndarray) -> tuple[np.ndarray, int]:
    """앞전을 원점, 시위를 1, 시위선을 x축에 맞춘다. (좌표, 앞전 인덱스) 반환."""
    te = 0.5 * (xy[0] + xy[-1])
    le_idx = int(np.argmax(np.linalg.norm(xy - te, axis=1)))
    le = xy[le_idx]
    chord_vec = te - le
    chord = np.linalg.norm(chord_vec)
    ang = np.arctan2(chord_vec[1], chord_vec[0])
    rot = np.array([[np.cos(-ang), -np.sin(-ang)], [np.sin(-ang), np.cos(-ang)]])
    out = (xy - le) @ rot.T / chord
    # Selig 순서(윗면 먼저)가 아니면 뒤집기
    if out[: le_idx + 1, 1].mean() < out[le_idx:, 1].mean():
        out = out[::-1].copy()
        le_idx = len(out) - 1 - le_idx
    # 중복 점 제거 (뒷전 닫힘 점은 유지)
    keep = np.ones(len(out), bool)
    keep[1:] = np.linalg.norm(np.diff(out, axis=0), axis=1) > 1e-9
    keep[-1] = True
    le_idx = int(keep[:le_idx].sum())
    return out[keep], le_idx


def load_section(spec, root: Path, name: str) -> Element:
    spec = str(spec).strip()
    code = spec.upper().removeprefix("NACA").strip()
    xy = naca4(code) if re.fullmatch(r"\d{4}", code) else read_airfoil_file((root / spec).resolve())
    xy, le_idx = normalize(xy)
    return Element(name, xy, le_idx)


# ---------------------------------------------------------------- 거리 계산

def _point_segments_distance(p: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """점들(M,2)에서 폴리라인 poly(N,2) 선분들까지의 최소 거리 (M,)."""
    a, b = poly[:-1], poly[1:]
    ab = b - a
    denom = np.maximum((ab**2).sum(1), 1e-30)
    ap = p[:, None, :] - a[None]
    t = np.clip((ap * ab[None]).sum(2) / denom[None], 0, 1)
    closest = a[None] + t[..., None] * ab[None]
    return np.linalg.norm(p[:, None, :] - closest, axis=2).min(1)


def _closed(poly: np.ndarray) -> np.ndarray:
    return poly if np.allclose(poly[0], poly[-1]) else np.vstack([poly, poly[:1]])


def _inside(p: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """점들(M,2)이 닫힌 다각형 내부인지 (ray casting)."""
    poly = _closed(poly)
    x, y = p[:, 0][:, None], p[:, 1][:, None]
    x1, y1, x2, y2 = poly[:-1, 0], poly[:-1, 1], poly[1:, 0], poly[1:, 1]
    cond = (y1 > y) != (y2 > y)
    xint = x1 + (y - y1) * (x2 - x1) / np.where(y2 == y1, 1e-30, y2 - y1)
    return ((cond & (x < xint)).sum(1) % 2) == 1


def signed_distance(p: np.ndarray, poly: np.ndarray) -> np.ndarray:
    d = _point_segments_distance(np.atleast_2d(p), _closed(poly))
    return np.where(_inside(np.atleast_2d(p), poly), -d, d)


def body_clearance(a: np.ndarray, b: np.ndarray) -> float:
    """두 물체 사이 최소 거리. 겹치면 음수."""
    if _inside(a, b).any() or _inside(b, a).any():
        return -1.0
    return float(min(_point_segments_distance(a, _closed(b)).min(),
                     _point_segments_distance(b, _closed(a)).min()))


# ---------------------------------------------------------------- 형상 조립

def build_geometry(params: dict, root: Path) -> Geometry:
    main = load_section(params["main_airfoil"], root, "main")
    flap_spec = params.get("flap_airfoil")
    if not flap_spec:
        return Geometry([main], {"elements": 1})

    try:
        c_f = float(params["flap_chord"])
        delta = float(params.get("flap_deflection", 0.0))
        gap = float(params["gap"])
        overlap = float(params.get("overlap", 0.0))
    except KeyError as exc:
        raise ConfigError(f"플랩 형상에 필요한 변수 {exc}가 없습니다.") from exc
    if not (0 < c_f < 1.5):
        raise ConfigError(f"flap_chord 값이 이상합니다: {c_f}")
    if gap <= 0:
        raise ConfigError(f"gap은 0보다 커야 합니다: {gap}")

    flap0 = load_section(flap_spec, root, "flap").transformed(scale=c_f, angle_deg=delta)
    te = main.te_lower
    x_le = te[0] - overlap
    base_shift = np.array([x_le - flap0.xy[:, 0].min(), te[1]])

    def dist(h: float) -> float:
        xy = flap0.xy + base_shift + (0.0, -h)
        return float(signed_distance(te, xy)[0])

    # 플랩을 아래로 내리며(h 증가) 메인익 뒷전까지 거리가 gap이 되는 위치를 찾는다.
    hs = np.linspace(-0.5 * c_f, 1.0 * c_f + gap, 1500)
    ds = np.array([dist(h) for h in hs])
    i0 = int(np.argmin(ds))
    above = np.nonzero(ds[i0:] >= gap)[0]
    if ds[i0] > gap or not above.size:
        raise ConfigError(
            f"gap={gap}를 만족하는 플랩 위치가 없습니다 (overlap={overlap}일 때 가능한 최소 간격 {ds[i0]:.4f}). "
            "overlap을 줄이거나 gap을 늘리세요."
        )
    lo, hi = hs[i0 + above[0] - 1], hs[i0 + above[0]]
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if dist(mid) < gap else (lo, mid)
    h = 0.5 * (lo + hi)
    flap = Element("flap", flap0.xy + base_shift + (0.0, -h), flap0.le_index)

    clearance = body_clearance(main.xy, flap.xy)
    if clearance <= 0:
        raise ConfigError(
            f"메인익과 플랩이 겹칩니다 (gap={gap}, overlap={overlap}, deflection={delta}). "
            "overlap을 줄이거나 gap을 늘리세요."
        )
    info = {
        "elements": 2,
        "gap": gap,
        "overlap": overlap,
        "flap_chord": c_f,
        "flap_deflection": delta,
        "min_clearance": clearance,  # 두 물체 사이 실제 최소 거리 (메인익 아랫면이 더 가까울 수 있음)
        "flap_le": flap.xy[flap.le_index].tolist(),
    }
    return Geometry([main, flap], info)
