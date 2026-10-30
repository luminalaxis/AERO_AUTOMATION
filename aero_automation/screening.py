"""CFD 전 스크리닝: 저충실도 해석으로 민감도를 보고 가망 없는 케이스를 미리 제외한다.

1. 형상별로 패널법 + 적분 경계층(panel.py)으로 Cl, Cd, 박리를 받음각에 따라 추정
2. 형상 지표: 추정 실속각, 최대 Cl, 최대 L/D
3. 계열 선별: `protect` 변수(기본 gap, overlap)를 뺀 나머지가 같은 형상을 한 계열로 묶고,
   계열 안 최고 점수로 비교해 하위 계열을 제외. 슬롯 변수(갭/오버랩)는 저충실도로 예측이 어렵고
   (합류 경계층 미고려), 메인 코드는 길수록 Re가 커져 항상 유리하므로 그 값만으로는 제외하지 않는다.
4. 받음각 선별: 추정 실속각 + 여유각을 넘는 받음각은 제외 (넘는 첫 받음각 1개는 유지)
5. 스팟 체크: 제외된 계열 중 점수가 가장 높은 계열의 최고 형상을 받음각 1~2개만 CFD로 확인.
   collect 단계에서 이 형상이 CFD에서 남긴 형상보다 좋으면 경고 → 스크리닝 기준 완화 필요
6. 결과: runs/screening/ 에 CSV, 민감도 그래프, selection.json → run 이 자동으로 반영
"""

from __future__ import annotations

import csv
import json
import math
import shutil
from fnmatch import fnmatch
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import numpy as np

from .cases import Case
from .config import ConfigError, ProjectConfig, safe_name
from .geometry import build_geometry
from .geometry import geometry_params
from .meshing import mesh_name
from .panel import analyze, solve_inviscid

SCREEN_DIR = "screening"
SELECTION_FILE = "selection.json"


@dataclass
class ScreeningConfig:
    reynolds: float | None = None   # 비우면 케이스 변수 reynolds → [mesh] reynolds → 3e6
    mach: float | None = None       # 비우면 케이스 변수 mach → 0
    transition: str = "turbulent"   # turbulent (Fluent SST 완전난류와 유사) / free
    metric: str | None = None       # max_ld / max_cl. 비우면 [results] objective
    protect: list = field(default_factory=lambda: ["*gap", "*overlap", "main_chord", "chord"])  # 와일드카드 가능
    keep_top: int = 3               # 점수 상위 N개 계열은 항상 유지
    keep_within: float = 0.05       # 최고 계열 대비 이 비율 이내면 유지
    spot_check: int = 1             # 제외된 계열 중 상위 N개는 대표 케이스만 CFD로 검증
    prune_aoa: bool = True
    sep_limit: float = 0.10         # 메인익 윗면 박리 구간 비율이 이 값을 넘으면 실속으로 판정
    aoa_margin: float = 2.0         # 추정 실속각 + 이 값까지는 해석
    aoa_step: float = 1.0           # 실속각 탐색 간격 [deg] (사이 값은 보간)
    n_side: int = 80                # 물체당 윗면/아랫면 패널 수

    @classmethod
    def from_dict(cls, data: dict | None) -> "ScreeningConfig":
        data = data or {}
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ConfigError(f"[screening]에 알 수 없는 항목: {', '.join(sorted(unknown))}")
        return cls(**data)


def _geometry_params(case: Case) -> dict:
    return geometry_params(case.params)


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
                      sc: ScreeningConfig, x_param: str, cache: dict | None = None) -> dict:
    """한 형상의 받음각 스윕 저충실도 해석. 실패(형상 불가)면 ConfigError.

    비점성 해는 Re/Mach와 무관하므로 cache에 형상별로 저장해 조건(속도)마다 재사용한다.
    """
    # 자유류에서는 뒤집힌(다운포스) 날개가 똑바른 날개의 정확한 거울상이므로, 똑바른 형상을
    # 같은 받음각으로 풀면 양력 = 다운포스 크기, 항력·박리는 동일하다.
    params = {k: v for k, v in params.items() if k != "downforce"}
    key = repr(sorted(params.items()))  # 같은 형상은 조건(속도)이 달라도 비점성 해 재사용
    if cache is not None and key in cache:
        geom, sol = cache[key]
    else:
        geom = build_geometry(params, root)
        sol = solve_inviscid(geom, sc.n_side)
        if cache is not None:
            cache[key] = (geom, sol)
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
        "flap_sep_est": max(max(p.sep_upper[1:]) for p in valid or pts[:1]) if len(geom.elements) > 1 else 0.0,
        "min_clearance": min((f["clearance"] for f in geom.info["flaps"]), default=""),
        "_polar": pts,
        "_by_alpha": by_alpha,
    }


