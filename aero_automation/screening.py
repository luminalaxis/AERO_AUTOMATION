"""CFD 전 스크리닝: 저충실도 해석으로 민감도를 보고 가망 없는 케이스를 미리 제외한다.

1. 형상별로 패널법 + 적분 경계층(panel.py)으로 Cl, Cd, 박리를 받음각에 따라 추정
2. 형상 지표: 추정 실속각, 최대 Cl, 최대 L/D
3. 계열 선별: `protect` 변수(기본 gap, overlap)를 뺀 나머지가 같은 형상을 한 계열로 묶고,
   계열 안 최고 점수로 비교해 하위 계열을 제외. 슬롯 변수는 저충실도로 예측이 어려워
   (합류 경계층 미고려) 그 값만으로는 제외하지 않는다.
4. 받음각 선별: 추정 실속각 + 여유각을 넘는 받음각은 제외 (넘는 첫 받음각 1개는 유지)
5. 스팟 체크: 제외된 계열 중 점수가 가장 높은 계열의 최고 형상을 받음각 1~2개만 CFD로 확인.
   collect 단계에서 이 형상이 CFD에서 남긴 형상보다 좋으면 경고 → 스크리닝 기준 완화 필요
6. 결과: runs/screening/ 에 CSV, 민감도 그래프, selection.json → run 이 자동으로 반영
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import numpy as np

from .cases import Case
from .config import ConfigError, ProjectConfig
from .geometry import build_geometry
from .meshing import GEOMETRY_KEYS, mesh_name
from .panel import analyze, solve_inviscid

SCREEN_DIR = "screening"
SELECTION_FILE = "selection.json"


@dataclass
class ScreeningConfig:
    reynolds: float | None = None   # 비우면 케이스 변수 reynolds → [mesh] reynolds → 3e6
    mach: float | None = None       # 비우면 케이스 변수 mach → 0
    transition: str = "turbulent"   # turbulent (Fluent SST 완전난류와 유사) / free
    metric: str | None = None       # max_ld / max_cl. 비우면 [results] objective
    protect: list = field(default_factory=lambda: ["gap", "overlap"])
    keep_top: int = 3               # 점수 상위 N개 계열은 항상 유지
    keep_within: float = 0.05       # 최고 계열 대비 이 비율 이내면 유지
    spot_check: int = 1             # 제외된 계열 중 상위 N개는 대표 케이스만 CFD로 검증
    prune_aoa: bool = True
    sep_limit: float = 0.10         # 메인익 윗면 박리 구간 비율이 이 값을 넘으면 실속으로 판정
    aoa_margin: float = 2.0         # 추정 실속각 + 이 값까지는 해석
    aoa_step: float = 0.5           # 실속각 탐색 간격 [deg]
    n_side: int = 80                # 물체당 윗면/아랫면 패널 수

    @classmethod
    def from_dict(cls, data: dict | None) -> "ScreeningConfig":
        data = data or {}
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ConfigError(f"[screening]에 알 수 없는 항목: {', '.join(sorted(unknown))}")
        return cls(**data)


def _geometry_params(case: Case) -> dict:
    return {k: case.params[k] for k in GEOMETRY_KEYS if k in case.params and case.params[k] != ""}


def _flow(case: Case, cfg: ProjectConfig, sc: ScreeningConfig) -> tuple[float, float]:
    re = sc.reynolds or case.params.get("reynolds") or (cfg.mesh or {}).get("reynolds") or 3e6
    mach = sc.mach if sc.mach is not None else case.params.get("mach", 0.0)
    return float(re), float(mach)


def _stall_alpha(alphas: np.ndarray, sep: np.ndarray, limit: float) -> float | None:
    above = np.nonzero(sep > limit)[0]
    if not above.size:
        return None
    i = above[0]
    if i == 0:
        return float(alphas[0])
    a0, a1, s0, s1 = alphas[i - 1], alphas[i], sep[i - 1], sep[i]
    return float(a0 + (limit - s0) / (s1 - s0) * (a1 - a0)) if s1 != s0 else float(a1)


def evaluate_geometry(params: dict, root: Path, case_alphas: list[float], re: float, mach: float,
                      sc: ScreeningConfig, x_param: str) -> dict:
    """한 형상의 받음각 스윕 저충실도 해석. 실패(형상 불가)면 ConfigError."""
    geom = build_geometry(params, root)
    sol = solve_inviscid(geom, sc.n_side)
    lo, hi = min(case_alphas), max(case_alphas) + sc.aoa_margin + 4
    grid = np.unique(np.round(np.concatenate([np.arange(lo, hi + 1e-9, sc.aoa_step), case_alphas]), 6))
    pts = [analyze(sol, a, re, mach, sc.transition) for a in grid]

    sep = np.array([p.sep_upper[0] for p in pts])
    a_stall = _stall_alpha(grid, sep, sc.sep_limit)
    valid = [p for p in pts if a_stall is None or p.alpha <= a_stall]
    best_cl = max(valid or pts[:1], key=lambda p: p.cl)
    best_ld = max(valid or pts[:1], key=lambda p: p.ld)
    by_alpha = {round(p.alpha, 6): p for p in pts}
    return {
        "alpha_stall_est": a_stall,
        "cl_max_est": best_cl.cl,
        f"{x_param}@cl_max_est": best_cl.alpha,
        "ld_max_est": best_ld.ld,
        f"{x_param}@ld_max_est": best_ld.alpha,
        "flap_sep_est": max(p.sep_upper[1] for p in valid or pts[:1]) if len(geom.elements) > 1 else 0.0,
        "min_clearance": geom.info.get("min_clearance", ""),
        "_polar": pts,
        "_by_alpha": by_alpha,
    }


def screen(cfg: ProjectConfig, cases: list[Case], log=print) -> dict:
    sc = ScreeningConfig.from_dict(cfg.screening)
    metric = sc.metric or cfg.results.objective
    score_key = "ld_max_est" if metric == "max_ld" else "cl_max_est"
    x_param = cfg.results.x_param
    if any("main_airfoil" not in c.params for c in cases):
        raise ConfigError("스크리닝에는 형상 변수(main_airfoil 등)가 필요합니다.")
    if any(x_param not in c.params for c in cases):
        raise ConfigError(f"스크리닝에는 받음각 변수 '{x_param}'가 필요합니다 ([results] x_param).")

    # 형상(+유동 조건)별로 케이스 묶기
    groups: dict[tuple, list[Case]] = {}
    for c in cases:
        gp = _geometry_params(c)
        groups.setdefault((mesh_name(gp), *_flow(c, cfg, sc)), []).append(c)

    varying = [k for k in GEOMETRY_KEYS
               if len({repr(_geometry_params(c).get(k)) for c in cases}) > 1]
    family_keys = [k for k in varying if k not in sc.protect]

    geoms: list[dict] = []
    for i, ((gname, re, mach), gcases) in enumerate(groups.items(), start=1):
        gp = _geometry_params(gcases[0])
        row = {"geometry": gname, **{k: gp.get(k, "") for k in varying}, "reynolds": re, "mach": mach,
               "family": ", ".join(f"{k}={gp.get(k)}" for k in family_keys) or "all",
               "n_cases": len(gcases)}
        try:
            row.update(evaluate_geometry(gp, cfg.root, [float(c.params[x_param]) for c in gcases],
                                         re, mach, sc, x_param))
            row["feasible"] = True
        except ConfigError as exc:
            row.update(feasible=False, reason=f"형상 불가: {exc}")
        row["_cases"] = gcases
        geoms.append(row)
        if i % 25 == 0 or i == len(groups):
            log(f"  저충실도 해석 {i}/{len(groups)} 형상")

    # 계열 선별
    families: dict[str, float] = {}
    for g in geoms:
        if g["feasible"]:
            families[g["family"]] = max(families.get(g["family"], -math.inf), g[score_key])
    ranked = sorted(families.items(), key=lambda kv: kv[1], reverse=True)
    best = ranked[0][1] if ranked else 0.0
    threshold = best - abs(best) * sc.keep_within
    keep_family = {f for rank, (f, s) in enumerate(ranked, start=1) if rank <= sc.keep_top or s >= threshold}
    family_rank = {f: r for r, (f, _) in enumerate(ranked, start=1)}

    # 스팟 체크: 제외된 계열 중 상위 계열의 최고 형상, 추정 최적 받음각 근처 케이스만 유지
    spot: dict[str, list[str]] = {}
    pruned_families = [f for f, _ in ranked if f not in keep_family][: sc.spot_check]
    for fam in pruned_families:
        g = max((g for g in geoms if g["feasible"] and g["family"] == fam), key=lambda g: g[score_key])
        alphas = sorted({float(c.params[x_param]) for c in g["_cases"]})
        targets = {g[f"{x_param}@ld_max_est"], g[f"{x_param}@cl_max_est"]}
        picks = {min(alphas, key=lambda a: abs(a - t)) for t in targets}
        spot[g["geometry"]] = [c.name for c in g["_cases"] if float(c.params[x_param]) in picks]

    case_rows, kept, pruned = [], [], {}
    for g in geoms:
        if g["feasible"]:
            g["family_rank"] = family_rank[g["family"]]
            g["family_score"] = families[g["family"]]
            g["keep"] = g["family"] in keep_family
            if not g["keep"]:
                g["reason"] = (f"계열 {score_key} {families[g['family']]:.3g} < 기준 {threshold:.3g} "
                               f"(상위 {sc.keep_top}개 밖)")
        else:
            g["keep"] = False
        a_cut = None
        if g["keep"] and sc.prune_aoa and g.get("alpha_stall_est") is not None:
            a_cut = g["alpha_stall_est"] + sc.aoa_margin
            beyond = sorted(float(c.params[x_param]) for c in g["_cases"] if float(c.params[x_param]) > a_cut)
            if beyond:
                a_cut = beyond[0]  # 실속 이후 거동 확인용으로 넘는 첫 받음각 1개는 유지
        g["aoa_cut"] = a_cut
        for c in sorted(g["_cases"], key=lambda c: float(c.params[x_param])):
            a = float(c.params[x_param])
            est = g.get("_by_alpha", {}).get(round(a, 6))
            reason = g.get("reason", "") if not g["keep"] else ""
            if g["keep"] and a_cut is not None and a > a_cut:
                reason = f"추정 실속각 {g['alpha_stall_est']:.1f}° + {sc.aoa_margin:g}° 초과"
            if c.name in spot.get(g["geometry"], []):
                reason = ""
                g["spot_check"] = True
            keep = not reason
            case_rows.append({"case": c.name, "geometry": g["geometry"], x_param: a,
                              "cl_est": est.cl if est else "", "cd_est": est.cd if est else "",
                              "ld_est": est.ld if est else "",
                              "cl_main_est": est.cl_elements[0] if est else "",
                              "sep_main_est": est.sep_upper[0] if est else "",
                              "keep": keep, "reason": reason,
                              "spot_check": c.name in spot.get(g["geometry"], [])})
            if keep:
                kept.append(c.name)
            else:
                pruned[c.name] = reason

    out = cfg.output_dir / SCREEN_DIR
    out.mkdir(parents=True, exist_ok=True)
    public = [{k: v for k, v in g.items() if not k.startswith("_")} for g in geoms]
    public.sort(key=lambda g: (not g["feasible"], -(g.get(score_key) or -math.inf)))
    _write_csv(out / "geometries.csv", public)
    _write_csv(out / "cases.csv", case_rows)
    selection = {"all_cases": [c.name for c in cases], "kept": kept, "pruned": pruned,
                 "spot_check": spot, "metric": score_key, "settings": asdict(sc)}
    (out / SELECTION_FILE).write_text(json.dumps(selection, ensure_ascii=False, indent=1), encoding="utf-8")

    from .plots import plot_screening
    plots = plot_screening(geoms, varying, score_key, x_param, sc.protect, out)
    return {"dir": out, "geometries": public, "kept": kept, "pruned": pruned, "plots": plots,
            "score_key": score_key, "n_families": len(families), "n_families_kept": len(keep_family),
            "spot_check": spot}


def check_spot(selection: dict, ranking: list[dict]) -> list[str]:
    """스팟 체크 형상의 CFD 결과가 유지된 형상보다 좋으면 경고 메시지를 만든다."""
    spot = selection.get("spot_check") or {}
    key = "ld_max" if selection.get("metric") == "ld_max_est" else "cl_max"
    by_geom = {r.get("mesh"): r for r in ranking if r.get("mesh") and r.get(key) is not None}
    kept_scores = [r[key] for g, r in by_geom.items() if g not in spot]
    warnings = []
    for g in spot:
        if g in by_geom and kept_scores and by_geom[g][key] > min(kept_scores):
            warnings.append(
                f"스팟 체크 형상 {g}의 CFD {key} {by_geom[g][key]:.3g}가 유지된 형상의 최저값 "
                f"{min(kept_scores):.3g}보다 높습니다. 스크리닝이 좋은 형상을 놓쳤을 수 있으니 "
                "[screening] keep_within 을 늘려 다시 screen 하는 것을 권장합니다."
            )
    return warnings


def _write_csv(path: Path, rows: list[dict]) -> None:
    columns: list[str] = []
    for r in rows:
        columns += [k for k in r if k not in columns]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


def load_selection(cfg: ProjectConfig) -> dict | None:
    path = cfg.output_dir / SCREEN_DIR / SELECTION_FILE
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
