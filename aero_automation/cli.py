"""명령줄 인터페이스.

    python -m aero_automation plan     # 케이스 목록 확인 + 폴더/저널 생성
    python -m aero_automation screen   # 저충실도 해석으로 민감도 확인 + 가망 없는 케이스 제외
    python -m aero_automation mesh     # 형상별 메쉬 생성 + 형상/메쉬 이미지
    python -m aero_automation run      # 배치 실행 (완료된 케이스는 건너뜀)
    python -m aero_automation status   # 케이스별 진행 상태
    python -m aero_automation collect  # 결과 요약 CSV, 순위, 그래프 생성
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from .cases import build_cases, filter_cases
from .config import ConfigError, load_config
from .expr import ExpressionError
from .results import collect
from .runner import build_command, ensure_meshes, mark_screened, prepare_case, read_status, run_all


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
    meshes = {c.mesh.name for c in cases if c.mesh is not None}
    if meshes:
        print(f"형상(메쉬) {len(meshes)}개 → {cfg.output_dir / '_meshes'}")
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


def _apply_selection(cfg, cases, use_all: bool):
    """screen 결과를 반영해 실행할 케이스만 남긴다."""
    from .screening import load_selection

    selection = None if use_all else load_selection(cfg)
    if selection is None:
        return cases
    known = set(selection["all_cases"])
    new = [c for c in cases if c.name not in known]
    if new:
        print(f"[경고] 스크리닝 이후 추가된 케이스 {len(new)}개는 스크리닝 없이 실행합니다. "
              "다시 screen 을 실행하는 것을 권장합니다.")
    pruned = selection["pruned"]
    for c in cases:
        if c.name in pruned:
            mark_screened(c, pruned[c.name])
    kept = [c for c in cases if c.name not in pruned]
    print(f"스크리닝 반영: {len(cases)}개 중 {len(cases) - len(kept)}개 제외 (전체 실행은 --all)")
    return kept


def cmd_screen(args) -> int:
    from .screening import screen

    cfg, cases = _load(args)
    print(f"저충실도 스크리닝: 케이스 {len(cases)}개")
    r = screen(cfg, cases)
    n_kept = len(r["kept"])
    infeasible = {g["geometry"] for g in r["geometries"] if not g["feasible"]}
    n_geom = len({g["geometry"] for g in r["geometries"]})
    print(f"\n형상 {n_geom}개 (형상 불가 {len(infeasible)}개)")
    from .screening import ScreeningConfig

    ground = any(c.params.get("ground_clearance") not in (None, "") for c in cases)
    show_stall = ScreeningConfig.from_dict(cfg.screening).prune_aoa
    if ground:
        # 지면 근처에서는 비점성 근사가 흡입을 크게 과대평가해 절대값이 실제의 몇 배로 나온다.
        # 오해를 막기 위해 절대값 대신 조건 안 최고 형상 대비 상대 점수만 보여준다.
        print("\n※ 지면 모드: 저충실도 추정의 절대값(Cl, L/D)은 실제보다 몇 배 크게 나오므로 표시하지 않습니다.\n"
              "   아래 점수는 같은 조건 안에서 최고 형상 대비 비율(100 = 최고)이며 순위 비교용입니다.")
    for c in r["conditions"]:
        print(f"\n[{c['condition']}] 계열 {c['families']}개 중 {c['families_kept']}개 유지. "
              f"상위 형상 ({r['score_key']} 기준, 저충실도 추정):")
        best = c["best"][0][r["score_key"]] if c["best"] else 1.0
        for g in c["best"]:
            stall = g["alpha_stall_est"]
            stall_txt = f"  실속 {'-' if stall is None else f'{stall:.1f}°'}" if show_stall else ""
            if ground:
                print(f"  {g['geometry']:<50} Re {g['reynolds']:,.0f}  상대 점수 {g[r['score_key']] / best * 100:5.1f}")
            else:
                print(f"  {g['geometry']:<45} Re {g['reynolds']:,.0f}  Cl max {g['cl_max_est']:.2f}  "
                      f"L/D max {g['ld_max_est']:.1f}{stall_txt}")
    print(f"\n케이스 {len(cases)}개 → {n_kept}개 해석 ({(1 - n_kept / len(cases)) * 100:.0f}% 절감)")
    if r["spot_check"]:
        n_spot = sum(len(s["cases"]) for s in r["spot_check"])
        print(f"  (제외 계열 검증용 스팟 체크 {n_spot}개 포함)")
    print(f"\n결과: {r['dir']}  (geometries.csv, cases.csv, sensitivity_*.png, screening_*.png)")
    print("이후 run 은 제외된 케이스를 자동으로 건너뜁니다 (전체 실행은 run --all).")
    return 0


def cmd_run(args) -> int:
    cfg, cases = _load(args)
    cases = _apply_selection(cfg, cases, args.all)
    print(f"총 {len(cases)}개 케이스 실행 (동시 실행 {args.jobs or cfg.max_parallel}개)")
    results = run_all(cfg, cases, force=args.force, max_parallel=args.jobs)
    counts = Counter(r.state for r in results)
    print("\n결과: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    _print_collect(collect(cfg, build_cases(cfg)), cfg)
    return 1 if counts.get("failed") else 0


def cmd_mesh(args) -> int:
    cfg, cases = _load(args)
    if cfg.mesh is None:
        raise ConfigError("project.toml에 [mesh] 섹션이 없습니다.")
    errors = ensure_meshes(cfg, _apply_selection(cfg, cases, args.all), force=args.force)
    print(f"\n메쉬 폴더: {cfg.output_dir / '_meshes'}  (형상별 mesh.msh, geometry.png, mesh*.png)")
    return 1 if errors else 0


def _print_collect(result: dict, cfg) -> None:
    print(f"요약: {result['summary']}")
    if result["ranking"]:
        key = "ld_max" if cfg.results.objective == "max_ld" else "cl_max"
        print(f"형상 순위 ({key} 기준): {result['ranking']}")
        for e in result["top"]:
            desc = ", ".join(f"{k}={v}" for k, v in e.items()
                             if k not in ("rank", "n_aoa", "mesh") and "@" not in k and not k.endswith("_max"))
            extra = f"L/D max {e['ld_max']:.2f}" if "ld_max" in e else ""
            print(f"  {e['rank']}. {desc}  Cl max {e['cl_max']:.3f}  {extra}")
    if result["plots"]:
        print(f"그래프: {result['plots'][0].parent}")
    for w in result.get("warnings", []):
        print(f"[경고] {w}")


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
    _print_collect(collect(cfg, cases), cfg)
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
    add("screen", cmd_screen, "저충실도 해석 + 민감도 그래프 + 케이스 선별")
    p = add("mesh", cmd_mesh, "형상별 메쉬 생성 (이미 있으면 건너뜀)")
    p.add_argument("--all", action="store_true", help="스크리닝 결과 무시하고 전체 형상 메쉬 생성")
    p.add_argument("--force", action="store_true", help="기존 메쉬도 다시 생성")
    p = add("run", cmd_run, "배치 실행 (필요한 메쉬 자동 생성)")
    p.add_argument("--force", action="store_true", help="완료된 케이스도 다시 실행")
    p.add_argument("-j", "--jobs", type=int, help="동시 실행 케이스 수 (라이선스 수 고려)")
    p.add_argument("--all", action="store_true", help="스크리닝 결과 무시하고 전체 케이스 실행")
    add("status", cmd_status, "진행 상태 확인")
    add("collect", cmd_collect, "결과 요약 CSV, 형상 순위, 그래프 생성")

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
