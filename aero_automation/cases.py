"""케이스 매트릭스 생성 및 분류."""

from __future__ import annotations

import csv
import itertools
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, ProjectConfig, safe_name
from .expr import evaluate
from .meshing import MeshJob, make_job, mesh_name


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


def _expand(cfg: ProjectConfig):
    """(params, 이름 패턴, 그룹 패턴) 생성.

    [[cases.set]]가 있으면 세트마다 (공통 [cases.sweep] × 세트 sweep)을 펼쳐 합친다.
    값 우선순위: [constants] < 세트 constants < CSV 행 < 공통 sweep < 세트 sweep.
    세트 label은 케이스 변수 case_set 으로 쓸 수 있다.
    """
    base_rows = _read_csv(cfg.cases_csv) if cfg.cases_csv else [{}]
    sets = cfg.case_sets or [{}]
    for s in sets:
        extra = {"case_set": s["label"]} if "label" in s else {}
        for row, common, own in itertools.product(base_rows, _sweep_rows(cfg.sweep),
                                                  _sweep_rows(s.get("sweep", {}))):
            params = {**cfg.constants, **s.get("constants", {}), **extra, **row, **common, **own}
            yield params, s.get("name", cfg.case_name), s.get("group", cfg.group)


def build_cases(cfg: ProjectConfig) -> list[Case]:
    """CSV 행 × 스윕 조합(케이스 세트별)으로 케이스 목록을 만든다."""
    cases: list[Case] = []
    seen: dict[str, int] = {}

    for idx, (params, name_pattern, group_pattern) in enumerate(_expand(cfg), start=1):
        for key, expression in cfg.derived.items():
            params[key] = evaluate(expression, params)

        name = params.pop("name", None)
        if name is None:
            name = _format(name_pattern, params, "케이스 이름") if name_pattern else f"case_{idx:03d}"
        name = safe_name(str(name))
        group = safe_name(_format(group_pattern, params, "그룹")) if group_pattern else ""

        if name in seen:
            raise ConfigError(
                f"케이스 이름 {name!r}이 중복됩니다 ({seen[name]}번째, {idx}번째). "
                "[cases] name 패턴에 구분 변수를 추가하세요."
            )
        seen[name] = idx

        case_dir = cfg.output_dir / group / name if group else cfg.output_dir / name
        cases.append(Case(name=name, group=group, params=params, dir=case_dir))

    _set_flow_angle(cases, cfg.results.x_param)
    if cfg.mesh is not None:
        _attach_meshes(cases, cfg)
    return cases


def _set_flow_angle(cases: list[Case], x_param: str) -> None:
    """Fluent 유입 각도 flow_angle [deg]. 다운포스(뒤집힌) 날개는 양의 받음각이 다운포스를
    키우는 방향이 되도록 flow_angle = -aoa 로 둔다. 저널은 ${flow_angle}을 쓴다.
    지면이 있으면 받음각은 형상 회전으로 반영하고 유동은 지면과 평행하므로 flow_angle = 0."""
    for c in cases:
        a = c.params.get(x_param)
        if isinstance(a, (int, float)) and "flow_angle" not in c.params:
            if c.params.get("ground_clearance") not in (None, ""):
                c.params["flow_angle"] = 0.0
            else:
                c.params["flow_angle"] = -a if c.params.get("downforce") else a


def _attach_meshes(cases: list[Case], cfg: ProjectConfig) -> None:
    """형상이 같은 케이스는 메쉬 하나를 공유한다.

    케이스 변수 reynolds가 형상 안에서 다르면(예: 속도 스윕) 가장 큰 값으로 첫 셀 높이를 잡아
    모든 조건에서 y+ 가 목표 이하가 되게 한다.
    """
    meshes_dir = cfg.output_dir / "_meshes"
    max_re: dict[str, float] = {}
    for c in cases:
        if "reynolds" in c.params and "main_airfoil" in c.params:
            key = mesh_name(c.params)
            max_re[key] = max(max_re.get(key, 0.0), float(c.params["reynolds"]))
    for c in cases:
        params = c.params
        if "reynolds" in params and "main_airfoil" in params:
            key = mesh_name(params)
            params = {**params, "reynolds": max_re[key]}
        c.mesh = make_job(params, cfg.mesh, meshes_dir)


def filter_cases(cases: list[Case], patterns: list[str] | None) -> list[Case]:
    """--only 옵션: 케이스/그룹 이름에 대한 와일드카드 필터."""
    if not patterns:
        return cases
    from fnmatch import fnmatch

    return [
        c for c in cases
        if any(fnmatch(c.name, p) or fnmatch(c.group, p) for p in patterns)
    ]


def split_part(cases: list[Case], part: str) -> list[Case]:
    """--part K/N: 형상(메쉬) 단위로 케이스를 N등분해 K번째 몫만 남긴다.

    같은 형상의 케이스(받음각·Re만 다름)는 항상 같은 몫에 들어가 메쉬를 한 사람만 만든다.
    형상 이름을 정렬해 번갈아 배정하므로 설정 파일이 같으면 누가 실행해도 같은 결과가 나온다.
    """
    try:
        k, n = (int(x) for x in part.split("/"))
    except ValueError as exc:
        raise ConfigError(f"--part 형식은 K/N 입니다 (예: 2/4): {part!r}") from exc
    if not 1 <= k <= n:
        raise ConfigError(f"--part K/N 에서 1 ≤ K ≤ N 이어야 합니다: {part!r}")

    def key(c: Case) -> str:
        if c.mesh is not None:
            return c.mesh.name
        return str(c.params.get("main_airfoil", c.group or c.name))

    keys = sorted({key(c) for c in cases})
    mine = {kk for i, kk in enumerate(keys) if i % n == k - 1}
    return [c for c in cases if key(c) in mine]