def condition_label(cond: tuple) -> str:
    return ", ".join(f"{k}={v}" for k, v in cond) or "all"


def screen(cfg: ProjectConfig, cases: list[Case], log=print) -> dict:
    sc = ScreeningConfig.from_dict(cfg.screening)
    metric = sc.metric or cfg.results.objective
    score_key = "ld_max_est" if metric == "max_ld" else "cl_max_est"
    x_param = cfg.results.x_param
    split_by = cfg.results.split_by
    if any("main_airfoil" not in c.params for c in cases):
        raise ConfigError("스크리닝에는 형상 변수(main_airfoil 등)가 필요합니다.")
    if any(x_param not in c.params for c in cases):
        raise ConfigError(f"스크리닝에는 받음각 변수 '{x_param}'가 필요합니다 ([results] x_param).")
    missing = [k for k in split_by if any(k not in c.params for c in cases)]
    if missing:
        raise ConfigError(f"[results] split_by 변수가 케이스에 없습니다: {', '.join(missing)}")

    # 형상 × 운용 조건(split_by, 예: 속도)별로 케이스 묶기.
    # 케이스 세트(예: 2단/3단)도 조건처럼 따로 선별한다: 구성이 다르면 L/D만으로 비교하기 어렵고
    # (3단은 양력이 크지만 항력도 커서 L/D로는 항상 불리) 구성 간 비교는 CFD 결과로 해야 하기 때문.
    cond_keys = (["case_set"] if any("case_set" in c.params for c in cases) else []) + list(split_by)
    groups: dict[tuple, list[Case]] = {}
    for c in cases:
        cond = tuple((k, c.params.get(k)) for k in cond_keys)
        groups.setdefault((mesh_name(_geometry_params(c)), cond, *_flow(c, cfg, sc)), []).append(c)

    all_keys = list(dict.fromkeys(k for c in cases for k in _geometry_params(c)))
    varying = [k for k in all_keys if len({repr(_geometry_params(c).get(k)) for c in cases}) > 1]
    family_keys = [k for k in varying if not any(fnmatch(k, pat) for pat in sc.protect)]

    geoms: list[dict] = []
    cache: dict = {}
    for i, ((gname, cond, re, mach), gcases) in enumerate(groups.items(), start=1):
        gp = _geometry_params(gcases[0])
        row = {"geometry": gname, "condition": condition_label(cond), **dict(cond),
               **{k: gp.get(k, "") for k in varying}, "reynolds": re, "mach": mach,
               "family": ", ".join(f"{k}={gp.get(k)}" for k in family_keys) or "all",
               "n_cases": len(gcases)}
        try:
            row.update(evaluate_geometry(gp, cfg.root, [float(c.params[x_param]) for c in gcases],
                                         re, mach, sc, x_param, cache))
            row["feasible"] = True
        except ConfigError as exc:
            row.update(feasible=False, reason=f"형상 불가: {exc}")
        row["_cases"] = gcases
        row["_cond"] = cond
        geoms.append(row)
        if i % 25 == 0 or i == len(groups):
            log(f"  저충실도 해석 {i}/{len(groups)} (형상 × 조건)")

    # 조건(속도 등)마다 따로 계열 선별: 특정 조건에서만 좋은 계열도 그 조건에서는 유지
    conditions = list(dict.fromkeys(g["_cond"] for g in geoms))
    summary, spot = [], []
    for cond in conditions:
        cg = [g for g in geoms if g["_cond"] == cond]
        families: dict[str, float] = {}
        for g in cg:
            if g["feasible"]:
                families[g["family"]] = max(families.get(g["family"], -math.inf), g[score_key])
        ranked = sorted(families.items(), key=lambda kv: kv[1], reverse=True)
        best = ranked[0][1] if ranked else 0.0
        threshold = best - abs(best) * sc.keep_within
        keep_family = {f for r, (f, s) in enumerate(ranked, start=1) if r <= sc.keep_top or s >= threshold}
        family_rank = {f: r for r, (f, _) in enumerate(ranked, start=1)}
        for g in cg:
            if not g["feasible"]:
                g["keep"] = False
                continue
            g["family_rank"] = family_rank[g["family"]]
            g["family_score"] = families[g["family"]]
            g["keep"] = g["family"] in keep_family
            if not g["keep"]:
                g["reason"] = (f"[{condition_label(cond)}] 계열 {score_key} {families[g['family']]:.3g} "
                               f"< 기준 {threshold:.3g} (상위 {sc.keep_top}개 밖)")
        # 스팟 체크: 제외된 계열 중 상위 계열의 최고 형상, 추정 최적 받음각 근처 케이스만 유지
        for fam in [f for f, _ in ranked if f not in keep_family][: sc.spot_check]:
            g = max((g for g in cg if g["feasible"] and g["family"] == fam), key=lambda g: g[score_key])
            alphas = sorted({float(c.params[x_param]) for c in g["_cases"]})
            targets = {g[f"{x_param}@ld_max_est"], g[f"{x_param}@cl_max_est"]}
            picks = {min(alphas, key=lambda a: abs(a - t)) for t in targets}
            g["spot_check"] = True
            spot.append({"geometry": g["geometry"], "condition": dict(cond),
                         "cases": [c.name for c in g["_cases"] if float(c.params[x_param]) in picks]})
        summary.append({"condition": condition_label(cond), "families": len(families),
                        "families_kept": len(keep_family),
                        "best": [g for g in sorted((g for g in cg if g["feasible"]),
                                                   key=lambda g: -g[score_key])][:3]})
    spot_cases = {n for s in spot for n in s["cases"]}

    case_rows, kept, pruned = [], [], {}
    for g in geoms:
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
            if c.name in spot_cases:
                reason = ""
            keep = not reason
            case_rows.append({"case": c.name, "geometry": g["geometry"], "condition": g["condition"],
                              x_param: a, "reynolds": g["reynolds"], "mach": g["mach"],
                              "cl_est": est.cl if est else "", "cd_est": est.cd if est else "",
                              "ld_est": est.ld if est else "",
                              "cl_main_est": est.cl_elements[0] if est else "",
                              "sep_main_est": est.sep_upper[0] if est else "",
                              "keep": keep, "reason": reason, "spot_check": c.name in spot_cases})
            if keep:
                kept.append(c.name)
            else:
                pruned[c.name] = reason

    out = cfg.output_dir / SCREEN_DIR
    if out.is_dir():
        shutil.rmtree(out)  # 이전 스크리닝의 조건별 그래프 폴더가 섞이지 않도록
    out.mkdir(parents=True)
    public = [{k: v for k, v in g.items() if not k.startswith("_")} for g in geoms]
    public.sort(key=lambda g: (g["condition"], not g["feasible"], -(g.get(score_key) or -math.inf)))
    _write_csv(out / "geometries.csv", public)
    _write_csv(out / "cases.csv", case_rows)
    selection = {"all_cases": [c.name for c in cases], "kept": kept, "pruned": pruned,
                 "spot_check": spot, "metric": score_key, "split_by": split_by, "settings": asdict(sc)}
    (out / SELECTION_FILE).write_text(json.dumps(selection, ensure_ascii=False, indent=1), encoding="utf-8")

    from .plots import plot_screening
    plots = []
    for cond in conditions:
        sub = out if len(conditions) == 1 else out / safe_name("_".join(f"{k}={v}" for k, v in cond))
        plots += plot_screening([g for g in geoms if g["_cond"] == cond], varying, score_key, x_param,
                                sc.protect, sub, title=condition_label(cond) if cond else "")
    return {"dir": out, "geometries": public, "kept": kept, "pruned": pruned, "plots": plots,
            "score_key": score_key, "conditions": summary, "spot_check": spot}


def check_spot(selection: dict, ranking: list[dict]) -> list[str]:
    """스팟 체크 형상의 CFD 결과가 같은 조건에서 유지된 형상보다 좋으면 경고 메시지를 만든다."""
    key = "ld_max" if selection.get("metric") == "ld_max_est" else "cl_max"
    spot_geoms = {(s["geometry"], tuple(sorted(s["condition"].items()))) for s in selection.get("spot_check") or []}
    warnings = []
    for s in selection.get("spot_check") or []:
        same = [r for r in ranking if r.get("mesh") and r.get(key) is not None
                and all(r.get(k) == v for k, v in s["condition"].items())]
        mine = [r for r in same if r["mesh"] == s["geometry"]]
        others = [r[key] for r in same
                  if (r["mesh"], tuple(sorted(s["condition"].items()))) not in spot_geoms]
        if mine and others and mine[0][key] > min(others):
            cond = condition_label(tuple(s["condition"].items()))
            warnings.append(
                f"[{cond}] 스팟 체크 형상 {s['geometry']}의 CFD {key} {mine[0][key]:.3g}가 유지된 형상의 "
                f"최저값 {min(others):.3g}보다 높습니다. 스크리닝이 좋은 형상을 놓쳤을 수 있으니 "
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
