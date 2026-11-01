"""이미지 자동 저장: 형상, 메쉬, 수렴 이력, Cl/Cd 요약 그래프."""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

import numpy as np

import matplotlib

matplotlib.use("Agg")  # 화면 없이 파일로만 저장
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402

DPI = 150


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_geometry(geom, path: Path, title: str = "") -> Path:
    fig, ax = plt.subplots(figsize=(9, 3.2))
    for el in geom.elements:
        ax.fill(el.xy[:, 0], el.xy[:, 1], alpha=0.25)
        ax.plot(el.xy[:, 0], el.xy[:, 1], lw=1, label=el.name)
    ground_y = geom.info.get("ground_y")
    if ground_y is not None:
        allx = np.concatenate([el.xy[:, 0] for el in geom.elements])
        ax.axhline(ground_y, color="k", lw=1.5)
        ax.fill_between([allx.min() - 0.1, allx.max() + 0.1], ground_y - 0.03, ground_y, color="0.6", hatch="//")
    lines = [title] + ([f"aoa={geom.info['aoa']:g}° (about main LE), ground clearance={geom.info['ground_clearance']:g} "
                        f"({geom.info['ground_ref']}), lowest point={geom.info['lowest_clearance']:.3g}, "
                        f"main LE height={geom.info['main_le_height']:.3g}"] if ground_y is not None else []) + [
        f"{f['name']}: {f['airfoil']} c={f['chord']:g}, δ={f['deflection']:g}°, gap={f['gap']:g}, "
        f"overlap={f['overlap']:g}, min clearance={f['clearance']:.3g}"
        for f in geom.info.get("flaps", [])
    ]
    ax.set_title("\n".join(lines), fontsize=9)
    ax.set_aspect("equal")
    ax.grid(alpha=0.3)
    ax.set_xlabel("x/c")
    ax.set_ylabel("y/c")
    return _save(fig, path)


def plot_mesh(nodes: np.ndarray, cells: list, geom, out_dir: Path, title: str = "") -> list[Path]:
    """전체 근거리 뷰 + 슬롯별(앞 요소 뒷전) 확대 뷰 + 메인 앞전 뷰."""
    allxy = np.vstack([el.xy for el in geom.elements])
    (x0, y0), (x1, y1) = allxy.min(0), allxy.max(0)
    ground_y = geom.info.get("ground_y")
    y_lo = ground_y - 0.02 if ground_y is not None else y0 - 0.35
    views = [("mesh.png", (x0 - 0.3, x1 + 0.6), (y_lo, y1 + 0.35))]
    inverted = geom.info.get("downforce", False)
    for i, flap in enumerate(geom.info.get("flaps", []), start=1):
        prev = geom.elements[i - 1]
        # 슬롯은 앞 요소 뒷전의 아래쪽(다운포스 형상이면 위쪽)
        te = prev.te_upper if inverted else prev.te_lower
        r = max(0.04, 6 * flap["clearance_c"])
        ylim = (te[1] - 0.6 * r, te[1] + r) if inverted else (te[1] - r, te[1] + 0.6 * r)
        views.append((f"mesh_slot{i}.png", (te[0] - r, te[0] + r), ylim))
    views.append(("mesh_le.png", (-0.03, 0.05), (-0.035, 0.035)))

    polys = [nodes[list(c)] for c in cells]
    saved = []
    for fname, xlim, ylim in views:
        xs = [p[:, 0] for p in polys]
        # 보이는 영역의 셀만 그려서 속도 확보
        visible = [p for p, x in zip(polys, xs)
                   if x.max() >= xlim[0] and x.min() <= xlim[1]
                   and p[:, 1].max() >= ylim[0] and p[:, 1].min() <= ylim[1]]
        fig, ax = plt.subplots(figsize=(9, 9 * (ylim[1] - ylim[0]) / (xlim[1] - xlim[0])))
        ax.add_collection(PolyCollection(visible, facecolors="none", edgecolors="k", linewidths=0.15))
        for el in geom.elements:
            ax.fill(el.xy[:, 0], el.xy[:, 1], color="0.7")
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_aspect("equal")
        ax.set_title(f"{title}  ({len(cells):,} cells)", fontsize=9)
        saved.append(_save(fig, out_dir / fname))
    return saved


