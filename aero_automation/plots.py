"""이미지 자동 저장: 형상, 메쉬, 수렴 이력, Cl/Cd 요약 그래프."""

from __future__ import annotations

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
    info = geom.info
    if info.get("elements", 1) > 1:
        ax.set_title(
            f"{title}\nflap c={info['flap_chord']:g}, δ={info['flap_deflection']:g}°, "
            f"gap={info['gap']:g}, overlap={info['overlap']:g}, min clearance={info['min_clearance']:.4f}",
            fontsize=9,
        )
    else:
        ax.set_title(title, fontsize=9)
    ax.set_aspect("equal")
    ax.grid(alpha=0.3)
    ax.set_xlabel("x/c")
    ax.set_ylabel("y/c")
    return _save(fig, path)


def plot_mesh(nodes: np.ndarray, cells: list, geom, out_dir: Path, title: str = "") -> list[Path]:
    """전체 근거리 뷰 + 슬롯(메인익 뒷전) 확대 뷰."""
    main = geom.elements[0]
    te = main.te_lower
    views = [("mesh.png", (-0.3, 1.8), (-0.6, 0.5))]
    if len(geom.elements) > 1:
        r = max(0.04, 6 * geom.info["min_clearance"])
        views.append(("mesh_slot.png", (te[0] - r, te[0] + r), (te[1] - r, te[1] + 0.6 * r)))
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


def plot_summary(rows: list[dict], out_dir: Path, x_param: str, group_keys: list[str]) -> list[Path]:
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
        ("cl_alpha.png", "Cl", lambda r: (r[x_param], r["cl"])),
        ("cd_alpha.png", "Cd", lambda r: (r[x_param], r.get("cd"))),
        ("ld_alpha.png", "L/D", lambda r: (r[x_param], r.get("l/d"))),
        ("polar.png", "Cl", lambda r: (r.get("cd"), r["cl"])),
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
