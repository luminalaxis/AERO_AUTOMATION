"""Fluent 리포트 파일(.out) 파싱 및 결과 요약 CSV 생성."""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path

from .cases import Case
from .config import ProjectConfig
from .runner import read_status

_QUOTED = re.compile(r'"([^"]*)"')
_INDEX_COLUMNS = {"iteration", "time step", "flow-time", "flow time"}


def parse_report_file(path: Path) -> tuple[list[str], list[list[float]]]:
    """Fluent report-file 형식을 읽는다.

    예::

        "cl-rfile"
        "Iteration" "cl" "cd"
        ("Iteration" "cl" "cd")
        1 0.123 0.0456
    """
    headers: list[str] = []
    rows: list[list[float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            values = [float(tok) for tok in line.split()]
        except ValueError:
            names = _QUOTED.findall(line)
            if len(names) > 1:
                headers = names
            continue
        if headers and len(values) == len(headers):
            rows.append(values)
    return headers, rows


def summarize_report(path: Path, average_last: int = 1) -> dict[str, float]:
    headers, rows = parse_report_file(path)
    if not rows:
        return {}
    tail = rows[-max(1, average_last):]
    summary = {}
    for col, name in enumerate(headers):
        if name.lower() in _INDEX_COLUMNS:
            continue
        summary[name] = sum(r[col] for r in tail) / len(tail)
    summary["iter_done"] = rows[-1][0]
    return summary


def _wind_axes(row: dict, aoa_key: str) -> None:
    """물체축 힘계수(cx, cy)를 받음각으로 회전해 양력/항력계수(cl, cd)를 만든다.

    리포트 이름이 cx/cy 로 시작하면 같은 접미사끼리 짝을 짓는다 (예: cx-flap, cy-flap → cl-flap, cd-flap).
    받음각이 바뀌어도 Fluent 쪽 force vector를 매번 바꿀 필요가 없다.
    """
    aoa = row.get(aoa_key)
    if not isinstance(aoa, (int, float)):
        return
    a = math.radians(aoa)
    for key in [k for k in row if k.lower().startswith("cx")]:
        suffix = key[2:]
        ky = next((k for k in row if k.lower() == "cy" + suffix.lower()), None)
        cx, cy = row[key], row.get(ky)
        if ky is None or not isinstance(cx, float) or not isinstance(cy, float):
            continue
        row["cl" + suffix] = cy * math.cos(a) - cx * math.sin(a)
        row["cd" + suffix] = cx * math.cos(a) + cy * math.sin(a)


def summarize_case(cfg: ProjectConfig, case: Case) -> dict:
    status = read_status(case)
    row = {"case": case.name, "group": case.group, **case.params}
    if case.mesh is not None:
        row["mesh"] = case.mesh.name
    row["state"] = status.get("state", "pending")
    row["elapsed_min"] = round(status["elapsed_sec"] / 60, 2) if status.get("elapsed_sec") else ""
    files = sorted({p for pattern in cfg.results.report_files for p in case.dir.glob(pattern)})
    for path in files:
        for key, value in summarize_report(path, cfg.results.average_last).items():
            if key == "iter_done":
                row["iter_done"] = max(row.get("iter_done", 0), int(value))
                continue
            column = key if key not in row else f"{path.stem}.{key}"
            row[column] = value
    if cfg.results.wind_axes:
        _wind_axes(row, cfg.results.x_param)
    if isinstance(row.get("cl"), float) and isinstance(row.get("cd"), float) and row["cd"]:
        row["l/d"] = row["cl"] / row["cd"]
    return row


def _write_csv(path: Path, rows: list[dict]) -> Path:
    columns: list[str] = []
    for row in rows:
        columns += [k for k in row if k not in columns]
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: 엑셀에서 바로 열어도 한글이 깨지지 않도록
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def geometry_keys(cfg: ProjectConfig, cases: list[Case]) -> list[str]:
    """형상을 구분하는 변수: 케이스마다 값이 다른 변수 중 받음각/파생 변수를 뺀 것."""
    keys = []
    for k in cases[0].params:
        if k == cfg.results.x_param or k in cfg.derived:
            continue
        if len({repr(c.params.get(k)) for c in cases}) > 1:
            keys.append(k)
    return keys


def rank_geometries(rows: list[dict], keys: list[str], x_param: str, objective: str) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if r.get("state") == "done" and isinstance(r.get("cl"), float):
            groups.setdefault(tuple(r.get(k) for k in keys), []).append(r)
    ranking = []
    for values, rs in groups.items():
        best_cl = max(rs, key=lambda r: r["cl"])
        entry = {**dict(zip(keys, values)), "n_aoa": len(rs),
                 "cl_max": best_cl["cl"], f"{x_param}@cl_max": best_cl.get(x_param)}
        with_ld = [r for r in rs if isinstance(r.get("l/d"), float)]
        if with_ld:
            best_ld = max(with_ld, key=lambda r: r["l/d"])
            entry.update({"ld_max": best_ld["l/d"], f"{x_param}@ld_max": best_ld.get(x_param),
                          "cl@ld_max": best_ld["cl"], "cd@ld_max": best_ld.get("cd")})
        if "mesh" in rs[0]:
            entry["mesh"] = rs[0]["mesh"]
        ranking.append(entry)
    sort_key = "ld_max" if objective == "max_ld" else "cl_max"
    ranking.sort(key=lambda e: e.get(sort_key, float("-inf")), reverse=True)
    for i, e in enumerate(ranking, start=1):
        e["rank"] = i
    return ranking


def collect(cfg: ProjectConfig, cases: list[Case], plots: bool = True) -> dict:
    """summary.csv, ranking.csv, 케이스별 수렴 그래프, 요약 그래프 생성."""
    rows = [summarize_case(cfg, c) for c in cases]
    out_dir = cfg.output_dir
    result = {"summary": _write_csv(out_dir / cfg.results.summary_file, rows), "ranking": None,
              "top": [], "plots": []}

    keys = geometry_keys(cfg, cases)
    ranking = rank_geometries(rows, keys, cfg.results.x_param, cfg.results.objective)
    if ranking:
        result["ranking"] = _write_csv(out_dir / "ranking.csv", ranking)
        result["top"] = ranking[:5]

    if plots:
        from .plots import plot_convergence, plot_summary
        for case in cases:
            files = sorted({p for pat in cfg.results.report_files for p in case.dir.glob(pat)})
            if files:
                plot_convergence(files, case.dir / "convergence.png", title=case.name)
        result["plots"] = plot_summary(rows, out_dir / "plots", cfg.results.x_param, keys)
    return result
