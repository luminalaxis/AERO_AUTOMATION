"""단계적 실행: 받음각 범위를 CFD로 정한 뒤 나머지 형상은 그 주변만 푼다.

1단계 (run --stage1): 스팟 체크 + 남은 계열마다 조건(세트·속도 등)별 점수 1위 형상(대표 형상)의 전체 받음각.
   --per ground_clearance 처럼 주면 그 변수 값마다 대표 형상을 따로 뽑는다 (지상고에 따라 최대 각도가 다를 때).
2단계 (narrow-aoa): 1단계 CFD 결과로 그룹(계열 × 조건 [× per 변수])마다 대표 형상의 최대 Cl(또는 L/D) 받음각을 찾고,
   같은 그룹의 나머지 형상은 그 받음각을 가운데로 한 keep개만 남긴다. 나머지는 selection.json의 제외 목록에 넣어
   이후 run 이 건너뛴다. 이미 끝난 케이스와 스팟 체크는 그대로 둔다.
   screen 을 다시 하면 selection.json 이 새로 만들어지므로 narrow-aoa 도 다시 실행한다.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from .cases import Case
from .config import ConfigError, ProjectConfig
from .screening import SCREEN_DIR, SELECTION_FILE, load_selection

DIVERGED = "diverged"


def _read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        raise ConfigError(f"스크리닝 결과가 없습니다: {path}. 먼저 aero-auto screen 을 실행하세요.")
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _screening(cfg: ProjectConfig) -> tuple[dict, dict, dict]:
    selection = load_selection(cfg)
    if selection is None:
        raise ConfigError("스크리닝 결과(selection.json)가 없습니다. 먼저 aero-auto screen 을 실행하세요.")
    out = cfg.output_dir / SCREEN_DIR
    case_rows = {r["case"]: r for r in _read_csv(out / "cases.csv")}
    geo_rows = {(r["geometry"], r["condition"]): r for r in _read_csv(out / "geometries.csv")}
    return selection, case_rows, geo_rows


def _groups(cfg: ProjectConfig, cases: list[Case], per: list[str]):
    """남은(스팟 체크 제외) 케이스를 그룹별·형상별로 묶고 그룹마다 대표 형상(스크리닝 점수 1위)을 고른다.

    반환: selection, spot(케이스 이름 집합), groups {그룹 키: {형상: [케이스]}}, reps {그룹 키: 대표 형상}
    """
    selection, case_rows, geo_rows = _screening(cfg)
    score_key = selection.get("metric", "cl_max_est")
    spot = {n for s in selection.get("spot_check") or [] for n in s["cases"]}
    pruned = selection["pruned"]
    by_name = {c.name: c for c in cases}
    missing = [k for k in per if any(k not in c.params for c in cases)]
    if missing:
        raise ConfigError(f"--per 변수가 케이스에 없습니다: {', '.join(missing)}")
    groups: dict[tuple, dict[str, list[Case]]] = {}
    for name, c in by_name.items():
        if name in pruned or name in spot or name not in case_rows:
            continue
        r = case_rows[name]
        g = geo_rows.get((r["geometry"], r["condition"]))
        if g is None:
            continue
        key = (g["family"], r["condition"], *(f"{k}={c.params.get(k)}" for k in per))
        groups.setdefault(key, {}).setdefault(r["geometry"], []).append(c)

    def score(key, geometry):
        try:
            return float(geo_rows[(geometry, key[1])].get(score_key) or "-inf")
        except ValueError:
            return -math.inf

    reps = {key: max(geoms, key=lambda geo: score(key, geo)) for key, geoms in groups.items()}
    return selection, spot, groups, reps


def stage1_cases(cfg: ProjectConfig, cases: list[Case], per: list[str] | None = None) -> list[Case]:
    """1단계 케이스: 스팟 체크 + 그룹마다 대표 형상의 모든 받음각 (원래 순서 유지)."""
    per = per or []
    _, spot, groups, reps = _groups(cfg, cases, per)
    names = set(spot)
    for key, geometry in reps.items():
        names |= {c.name for c in groups[key][geometry]}
    return [c for c in cases if c.name in names]


def _metric(cfg: ProjectConfig) -> str:
    return "l/d" if cfg.results.objective == "max_ld" else "cl"


def narrow_aoa(cfg: ProjectConfig, cases: list[Case], keep: int = 3, per: list[str] | None = None,
               dry_run: bool = False) -> dict:
    """1단계 결과로 나머지 형상의 받음각을 대표 형상 최대 각도 주변 keep개로 줄인다 (selection.json 갱신)."""
    from .results import summarize_case
    from .runner import read_status

    if keep < 1:
        raise ConfigError("--keep 은 1 이상이어야 합니다.")
    x = cfg.results.x_param
    metric = _metric(cfg)
    per = per if per is not None else selection_per(cfg)
    selection, spot, groups, reps = _groups(cfg, cases, per)

    report, removed = [], {}
    for key, geoms in groups.items():
        rep = reps[key]
        label = " / ".join(key[1:]) + f" / {key[0]}"
        rows = [summarize_case(cfg, c) for c in geoms[rep]]
        good = [r for r in rows if r.get("state") == "done" and isinstance(r.get(metric), float)
                and r.get("conv_state") != DIVERGED and isinstance(r.get(x), (int, float))]
        aoas = sorted({float(c.params[x]) for cs in geoms.values() for c in cs})
        entry = {"group": label, "representative": rep, "n_done": len(good), "n_aoa": len(aoas)}
        if len(good) < min(3, len(aoas)):
            entry["status"] = "데이터 부족 (대표 형상의 1단계 결과가 3개 미만)"
            report.append(entry)
            continue
        best = max(good, key=lambda r: r[metric])
        peak = float(best[x])
        i = aoas.index(peak)
        n = min(keep, len(aoas))
        start = min(max(i - (n - 1) // 2, 0), len(aoas) - n)  # 최대 각도를 가운데로, 범위 끝에서는 안쪽으로 밀기
        window = aoas[start:start + n]
        entry.update(peak=peak, peak_value=best[metric], window=window,
                     edge=peak in (aoas[0], aoas[-1]))
        cut = 0
        for geometry, cs in geoms.items():
            if geometry == rep:
                continue
            for c in cs:
                a = float(c.params[x])
                if a in window or read_status(c).get("state") == "done":
                    continue
                removed[c.name] = (f"받음각 범위 밖: 대표 형상 {rep}의 CFD 최대 {metric} 받음각 {peak:g}° "
                                   f"→ {', '.join(f'{v:g}' for v in window)}° 만 해석")
                cut += 1
        entry["removed"] = cut
        entry["status"] = "줄임"
        report.append(entry)

    before = len(selection["kept"])
    if not dry_run and removed:
        selection["pruned"].update(removed)
        selection["kept"] = [n for n in selection["kept"] if n not in removed]
        if "priority" in selection:
            selection["priority"] = [n for n in selection["priority"] if n not in removed]
        selection.setdefault("narrowed", []).append(
            {"keep": keep, "per": per, "metric": metric, "removed": len(removed)})
        path = cfg.output_dir / SCREEN_DIR / SELECTION_FILE
        path.write_text(json.dumps(selection, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"groups": report, "removed": len(removed), "kept_before": before,
            "kept_after": before - len(removed), "metric": metric, "dry_run": dry_run, "spot": len(spot)}


def selection_per(cfg: ProjectConfig) -> list[str]:
    """run --stage1 에서 쓴 --per 변수 (narrow-aoa 의 기본값)."""
    selection = load_selection(cfg) or {}
    return list(selection.get("stage1_per") or [])


def remember_per(cfg: ProjectConfig, per: list[str]) -> None:
    selection = load_selection(cfg)
    if selection is None:
        return
    selection["stage1_per"] = list(per)
    path = cfg.output_dir / SCREEN_DIR / SELECTION_FILE
    path.write_text(json.dumps(selection, ensure_ascii=False, indent=1), encoding="utf-8")
