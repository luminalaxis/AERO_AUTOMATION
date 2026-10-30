"""2D 다요소 익형 형상 생성: NACA 4자리 / 좌표 파일 + 슬롯 플랩 N개 배치.

좌표계: 메인 코드 = 1, 메인 앞전 = 원점, x는 뒷전 방향 (내부 계산은 메인 코드 기준 무차원).
점 순서는 Selig 형식(뒷전 윗면 → 앞전 → 뒷전 아랫면)으로 통일한다.

입력 변수 (길이는 main_chord와 같은 단위, 예: mm):
  - main_airfoil, main_chord
  - flapN_airfoil, flapN_chord   : N번째 플랩 단면과 코드 길이 (N = 1, 2, ...)
  - flapN_deflection             : 메인 코드선 기준 절대각 [deg], 뒷전이 내려가는 방향이 +
  - flapN_overlap                : 앞 요소 뒷전 x - 플랩 앞쪽 끝 x. +면 플랩이 앞 요소 아래로 들어감
  - flapN_gap                    : 앞 요소 뒷전(아랫면 끝점)에서 플랩 표면까지의 최소 거리
  - downforce                    : true면 다운포스용으로 전체 형상을 위아래로 뒤집는다 (거울상).
                                   플랩은 앞 요소 뒷전의 위쪽에 놓이고 갭/오버랩/각도 정의는 거울상 그대로.
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

MAX_FLAPS = 3
FLAP_FIELDS = ("airfoil", "chord", "deflection", "gap", "overlap")


def geometry_params(params: dict) -> dict:
    """케이스 변수 중 형상(메쉬)을 결정하는 것만 추린다."""
    keys = ["main_airfoil", "main_chord", "chord", "downforce"]
    keys += [f"flap{i}_{f}" for i in range(1, MAX_FLAPS + 1) for f in FLAP_FIELDS]
    return {k: params[k] for k in keys if k in params and params[k] not in ("", None)}


def n_flaps(params: dict) -> int:
    n = 0
    while n < MAX_FLAPS and params.get(f"flap{n + 1}_airfoil") not in (None, ""):
        n += 1
    return n


def _place_flap(section: Element, prev: Element, others: list[Element], ratio: float, delta: float,
                gap: float, overlap: float, name: str) -> tuple[Element, float]:
    """prev 요소의 뒷전 기준으로 플랩을 배치. (플랩, 앞선 모든 요소와의 최소 간격) 반환. 길이는 메인 코드 기준."""
    flap0 = section.transformed(scale=ratio, angle_deg=delta)
    te = prev.te_lower
    base_shift = np.array([te[0] - overlap - flap0.xy[:, 0].min(), te[1]])

    def dist(h: float) -> float:
        return float(signed_distance(te, flap0.xy + base_shift + (0.0, -h))[0])

    # 플랩을 아래로 내리며(h 증가) 앞 요소 뒷전까지 거리가 gap이 되는 위치를 찾는다.
    hs = np.linspace(-0.5 * ratio, 1.0 * ratio + gap, 300)
    ds = np.array([dist(h) for h in hs])
    i0 = int(np.argmin(ds))
    above = np.nonzero(ds[i0:] >= gap)[0]
    if ds[i0] > gap or not above.size:
        raise ConfigError(f"{name}: 주어진 overlap에서 gap을 만족하는 위치가 없습니다 "
                          f"(가능한 최소 간격 {ds[i0]:.4f} c). overlap을 줄이거나 gap을 늘리세요.")
    lo, hi = hs[i0 + above[0] - 1], hs[i0 + above[0]]
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if dist(mid) < gap else (lo, mid)
    flap = Element(name, flap0.xy + base_shift + (0.0, -0.5 * (lo + hi)), flap0.le_index)

    clearance = min(body_clearance(o.xy, flap.xy) for o in others)
    if clearance <= 0:
        raise ConfigError(f"{name}가 다른 요소와 겹칩니다 (gap/overlap/deflection 조합 확인).")
    return flap, clearance


def build_geometry(params: dict, root: Path) -> Geometry:
    """메인 + 플랩 N개(flap1, flap2, ...) 다요소 형상.

    길이(main_chord, flapN_chord, flapN_gap, flapN_overlap)는 모두 같은 단위(예: mm)로 받는다.
    main_chord가 없으면 메인 코드 = 1로 보고 나머지를 메인 코드 대비 비율로 해석한다.
    flapN_deflection은 메인 코드선 기준 절대각 [deg], 뒷전이 내려가는 방향이 +.
    flapN의 gap/overlap은 바로 앞 요소(main 또는 flap{N-1})의 뒷전 기준이다.
    """
    main = load_section(params["main_airfoil"], root, "main")
    ref = float(params.get("main_chord", 1.0))
    elements = [main]
    flaps = []
    for i in range(1, n_flaps(params) + 1):
        try:
            chord = float(params[f"flap{i}_chord"])
            gap = float(params[f"flap{i}_gap"])
        except KeyError as exc:
            raise ConfigError(f"플랩 {i} 형상에 필요한 변수 {exc}가 없습니다.") from exc
        delta = float(params.get(f"flap{i}_deflection", 0.0))
        overlap = float(params.get(f"flap{i}_overlap", 0.0))
        if not 0 < chord / ref < 1.5:
            raise ConfigError(f"flap{i}_chord 값이 이상합니다: {chord} (main_chord {ref})")
        if gap <= 0:
            raise ConfigError(f"flap{i}_gap은 0보다 커야 합니다: {gap}")
        section = load_section(params[f"flap{i}_airfoil"], root, f"flap{i}")
        flap, clearance = _place_flap(section, elements[-1], elements, chord / ref, delta,
                                      gap / ref, overlap / ref, f"flap{i}")
        elements.append(flap)
        flaps.append({"name": f"flap{i}", "airfoil": str(params[f"flap{i}_airfoil"]), "chord": chord,
                      "deflection": delta, "gap": gap, "overlap": overlap,
                      "clearance": clearance * ref,  # 앞선 요소들과의 실제 최소 간격 (입력 단위)
                      "clearance_c": clearance})     # 메인 코드 기준
    info = {"elements": len(elements), "main_chord": ref, "flaps": flaps,
            "downforce": bool(params.get("downforce"))}
    if flaps:
        info["min_clearance"] = min(f["clearance_c"] for f in flaps)
    if info["downforce"]:
        elements = [mirror(el) for el in elements]
    return Geometry(elements, info)


def mirror(el: Element) -> Element:
    """x축 기준 위아래 뒤집기. 점 순서를 뒤집어 Selig 순서(반시계, 위쪽 면 먼저)를 유지한다."""
    xy = (el.xy * (1.0, -1.0))[::-1].copy()
    return Element(el.name, xy, len(xy) - 1 - el.le_index)
