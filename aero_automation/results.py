"""Fluent 리포트 파일(.out) 파싱 및 결과 요약 CSV 생성."""

from __future__ import annotations

import csv
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


def summarize_case(cfg: ProjectConfig, case: Case) -> dict:
    status = read_status(case)
    row = {"case": case.name, "group": case.group, **case.params,
           "state": status.get("state", "pending"),
           "elapsed_min": round(status.get("elapsed_sec", 0) / 60, 2) if status.get("elapsed_sec") else ""}
    files = sorted({p for pattern in cfg.results.report_files for p in case.dir.glob(pattern)})
    for path in files:
        for key, value in summarize_report(path, cfg.results.average_last).items():
            if key == "iter_done":
                row["iter_done"] = max(row.get("iter_done", 0), int(value))
                continue
            column = key if key not in row else f"{path.stem}.{key}"
            row[column] = value
    return row


def collect(cfg: ProjectConfig, cases: list[Case], out: Path | None = None) -> Path:
    rows = [summarize_case(cfg, c) for c in cases]
    columns: list[str] = []
    for row in rows:
        columns += [k for k in row if k not in columns]
    out = out or cfg.output_dir / cfg.results.summary_file
    out.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: 엑셀에서 바로 열어도 한글이 깨지지 않도록
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return out
