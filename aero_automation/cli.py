"""명령줄 인터페이스.

    python -m aero_automation plan     # 케이스 목록 확인 + 폴더/저널 생성
    python -m aero_automation run      # 배치 실행 (완료된 케이스는 건너뜀)
    python -m aero_automation status   # 케이스별 진행 상태
    python -m aero_automation collect  # 결과 요약 CSV 생성
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from .cases import build_cases, filter_cases
from .config import ConfigError, load_config
from .expr import ExpressionError
from .results import collect
from .runner import build_command, prepare_case, read_status, run_all


def _load(args):
    cfg = load_config(args.config)
    cases = filter_cases(build_cases(cfg), args.only)
    if not cases:
        raise ConfigError("조건에 맞는 케이스가 없습니다.")
    return cfg, cases


def _print_table(cases, extra=None) -> None:
    keys = []
    for c in cases:
        keys += [k for k in c.params if k not in keys]
    header = ["case", "group", *keys] + ([extra[0]] if extra else [])
    rows = [
        [c.name, c.group, *(_fmt(c.params.get(k, "")) for k in keys)] + ([extra[1](c)] if extra else [])
        for c in cases
    ]
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    for line in [header, ["-" * w for w in widths], *rows]:
        print("  ".join(str(x).ljust(w) for x, w in zip(line, widths)))


def _fmt(value) -> str:
    return f"{value:.6g}" if isinstance(value, float) else str(value)


def cmd_plan(args) -> int:
    cfg, cases = _load(args)
    _print_table(cases)
    print(f"\n총 {len(cases)}개 케이스 → {cfg.output_dir}")
    if args.dry_run:
        return 0
    template_text = cfg.template.read_text(encoding="utf-8")
    for case in cases:
        prepare_case(cfg, case, template_text)
    print("케이스 폴더와 저널 파일(run.jou)을 생성했습니다.")
    try:
        print("실행 명령 예시:", " ".join(build_command(cfg, cases[0])))
    except ConfigError as exc:
        print(f"[경고] {exc}")
    return 0


def cmd_run(args) -> int:
    cfg, cases = _load(args)
    print(f"총 {len(cases)}개 케이스 실행 (동시 실행 {args.jobs or cfg.max_parallel}개)")
    results = run_all(cfg, cases, force=args.force, max_parallel=args.jobs)
    counts = Counter(r.state for r in results)
    print("\n결과: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    summary = collect(cfg, build_cases(cfg))
    print(f"요약: {summary}")
    return 1 if counts.get("failed") else 0


def cmd_status(args) -> int:
    _, cases = _load(args)

    def state(c):
        s = read_status(c)
        return s["state"] + (f" ({s['message']})" if s.get("message") else "")

    _print_table(cases, ("state", state))
    counts = Counter(read_status(c)["state"] for c in cases)
    print("\n" + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


def cmd_collect(args) -> int:
    cfg, cases = _load(args)
    out = collect(cfg, cases)
    print(f"요약 CSV 저장: {out}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="aero-auto", description="ANSYS 해석 케이스 배치 자동화")
    parser.add_argument("-c", "--config", default="project.toml", help="프로젝트 설정 파일 (기본: project.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, func, help_text):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--only", nargs="+", metavar="PATTERN",
                       help="케이스/그룹 이름 필터 (와일드카드 가능, 예: 'M0.3*')")
        p.set_defaults(func=func)
        return p

    p = add("plan", cmd_plan, "케이스 목록 확인 및 폴더/저널 생성")
    p.add_argument("--dry-run", action="store_true", help="목록만 출력하고 파일은 만들지 않음")
    p = add("run", cmd_run, "배치 실행")
    p.add_argument("--force", action="store_true", help="완료된 케이스도 다시 실행")
    p.add_argument("-j", "--jobs", type=int, help="동시 실행 케이스 수 (라이선스 수 고려)")
    add("status", cmd_status, "진행 상태 확인")
    add("collect", cmd_collect, "결과 요약 CSV 생성")

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, ExpressionError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n중단되었습니다. 다시 run 하면 완료된 케이스는 건너뜁니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
