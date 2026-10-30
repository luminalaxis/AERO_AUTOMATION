"""케이스 매트릭스 생성 및 분류."""

from __future__ import annotations

import csv
import itertools
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, ProjectConfig, safe_name
from .expr import evaluate
from .meshing import MeshJob, make_job


@dataclass
class Case:
    name: str
    group: str
    params: dict
    dir: Path
    mesh: MeshJob | None = None


def _coerce(value: str):
    value = value.strip()
    for conv in (int, float):
        try:
            return conv(value)
        except ValueError:
            pass
    return value


def _read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        raise ConfigError(f"케이스 CSV를 찾을 수 없습니다: {path}")
    # utf-8-sig: 엑셀에서 저장한 CSV의 BOM 처리
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = [
            {k.strip(): _coerce(v) for k, v in row.items() if k and v is not None and v.strip()}
            for row in csv.DictReader(f)
        ]
    return [r for r in rows if r]


def _sweep_rows(sweep: dict[str, list]) -> list[dict]:
    if not sweep:
        return [{}]
    keys = list(sweep)
    return [dict(zip(keys, combo)) for combo in itertools.product(*sweep.values())]


def _format(pattern: str, params: dict, what: str) -> str:
    try:
        return pattern.format(**params)
    except KeyError as exc:
        raise ConfigError(f"{what} 패턴 {pattern!r}에 없는 변수 {exc}가 쓰였습니다.") from exc


def build_cases(cfg: ProjectConfig) -> list[Case]:
    """CSV 행 × 스윕 조합으로 케이스 목록을 만든다."""
    base_rows = _read_csv(cfg.cases_csv) if cfg.cases_csv else [{}]
    cases: list[Case] = []
    seen: dict[str, int] = {}

    for idx, (row, combo) in enumerate(
        itertools.product(base_rows, _sweep_rows(cfg.sweep)), start=1
    ):
        params = {**cfg.constants, **row, **combo}
        for key, expression in cfg.derived.items():
            params[key] = evaluate(expression, params)

        name = params.pop("name", None)
        if name is None:
            name = _format(cfg.case_name, params, "케이스 이름") if cfg.case_name else f"case_{idx:03d}"
        name = safe_name(str(name))
        group = safe_name(_format(cfg.group, params, "그룹")) if cfg.group else ""

        if name in seen:
            raise ConfigError(
                f"케이스 이름 {name!r}이 중복됩니다 ({seen[name]}번째, {idx}번째). "
                "[cases] name 패턴에 구분 변수를 추가하세요."
            )
        seen[name] = idx

        case_dir = cfg.output_dir / group / name if group else cfg.output_dir / name
        mesh = make_job(params, cfg.mesh, cfg.output_dir / "_meshes") if cfg.mesh is not None else None
        cases.append(Case(name=name, group=group, params=params, dir=case_dir, mesh=mesh))
    return cases


def filter_cases(cases: list[Case], patterns: list[str] | None) -> list[Case]:
    """--only 옵션: 케이스/그룹 이름에 대한 와일드카드 필터."""
    if not patterns:
        return cases
    from fnmatch import fnmatch

    return [
        c for c in cases
        if any(fnmatch(c.name, p) or fnmatch(c.group, p) for p in patterns)
    ]
