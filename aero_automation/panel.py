"""다요소 익형 저충실도 공력 해석 (CFD 전 스크리닝용).

- 비점성: Hess-Smith 패널법 (패널별 일정 소스 + 물체별 일정 와류, 물체마다 Kutta 조건)
- 점성 근사: 적분 경계층 (Thwaites 층류 → Michel 천이 → Head 난류), Squire-Young 항력
- 압축성: Prandtl-Glauert 보정

경계층-비점성 상호작용, 슬롯의 합류 경계층(confluent BL)은 고려하지 않으므로
절대값보다 형상 간 경향 비교용이다. 모든 길이는 메인익 시위 기준.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .geometry import Geometry


# ---------------------------------------------------------------- 패널 분할

def _cosine_resample(xy: np.ndarray, n: int) -> np.ndarray:
    """곡선을 호길이 기준 코사인 분포(양 끝 집중)로 n+1 점 재분할."""
    seg = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    t = 0.5 * (1 - np.cos(np.linspace(0, np.pi, n + 1))) * s[-1]
    return np.column_stack([np.interp(t, s, xy[:, 0]), np.interp(t, s, xy[:, 1])])


def panelize(el, n_side: int) -> np.ndarray:
    """Selig 순서 단면 → 시계방향(뒷전→아랫면→앞전→윗면→뒷전) 노드."""
    upper = _cosine_resample(el.xy[: el.le_index + 1], n_side)      # 뒷전 윗면 → 앞전
    lower = _cosine_resample(el.xy[el.le_index:], n_side)           # 앞전 → 뒷전 아랫면
    return np.vstack([lower[::-1], upper[::-1][1:]])


# ---------------------------------------------------------------- Hess-Smith

def _influence(nodes: np.ndarray, pts: np.ndarray, self_start: int):
    """단위 세기 소스/와류 패널이 pts에 만드는 전역 속도 (u, v). 반환 shape (M, N).

    pts[self_start + j]는 패널 j 자신의 중점이다.
    """
    z1 = nodes[:-1, 0] + 1j * nodes[:-1, 1]
    z2 = nodes[1:, 0] + 1j * nodes[1:, 1]
    L = np.abs(z2 - z1)
    rot = (z2 - z1) / L                       # e^{iθ}
    zp = pts[:, 0] + 1j * pts[:, 1]
    zl = (zp[:, None] - z1[None]) / rot[None]  # 패널 국소 좌표
    lg = np.log(zl) - np.log(zl - L[None])     # ln(z/(z-L))
    # 자기 패널 중점: 반올림 오차로 로그 분기가 뒤집히지 않도록 해석값(바깥쪽 극한 -iπ)으로 고정
    j = np.arange(len(L))
    lg[self_start + j, j] = -1j * np.pi
    w_src = lg / (2 * np.pi) / rot[None]       # u - iv (전역)
    w_vor = -1j * lg / (2 * np.pi) / rot[None]  # 반시계 와류
    return (w_src.real, -w_src.imag), (w_vor.real, -w_vor.imag)


@dataclass
class InviscidSolution:
    bodies: list[slice]          # 물체별 패널 범위
    nodes: list[np.ndarray]      # 물체별 노드
    mid: np.ndarray              # 패널 중점
    tangent: np.ndarray
    normal: np.ndarray
    length: np.ndarray
    vt_x: np.ndarray             # V∞=(1,0)일 때 접선 속도
    vt_y: np.ndarray             # V∞=(0,1)일 때 접선 속도

    def vt(self, alpha_deg: float) -> np.ndarray:
        a = math.radians(alpha_deg)
        return math.cos(a) * self.vt_x + math.sin(a) * self.vt_y


def solve_inviscid(geom: Geometry, n_side: int = 80) -> InviscidSolution:
    nodes = [panelize(el, n_side) for el in geom.elements]
    bodies, start = [], 0
    for nd in nodes:
        bodies.append(slice(start, start + len(nd) - 1))
        start += len(nd) - 1
    N, K = start, len(nodes)

    all_mid, all_t, all_len = [], [], []
    for nd in nodes:
        d = np.diff(nd, axis=0)
        ln = np.linalg.norm(d, axis=1)
        all_mid.append(0.5 * (nd[:-1] + nd[1:]))
        all_t.append(d / ln[:, None])
        all_len.append(ln)
    mid, tangent, length = np.vstack(all_mid), np.vstack(all_t), np.concatenate(all_len)
    normal = np.column_stack([-tangent[:, 1], tangent[:, 0]])  # 왼쪽 = 바깥 (시계방향 순서)

    # 속도 영향계수: 열 = 패널 j의 소스, 물체 k의 와류
    us, vs, uv, vv = (np.zeros((N, N)) for _ in range(4))
    for b, nd in zip(bodies, nodes):
        (su, sv), (wu, wv) = _influence(nd, mid, b.start)
        us[:, b], vs[:, b], uv[:, b], vv[:, b] = su, sv, wu, wv
    Un = us * normal[:, :1] + vs * normal[:, 1:]      # 소스 → 법선 속도
    Ut = us * tangent[:, :1] + vs * tangent[:, 1:]
    Wn = np.zeros((N, K))
    Wt = np.zeros((N, K))
    for k, b in enumerate(bodies):
        Wn[:, k] = (uv[:, b] * normal[:, :1] + vv[:, b] * normal[:, 1:]).sum(1)
        Wt[:, k] = (uv[:, b] * tangent[:, :1] + vv[:, b] * tangent[:, 1:]).sum(1)

    A = np.zeros((N + K, N + K))
    A[:N, :N], A[:N, N:] = Un, Wn
    for k, b in enumerate(bodies):  # Kutta: 첫/마지막 패널 접선속도 합 = 0
        f, l = b.start, b.stop - 1
        A[N + k, :N] = Ut[f] + Ut[l]
        A[N + k, N:] = Wt[f] + Wt[l]

    sols = []
    for vinf in (np.array([1.0, 0.0]), np.array([0.0, 1.0])):
        rhs = np.zeros(N + K)
        rhs[:N] = -normal @ vinf
        for k, b in enumerate(bodies):
            f, l = b.start, b.stop - 1
            rhs[N + k] = -(tangent[f] @ vinf + tangent[l] @ vinf)
        x = np.linalg.solve(A, rhs)
        sols.append(tangent @ vinf + Ut @ x[:N] + Wt @ x[N:])
    return InviscidSolution(bodies, nodes, mid, tangent, normal, length, sols[0], sols[1])


# ---------------------------------------------------------------- 적분 경계층

def _H1_of_H(H):
    return 3.3 + (0.8234 * (H - 1.1) ** -1.287 if H <= 1.6 else 1.5501 * (H - 0.6778) ** -3.064)


def _H_of_H1(H1):
    H1 = max(H1, 3.3001)
    if H1 >= 5.3:
        return 1.1 + ((H1 - 3.3) / 0.8234) ** (-1 / 1.287)
    return 0.6778 + ((H1 - 3.3) / 1.5501) ** (-1 / 3.064)


@dataclass
class SurfaceBL:
    theta_te: float
    H_te: float
    ue_te: float
    x_transition: float      # 정체점부터 호길이
    sep_fraction: float      # 박리 이후 구간 / 전체 호길이 (0 = 박리 없음)
    cd: float = field(init=False)

    def __post_init__(self):
        # Squire-Young: 뒷전 상태에서 후류 원방 운동량 두께로 환산
        self.cd = 2 * self.theta_te * max(self.ue_te, 1e-6) ** ((min(self.H_te, 2.5) + 5) / 2)


def march_bl(s: np.ndarray, ue: np.ndarray, re: float, transition: str = "turbulent") -> SurfaceBL:
    """정체점(s=0, ue≈0)부터 뒷전까지 경계층 전진 계산. s, ue는 시위·자유류 기준 무차원."""
    ue = np.maximum(np.abs(ue), 1e-6)
    # 세분화 (패널 점 사이를 선형 보간해 안정적으로 적분)
    sf = np.interp(np.linspace(0, len(s) - 1, (len(s) - 1) * 8 + 1), np.arange(len(s)), s)
    uf = np.interp(sf, s, ue)
    due = np.gradient(uf, sf)

    # 층류 (Thwaites)
    integ = np.concatenate([[0], np.cumsum(0.5 * (uf[1:] ** 5 + uf[:-1] ** 5) * np.diff(sf))])
    theta = np.sqrt(np.maximum(0.45 / re * integ / uf**6, 1e-20))
    theta[0] = math.sqrt(0.075 / (re * max(due[0], 1e-3)))
    # 천이 위치: "turbulent"는 정체점 근처(1% 시위)에서 강제 천이 (Fluent SST 완전난류와 유사)
    # "free"는 Michel 기준 또는 층류 박리 지점
    i_tr = len(sf) - 1
    for i in range(1, len(sf)):
        if transition == "turbulent":
            tripped = sf[i] >= 0.01
        else:
            re_th, re_x = re * uf[i] * theta[i], max(re * uf[i] * sf[i], 1.0)
            tripped = re_th > 1.174 * (1 + 22400 / re_x) * re_x**0.46
        if tripped or re * theta[i] ** 2 * due[i] < -0.09:
            i_tr = i
            break
    th = theta[i_tr]
    H = 1.4

    # 난류 (Head + Ludwieg-Tillmann)
    sep_s = None
    for i in range(i_tr, len(sf) - 1):
        ds = sf[i + 1] - sf[i]
        re_th = max(re * uf[i] * th, 50.0)
        Hc = min(H, 2.4)
        cf = 0.246 * 10 ** (-0.678 * Hc) * re_th**-0.268 if H < 2.4 else 0.0
        dth = cf / 2 - (Hc + 2) * th / uf[i] * due[i]
        H1 = _H1_of_H(Hc)
        dH1 = (0.0306 * (H1 - 3) ** -0.6169 - H1 * (th * due[i] / uf[i] + dth)) / th
        th = max(th + dth * ds, 1e-8)
        H = _H_of_H1(H1 + dH1 * ds)
        if H >= 2.4 and sep_s is None:
            sep_s = sf[i + 1]
        if H >= 2.4:
            H = 2.4
    frac = 0.0 if sep_s is None else (sf[-1] - sep_s) / sf[-1]
    return SurfaceBL(th, H, uf[-1], sf[i_tr], frac)


# ---------------------------------------------------------------- 받음각별 결과

@dataclass
class PointResult:
    alpha: float
    cl: float
    cd: float
    cl_elements: list[float]
    cd_elements: list[float]
    cp_min: list[float]          # 물체별 최소 Cp
    delta_cp: list[float]        # 물체별 압력 회복량 Cp_TE(윗면) - Cp_min (실속 지표)
    sep_upper: list[float]       # 물체별 윗면 박리 비율

    @property
    def ld(self) -> float:
        return self.cl / self.cd if self.cd > 0 else float("nan")


def analyze(sol: InviscidSolution, alpha: float, re: float, mach: float = 0.0,
            transition: str = "turbulent") -> PointResult:
    a = math.radians(alpha)
    beta = math.sqrt(max(1 - mach**2, 0.05))
    vt = sol.vt(alpha)
    cp = (1 - vt**2) / beta
    lift_dir = np.array([-math.sin(a), math.cos(a)])

    cls, cds, cpmins, dcps, seps = [], [], [], [], []
    for b, nd in zip(sol.bodies, sol.nodes):
        f = -(cp[b, None] * sol.normal[b] * sol.length[b, None]).sum(0)
        cls.append(float(f @ lift_dir))
        v = vt[b]
        # 정체점: 접선속도 부호가 바뀌는 곳 (아랫면 v<0 → 윗면 v>0)
        sign_change = np.nonzero((v[:-1] < 0) & (v[1:] >= 0))[0]
        i_st = int(sign_change[np.argmin(np.abs(sign_change - len(v) // 2))]) if sign_change.size else len(v) // 2
        # 정체점 위치를 두 패널 중점 사이에서 선형 보간하고, 거기서부터 각 면을 따라 호길이 누적
        m = sol.mid[b]
        frac = v[i_st] / (v[i_st] - v[i_st + 1])
        stag = m[i_st] + frac * (m[i_st + 1] - m[i_st])
        pts_up = np.vstack([stag, m[i_st + 1:]])
        pts_lo = np.vstack([stag, m[: i_st + 1][::-1]])

        def arclen(pts):
            return np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])

        up = march_bl(arclen(pts_up), np.concatenate([[0], v[i_st + 1:]]), re, transition)
        lo = march_bl(arclen(pts_lo), np.concatenate([[0], v[: i_st + 1][::-1]]), re, transition)
        cds.append(up.cd + lo.cd)
        cpb = cp[b]
        cpmins.append(float(cpb.min()))
        dcps.append(float(cpb[-1] - cpb[i_st + 1:].min()))
        seps.append(up.sep_fraction)
    cl = sum(cls)
    return PointResult(alpha, cl, sum(cds), cls, cds, cpmins, dcps, seps)


def polar(geom: Geometry, alphas, re: float, mach: float = 0.0, transition: str = "turbulent",
          n_side: int = 80) -> list[PointResult]:
    sol = solve_inviscid(geom, n_side)
    return [analyze(sol, a, re, mach, transition) for a in alphas]
