"""케이스 폴더 준비 및 솔버 배치 실행."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .cases import Case
from .config import ConfigError, ProjectConfig
from .template import render

IS_WINDOWS = os.name == "nt"
JOURNAL_NAME = "run.jou"
LOG_NAME = "solver.log"
STATUS_NAME = "status.json"
PARAMS_NAME = "case.json"

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _posix(path: Path) -> str:
    # Fluent는 Windows에서도 슬래시 경로를 받아들이고, 역슬래시는 이스케이프 문제를 만든다.
    return path.resolve().as_posix()


def case_variables(cfg: ProjectConfig, case: Case) -> dict:
    variables = {
        **case.params,
        "case_name": case.name,
        "group": case.group,
        "case_dir": _posix(case.dir),
        "project_dir": _posix(cfg.root),
        "nprocs": cfg.solver.processes,
    }
    if case.mesh is not None:
        variables["mesh_file"] = _posix(case.mesh.mesh_file)
        variables["mesh_name"] = case.mesh.name
    return variables


def ensure_meshes(cfg: ProjectConfig, cases: list[Case], force: bool = False) -> dict[str, str]:
    """케이스에 필요한 메쉬를 생성한다 (같은 형상은 한 번만). 반환: {메쉬 이름: 오류 메시지}."""
    from .meshing import generate_mesh

    jobs = {c.mesh.name: c.mesh for c in cases if c.mesh is not None}
    todo = [j for j in jobs.values() if force or not j.is_current()]
    if jobs:
        log(f"메쉬: 형상 {len(jobs)}개 중 {len(todo)}개 생성 필요")
    errors = {}
    for i, job in enumerate(todo, start=1):
        start = time.monotonic()
        try:
            info = generate_mesh(job, cfg.root)
        except ConfigError as exc:
            errors[job.name] = str(exc)
            log(f"  [{i}/{len(todo)}] {job.name:<45} 실패 - {exc}")
            continue
        log(f"  [{i}/{len(todo)}] {job.name:<45} {info['cells']:>8,} cells  "
            f"품질 {info['min_quality']:.2f}  {time.monotonic() - start:4.1f}s")
    return errors


def find_fluent() -> str:
    """AWP_ROOTxxx 환경변수(ANSYS 설치 시 자동 등록)에서 최신 버전 Fluent를 찾는다."""
    roots = sorted(
        ((int(m.group(1)), v) for k, v in os.environ.items() if (m := re.fullmatch(r"AWP_ROOT(\d+)", k))),
        reverse=True,
    )
    for _, root in roots:
        exe = (
            Path(root, "fluent", "ntbin", "win64", "fluent.exe")
            if IS_WINDOWS
            else Path(root, "fluent", "bin", "fluent")
        )
        if exe.is_file():
            return str(exe)
    found = shutil.which("fluent")
    if found:
        return found
    raise ConfigError(
        "Fluent 실행 파일을 찾지 못했습니다. project.toml의 [solver] executable에 경로를 지정하세요.\n"
        r"  예) C:\Program Files\ANSYS Inc\v242\fluent\ntbin\win64\fluent.exe"
    )


def build_command(cfg: ProjectConfig, case: Case) -> list[str]:
    s = cfg.solver
    if s.kind == "custom":
        variables = {
            **case_variables(cfg, case),
            "exe": s.executable or "",
            "journal": JOURNAL_NAME,
            "python": sys.executable,
        }
        return [render(part, variables, "[solver] command") for part in s.command]

    exe = s.executable or find_fluent()
    cmd = [exe, s.mode, f"-t{s.processes}", "-i", JOURNAL_NAME]
    if s.graphics and not s.gui:
        # GUI 없이 그래픽 기능만 살려 save-picture 가능하게
        cmd += ["-hidden"] if IS_WINDOWS else ["-gu", "-driver", "null"]
    elif not s.gui:
        cmd.append("-g")
    if IS_WINDOWS:
        cmd.append("-wait")  # Windows 런처가 솔버 종료까지 기다리도록
    return cmd + list(s.extra_args)


def prepare_case(cfg: ProjectConfig, case: Case, template_text: str | None = None) -> Path:
    if template_text is None:
        template_text = cfg.template.read_text(encoding="utf-8")
    case.dir.mkdir(parents=True, exist_ok=True)
    journal = render(template_text, case_variables(cfg, case), str(cfg.template.name))
    journal_path = case.dir / JOURNAL_NAME
    journal_path.write_text(journal, encoding="utf-8")
    (case.dir / PARAMS_NAME).write_text(
        json.dumps({"name": case.name, "group": case.group, "params": case.params},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return journal_path


def read_status(case: Case) -> dict:
    path = case.dir / STATUS_NAME
    if not path.is_file():
        return {"state": "pending"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"state": "unknown"}


def _write_status(case: Case, **status) -> None:
    (case.dir / STATUS_NAME).write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def mark_screened(case: Case, reason: str) -> None:
    """스크리닝에서 제외된 케이스 표시 (이미 해석이 끝난 케이스는 그대로 둔다)."""
    if read_status(case).get("state") == "done":
        return
    case.dir.mkdir(parents=True, exist_ok=True)
    _write_status(case, state="screened", message=reason)


def _kill_tree(proc: subprocess.Popen) -> None:
    # Fluent는 런처 아래로 cortex/MPI 프로세스를 띄우므로 트리 전체를 종료해야 한다.
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _scan_errors(log_path: Path, patterns: list[str]) -> str | None:
    if not log_path.is_file() or not patterns:
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        for pattern in patterns:
            if pattern.lower() in line.lower():
                return line.strip()
    return None


@dataclass
class RunResult:
    case: Case
    state: str
    message: str = ""
    elapsed: float = 0.0


def run_case(cfg: ProjectConfig, case: Case, template_text: str | None = None,
             force: bool = False) -> RunResult:
    if not force and read_status(case).get("state") == "done":
        return RunResult(case, "skipped", "이미 완료됨")

    if case.mesh is not None and not case.mesh.mesh_file.is_file():
        from .meshing import ERROR_FILE

        error_file = case.mesh.dir / ERROR_FILE
        reason = error_file.read_text(encoding="utf-8") if error_file.is_file() else "메쉬 파일 없음"
        msg = f"메쉬 실패 ({case.mesh.name}): {reason}"
        case.dir.mkdir(parents=True, exist_ok=True)
        _write_status(case, state="failed", message=msg)
        return RunResult(case, "failed", msg)

    # 이전 실행 결과 정리 (Fluent가 기존 파일 덮어쓰기 확인을 물으면 배치가 멈춤)
    for pattern in [*cfg.results.report_files, "*.png"]:
        for old in case.dir.glob(pattern):
            old.unlink()
    prepare_case(cfg, case, template_text)
    cmd = build_command(cfg, case)
    log_path = case.dir / LOG_NAME
    timeout = cfg.solver.timeout_minutes * 60 if cfg.solver.timeout_minutes else None

    _write_status(case, state="running", command=cmd, started=time.strftime("%Y-%m-%d %H:%M:%S"))
    start = time.monotonic()
    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        popen_kwargs = {"start_new_session": True} if not IS_WINDOWS else {}
        try:
            proc = subprocess.Popen(cmd, cwd=case.dir, stdout=log_file,
                                    stderr=subprocess.STDOUT, **popen_kwargs)
        except OSError as exc:
            _write_status(case, state="failed", command=cmd, message=f"실행 실패: {exc}")
            return RunResult(case, "failed", f"실행 실패: {exc}")
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            proc.wait()
            elapsed = time.monotonic() - start
            msg = f"시간 초과 ({cfg.solver.timeout_minutes}분)"
            _write_status(case, state="failed", command=cmd, message=msg, elapsed_sec=round(elapsed, 1))
            return RunResult(case, "failed", msg, elapsed)
        except KeyboardInterrupt:
            _kill_tree(proc)
            _write_status(case, state="interrupted", command=cmd)
            raise

    elapsed = time.monotonic() - start
    if returncode != 0:
        state, msg = "failed", f"종료 코드 {returncode}"
    elif err := _scan_errors(log_path, cfg.results.error_patterns):
        state, msg = "failed", f"로그 오류: {err}"
    else:
        state, msg = "done", ""
    _write_status(case, state=state, command=cmd, returncode=returncode,
                  message=msg, elapsed_sec=round(elapsed, 1))
    return RunResult(case, state, msg, elapsed)


def run_all(cfg: ProjectConfig, cases: list[Case], force: bool = False,
            max_parallel: int | None = None) -> list[RunResult]:
    template_text = cfg.template.read_text(encoding="utf-8")
    ensure_meshes(cfg, [c for c in cases if force or read_status(c).get("state") != "done"])
    workers = max(1, max_parallel or cfg.max_parallel)
    total = len(cases)
    results: list[RunResult] = []

    def report(i: int, r: RunResult) -> None:
        extra = f" - {r.message}" if r.message else ""
        log(f"[{i}/{total}] {r.case.name:<30} {r.state:<8} {r.elapsed/60:6.1f}분{extra}")

    if workers == 1:
        for i, case in enumerate(cases, start=1):
            log(f"[{i}/{total}] {case.name} 실행 중...")
            r = run_case(cfg, case, template_text, force)
            report(i, r)
            results.append(r)
        return results

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_case, cfg, c, template_text, force): c for c in cases}
        for i, fut in enumerate(as_completed(futures), start=1):
            r = fut.result()
            report(i, r)
            results.append(r)
    return results