def plot_convergence(report_files: list[Path], path: Path, title: str = "") -> Path | None:
    from .results import parse_report_file

    series = []
    for f in report_files:
        headers, rows = parse_report_file(f)
        if rows:
            arr = np.array(rows)
            series += [(h, arr[:, 0], arr[:, i]) for i, h in enumerate(headers) if i > 0]
    if not series:
        return None
    n = len(series)
    fig, axes = plt.subplots(n, 1, figsize=(8, 2.0 * n), sharex=True, squeeze=False)
    for ax, (name, it, val) in zip(axes[:, 0], series):
        ax.plot(it, val, lw=1)
        ax.set_ylabel(name)
        ax.grid(alpha=0.3)
        # 마지막 20% 구간 기준으로 y축을 잡아 수렴 상태가 보이게
        tail = val[int(len(val) * 0.8):]
        if len(tail) > 2 and np.ptp(tail) > 0:
            pad = max(np.ptp(tail) * 2, abs(tail.mean()) * 0.02)
            ax.set_ylim(tail.mean() - pad, tail.mean() + pad)
    axes[-1, 0].set_xlabel("iteration")
    axes[0, 0].set_title(title, fontsize=9)
    return _save(fig, path)


def plot_summary(rows: list[dict], out_dir: Path, x_param: str, group_keys: list[str],
                 cl_label: str = "Cl") -> list[Path]:
    """형상별 Cl-α, Cd-α, L/D-α, 극곡선(Cl-Cd)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("state") == "done" and isinstance(r.get("cl"), float) and x_param in r:
            label = ", ".join(f"{k}={r[k]}" for k in group_keys if k in r) or "all"
            groups[label].append(r)
    if not groups:
        return []

    def figure(ylabel, xlabel=x_param):
        fig, ax = plt.subplots(figsize=(8, 5.5))
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.3)
        return fig, ax

    specs = [
        ("cl_alpha.png", cl_label, lambda r: (r[x_param], r["cl"])),
        ("cd_alpha.png", "Cd", lambda r: (r[x_param], r.get("cd"))),
        ("ld_alpha.png", "L/D", lambda r: (r[x_param], r.get("l/d"))),
        ("polar.png", cl_label, lambda r: (r.get("cd"), r["cl"])),
    ]
    saved = []
    for fname, ylabel, xy in specs:
        fig, ax = figure(ylabel, "Cd" if fname == "polar.png" else x_param)
        for label, rs in sorted(groups.items()):
            pts = sorted((p for p in map(xy, sorted(rs, key=lambda r: r[x_param]))
                          if p[0] is not None and p[1] is not None),
                         key=lambda p: p[0] if fname != "polar.png" else 0)
            if pts:
                ax.plot(*zip(*pts), "o-", ms=3, lw=1, label=label)
        if len(groups) <= 12:
            ax.legend(fontsize=7)
        else:
            ax.set_title(f"{len(groups)} geometries (legend omitted)", fontsize=9)
        saved.append(_save(fig, out_dir / fname))
    return saved


# ---------------------------------------------------------------- 스크리닝

def _category_axis(values):
    """숫자면 정렬된 숫자, 문자(익형 이름 등)면 순서대로 범주 위치."""
    uniq = sorted(set(values), key=lambda v: (isinstance(v, str), v if not isinstance(v, str) else str(v)))
    numeric = all(isinstance(v, (int, float)) for v in uniq)
    pos = {v: (float(v) if numeric else i) for i, v in enumerate(uniq)}
    return uniq, pos, numeric


def plot_screening(geoms: list[dict], params: list[str], score_key: str, x_param: str,
                   protect: list[str], out_dir: Path, title: str = "") -> list[Path]:
    ok = [g for g in geoms if g.get("feasible")]
    saved = []
    if not ok:
        return saved
    label = {"ld_max_est": "est. max L/D", "cl_max_est": "est. max Cl"}[score_key]

    # 1) 주효과: 변수 값별 점수 분포 (평균선 + 최소~최대 범위 + 개별 형상)
    if params:
        n = len(params)
        cols = min(3, n)
        rows = math.ceil(n / cols)
        fig, axes = plt.subplots(rows, cols, figsize=(4.6 * cols, 3.6 * rows), squeeze=False)
        importance = {}
        for ax, p in zip(axes.flat, params):
            uniq, pos, numeric = _category_axis([g[p] for g in ok])
            means, lows, highs = [], [], []
            for v in uniq:
                sc = [g[score_key] for g in ok if g[p] == v]
                means.append(np.mean(sc))
                lows.append(min(sc))
                highs.append(max(sc))
                xs = np.full(len(sc), pos[v]) + (np.random.default_rng(0).uniform(-1, 1, len(sc))
                                                 * (0 if numeric else 0.08))
                colors = ["tab:blue" if g["keep"] else "0.6" for g in ok if g[p] == v]
                ax.scatter(xs, sc, s=10, c=colors, alpha=0.6, zorder=3)
            x = [pos[v] for v in uniq]
            ax.fill_between(x, lows, highs, alpha=0.15, color="tab:orange")
            ax.plot(x, means, "o-", color="tab:orange", lw=2, label="mean")
            ax.set_xticks(x)
            ax.set_xticklabels([f"{v:g}" if numeric else str(v) for v in uniq],
                               rotation=0 if numeric else 30, ha="center" if numeric else "right", fontsize=8)
            ax.set_title(p + ("  (protected)" if p in protect else ""), fontsize=10)
            ax.set_ylabel(label)
            ax.grid(alpha=0.3)
            importance[p] = max(means) - min(means)
        for ax in list(axes.flat)[n:]:
            ax.axis("off")
        fig.suptitle(f"Main effects (blue = kept, gray = pruned)  {title}", fontsize=11)
        fig.tight_layout()
        saved.append(_save(fig, out_dir / "sensitivity_main_effects.png"))

        # 2) 변수 중요도: 평균 점수 변화 폭
        best = max(g[score_key] for g in ok)
        order = sorted(importance, key=importance.get)
        fig, ax = plt.subplots(figsize=(7, 0.5 * len(order) + 1.5))
        ax.barh(order, [importance[p] / abs(best) * 100 for p in order],
                color=["0.6" if p in protect else "tab:blue" for p in order])
        ax.set_xlabel(f"range of mean {label} / best  [%]")
        ax.set_title(f"Parameter importance (gray = protected from pruning)  {title}", fontsize=10)
        ax.grid(alpha=0.3, axis="x")
        saved.append(_save(fig, out_dir / "sensitivity_importance.png"))

    # 3) 형상별 추정 Cl-α, L/D-α
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5))
    top = sorted([g for g in ok if g["keep"]], key=lambda g: -g[score_key])[:10]
    for g in ok:
        pts = g["_polar"]
        al = np.array([p.alpha for p in pts])
        cl = np.array([p.cl for p in pts])
        ld = np.array([p.ld for p in pts])
        a_s = g.get("alpha_stall_est")
        pre = al <= (a_s if a_s is not None else np.inf)
        if g in top:
            style = dict(lw=1.2, label=g["geometry"])
        else:
            style = dict(lw=0.6, color="tab:blue" if g["keep"] else "0.75", alpha=0.6)
        line, = a1.plot(al[pre], cl[pre], **style)
        a2.plot(al[pre], ld[pre], **{**style, "color": line.get_color(), "label": None})
        # 추정 실속각 이후는 비점성 해석이 계속 증가하므로 참고용으로 흐리게
        post = ~pre | (np.arange(len(al)) == pre.sum() - 1)
        a1.plot(al[post], cl[post], color=line.get_color(), lw=0.5, alpha=0.25, ls="--")
        a2.plot(al[post], ld[post], color=line.get_color(), lw=0.5, alpha=0.25, ls="--")
        if a_s is not None:
            a1.plot(a_s, np.interp(a_s, al, cl), "kx", ms=5)
    a1.set_xlabel(x_param)
    a1.set_ylabel("est. Cl (inviscid + BL)")
    a2.set_xlabel(x_param)
    a2.set_ylabel("est. L/D")
    for a in (a1, a2):
        a.grid(alpha=0.3)
    a1.legend(fontsize=6)
    a1.set_title(f"x = est. stall (main element separation), dashed = post-stall  {title}", fontsize=9)
    saved.append(_save(fig, out_dir / "screening_polars.png"))

    # 4) 형상 산점도
    fig, ax = plt.subplots(figsize=(7, 5.5))
    for keep, color, name in ((False, "0.65", "pruned"), (True, "tab:blue", "kept")):
        sel = [g for g in ok if g["keep"] == keep]
        ax.scatter([g["cl_max_est"] for g in sel], [g["ld_max_est"] for g in sel], c=color, s=18, label=name)
    ax.set_xlabel("est. max Cl")
    ax.set_ylabel("est. max L/D")
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend()
    saved.append(_save(fig, out_dir / "screening_scatter.png"))
    return saved


def plot_screening_vs_cfd(pairs: list[tuple[float, float, str]], path: Path, label: str = "Cl") -> Path | None:
    """저충실도 추정 vs CFD 결과 비교 (스크리닝 신뢰도 확인용)."""
    if len(pairs) < 2:
        return None
    est, cfd, _ = zip(*pairs)
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.scatter(est, cfd, s=15)
    lo, hi = min(est + cfd), max(est + cfd)
    ax.plot([lo, hi], [lo, hi], "k--", lw=0.8)
    if len(set(est)) > 2:
        r = np.corrcoef(est, cfd)[0, 1]
        ax.set_title(f"screening vs CFD: correlation r = {r:.3f}", fontsize=10)
    ax.set_xlabel(f"{label} (screening estimate)")
    ax.set_ylabel(f"{label} (CFD)")
    ax.grid(alpha=0.3)
    return _save(fig, path)


def plot_condition_trend(ranking: list[dict], cond_key: str, geo_keys: list[str], objective: str,
                         path: Path, top: int = 10) -> Path | None:
    """조건(예: 속도)에 따른 형상별 최대 L/D(또는 Cl) 변화. 평균 상위 형상만 표시."""
    metric = "ld_max" if objective == "max_ld" else "cl_max"
    series: dict[str, list[tuple]] = defaultdict(list)
    for e in ranking:
        if isinstance(e.get(cond_key), (int, float)) and e.get(metric) is not None:
            label = ", ".join(f"{k}={e[k]}" for k in geo_keys if k in e) or "all"
            series[label].append((e[cond_key], e[metric]))
    if not series or all(len(v) < 2 for v in series.values()):
        return None
    best = sorted(series, key=lambda s: -np.mean([v for _, v in series[s]]))[:top]
    fig, ax = plt.subplots(figsize=(8, 5.5))
    for label in best:
        pts = sorted(series[label])
        ax.plot(*zip(*pts), "o-", ms=4, lw=1.2, label=label)
    ax.set_xlabel(cond_key)
    ax.set_ylabel({"ld_max": "max L/D", "cl_max": "max Cl"}[metric])
    ax.set_title(f"top {len(best)} geometries by mean {metric}", fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)
    return _save(fig, path)
