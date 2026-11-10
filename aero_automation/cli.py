"""명령줄 인터페이스.

    python -m aero_automation plan     # 케이스 목록 확인 + 폴더/저널 생성
    python -m aero_automation screen   # 저충실도 해석으로 민감도 확인 + 가망 없는 케이스 제외
    python -m aero_automation mesh     # 형상별 메쉬 생성 + 형상/메쉬 이미지
    python -m aero_automation check    # Fluent 실행 전 점검 (베이스 케이스, 메쉬, 저널, 경로, 디스크·시간)
    python -m aero_automation run      # 배치 실행 (완료된 케이스는 건너뜀, --stage1: 1단계만)
    python -m aero_automation narrow-aoa  # 1단계 결과로 나머지 형상의 받음각을 최대 각도 주변만 남김
    python -m aero_automation status   # 케이스별 진행 상태
    python -m aero_automation collect  # 결과 요약 CSV, 순위, 그래프 생성
    python -m aero_automation gci      # 메쉬 독립성 확인 (mesh_level 3단계 → Richardson 외삽, GCI)
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from .banner import print_banner
from .console import ColorArgumentParser, clear_screen
from .cases import build_cases, filter_cases, partition_cases
from .config import ConfigError, load_config
from .expr import ExpressionError
from .results import collect
from .runner import build_command, ensure_meshes, mark_screened, prepare_case, read_status, run_all


def _load(args):
    cfg = load_config(args.config)
    cases = partition_cases(filter_cases(build_cases(cfg), args.only), getattr(args, "part", None))
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
        n_spot = len({n for s in r["spot_check"] for n in s["cases"]})
        n_geo = len({s["geometry"] for s in r["spot_check"]})
        print(f"  (제외 계열 검증용 스팟 체크 {n_spot}개 포함: 형상 {n_geo}개 × 대표 받음각. "
              "run --priority 가 이 케이스부터 실행, 목록은 cases.csv의 spot_check 열)")
    for w in r.get("warnings", []):
        print(f"\n[주의] {w}")
    print(f"\n결과: {r['dir']}  (geometries.csv, cases.csv, sensitivity_*.png, screening_*.png)")
    print("이후 run 은 제외된 케이스를 자동으로 건너뜁니다 (전체 실행은 run --all).")
    return 0


def _order_cases(cfg, cases, priority: bool, limit: int | None):
    """run --priority: 스크리닝 우선순위(스팟 체크 → 유망한 계열·형상) 순서로, --limit N: 남은 케이스 중 앞의 N개만."""
    if priority:
        from .screening import load_selection

        selection = load_selection(cfg) or {}
        order = {n: i for i, n in enumerate(selection.get("priority", []))}
        if not order:
            print("[경고] 스크리닝 우선순위가 없습니다 (screen 을 다시 실행하세요). 원래 순서로 실행합니다.")
        cases = sorted(cases, key=lambda c: order.get(c.name, len(order)))
    if limit:
        todo = [c for c in cases if read_status(c).get("state") != "done"]
        cases = todo[:limit]
        print(f"--limit {limit}: 남은 케이스 {len(todo)}개 중 {len(cases)}개만 실행")
    return cases


def cmd_run(args) -> int:
    cfg, cases = _load(args)
    cases = _apply_selection(cfg, cases, args.all)
    if args.stage1:
        from .staging import remember_per, stage1_cases

        n_before = len(cases)
        cases = stage1_cases(cfg, cases, args.per)
        remember_per(cfg, args.per or [])
        per = f", 대표 형상을 {', '.join(args.per)} 값마다" if args.per else ""
        print(f"1단계: {n_before}개 중 {len(cases)}개 (스팟 체크 + 계열·조건별 대표 형상의 전체 받음각{per}). "
              "끝나면 aero-auto narrow-aoa 로 나머지 형상의 받음각을 줄이세요.")
    cases = _order_cases(cfg, cases, args.priority, args.limit)
    print(f"총 {len(cases)}개 케이스 실행 (동시 실행 {args.jobs or cfg.max_parallel}개)")
    results = run_all(cfg, cases, force=args.force, max_parallel=args.jobs)
    counts = Counter(r.state for r in results)
    print("\n결과: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    _print_collect(collect(cfg, build_cases(cfg)), cfg)
    return 1 if counts.get("failed") else 0


def cmd_narrow_aoa(args) -> int:
    from .staging import narrow_aoa

    cfg, cases = _load(args)
    r = narrow_aoa(cfg, cases, keep=args.keep, per=args.per, dry_run=args.dry_run)
    print(f"대표 형상의 CFD 최대 {r['metric']} 받음각 주변 {args.keep}개만 남깁니다 (그룹 = 계열 × 조건"
          + (f" × {', '.join(args.per)}" if args.per else "") + ")\n")
    edge, lacking = [], []
    for g in r["groups"]:
        if g["status"] != "줄임":
            lacking.append(g)
            continue
        win = ", ".join(f"{v:g}" for v in g["window"])
        print(f"  {g['group'][:90]:<90} 최대 {g['peak']:g}° ({r['metric']} {g['peak_value']:.3f}) → {win}°  제외 {g['removed']}개")
        if g["edge"]:
            edge.append(g)
    if lacking:
        print(f"\n[참고] 대표 형상 결과가 부족해 그대로 둔 그룹 {len(lacking)}개 (run --stage1 이 끝난 뒤 다시 실행):")
        for g in lacking[:5]:
            print(f"  {g['group'][:90]}  (끝난 받음각 {g['n_done']}/{g['n_aoa']}개)")
    if edge:
        print(f"\n[주의] 최대 각도가 받음각 범위 끝인 그룹 {len(edge)}개: 실제 최대는 범위 밖일 수 있습니다. "
              "[cases.sweep] aoa 범위를 넓히는 것을 검토하세요.")
    verb = "제외할" if r["dry_run"] else "제외한"
    print(f"\n{verb} 케이스 {r['removed']}개: 남은 케이스 {r['kept_before']}개 → {r['kept_after']}개"
          + ("  (--dry-run: selection.json은 그대로)" if r["dry_run"] else
             "  (selection.json 갱신. 이어서 aero-auto run --priority)"))
    return 0


def cmd_mesh(args) -> int:
    cfg, cases = _load(args)
    if cfg.mesh is None:
        raise ConfigError("project.toml에 [mesh] 섹션이 없습니다.")
    errors = ensure_meshes(cfg, _apply_selection(cfg, cases, args.all), force=args.force, workers=args.jobs)
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


def cmd_check(args) -> int:
    from .preflight import ERROR, TODO, format_items, run_checks

    cfg, cases = _load(args)
    items = run_checks(cfg, cases, parallel=args.jobs)
    print(format_items(items))
    return 1 if any(it.level in (ERROR, TODO) for it in items) else 0


def cmd_status(args) -> int:
    cfg, cases = _load(args)
    statuses = {c.name: read_status(c) for c in cases}

    def state(c):
        s = statuses[c.name]
        return s["state"] + (f" ({s['message']})" if s.get("message") else "")

    _print_table(cases, ("state", state))
    counts = Counter(s["state"] for s in statuses.values())
    print("\n" + ", ".join(f"{k} {v}" for k, v in counts.items()))
    eta = _eta(list(statuses.values()), args.jobs or cfg.max_parallel)
    if eta:
        print(eta)
    return 0


def _eta(statuses: list[dict], parallel: int) -> str | None:
    """끝난 케이스의 실행 시간(중앙값)으로 남은 케이스(대기·실행 중·실패)의 예상 소요 시간."""
    import statistics

    times = [s["elapsed_sec"] for s in statuses if s.get("state") == "done" and s.get("elapsed_sec")]
    left = sum(1 for s in statuses if s.get("state") in ("pending", "running", "failed", "interrupted", "unknown"))
    if not times or not left:
        return None
    per_case = statistics.median(times)
    hours = per_case * left / max(1, parallel) / 3600
    return (f"남은 케이스 {left}개 × 케이스당 {per_case / 60:.1f}분(완료 {len(times)}개 중앙값) "
            f"÷ 동시 {max(1, parallel)}개 ≈ {hours:.1f}시간 ({hours / 24:.1f}일)")


def cmd_collect(args) -> int:
    cfg, cases = _load(args)
    _print_collect(collect(cfg, cases, plots=not args.no_plots), cfg)
    return 0


def cmd_gci(args) -> int:
    import math

    from .mesh_study import mesh_study

    cfg, cases = _load(args)
    r = mesh_study(cfg, cases, quantities=args.quantity, plots=not args.no_plots)
    print("메쉬 독립성 (Celik et al. 2008 GCI): 1 = 조밀, 2 = 보통, 3 = 성김 (셀 수 기준)")
    print("GCI = 그 메쉬 결과의 이산화 불확실성 (95% 신뢰 수준 추정), 외삽 = 셀 크기 0으로 외삽한 값\n")
    print(f"{'group':<58}{'q':<9}{'조밀':>10}{'보통':>10}{'성김':>10}{'p':>6}{'외삽':>10}"
          f"{'GCI 조밀':>9}{'GCI 보통':>9}  판정")
    last = None
    for e in r["rows"]:
        group = e["group"] if e["group"] != last else ""
        last = e["group"]

        def pct(v):
            return f"{v * 100:.2f}%" if isinstance(v, float) and math.isfinite(v) else "-"

        def num(v, fmt=".4f"):
            return format(v, fmt) if isinstance(v, float) and math.isfinite(v) else "-"

        print(f"{group[:57]:<58}{e['quantity']:<9}{e['phi_fine']:>10.4f}{e['phi_medium']:>10.4f}"
              f"{e['phi_coarse']:>10.4f}{num(e.get('p'), '.2f'):>6}{num(e.get('phi_ext')):>10}"
              f"{pct(e.get('gci_fine')):>9}{pct(e.get('gci_medium')):>9}  {e['convergence']}")
    for g in r["incomplete"]:
        print(f"[참고] {g['group']}: 끝난 메쉬 단계 {g['levels']}개 (3단계가 모두 끝나야 계산)")
    bad = [e for e in r["rows"] if e["quantity"] in ("cl", "cd") and not (
        isinstance(e.get("gci_medium"), float) and math.isfinite(e["gci_medium"])
        and e["gci_medium"] <= (0.02 if e["quantity"] == "cl" else 0.05))]
    if r["rows"]:
        if bad:
            print(f"\n[판단] 보통(본 배치) 메쉬의 GCI가 기준(Cl 2%, Cd 5%)을 넘거나 수렴이 단조롭지 않은 항목 {len(bad)}개."
                  " 메쉬를 조밀하게(project.toml [mesh] refine 1.3~1.5 또는 surface_cells·near_size) 하거나,"
                  " 반복 수렴(conv_state)부터 확인하세요.")
        else:
            print("\n[판단] 보통(본 배치) 메쉬의 GCI가 Cl 2%, Cd 5% 이내입니다. 현재 [mesh] 설정으로 본 배치를 진행해도 됩니다.")
    print(f"\n결과: {r['csv']}" + (f"  (그래프 {len(r['plots'])}개)" if r["plots"] else ""))
    return 0


def _safe_console() -> None:
    """출력을 파일로 넘길 때(예: aero-auto run > log.txt) 한국어 Windows 기본 인코딩(cp949)에 없는
    문자(−, ≈ 등)가 나와도 멈추지 않도록, 표현할 수 없는 문자는 ?로 바꿔 쓴다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    parser = ColorArgumentParser(prog="aero-auto", description="ANSYS 해석 케이스 배치 자동화")
    parser.add_argument("-c", "--config", default="project.toml", help="프로젝트 설정 파일 (기본: project.toml)")
    parser.add_argument("--no-banner", action="store_true",
                        help="시작 배너 숨기기 (환경 변수 AERO_AUTO_NO_BANNER=1 과 같음)")
    parser.add_argument("--no-clear", action="store_true",
                        help="시작할 때 터미널 화면을 지우지 않기 (환경 변수 AERO_AUTO_NO_CLEAR=1 과 같음)")
    sub = parser.add_subparsers(dest="command")

    def add(name, func, help_text):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--only", nargs="+", metavar="PATTERN",
                       help="케이스/그룹 이름 필터 (와일드카드 가능, 예: 'M0.3*')")
        p.add_argument("--part", metavar="K/N",
                       help="형상 단위로 N등분한 K번째 몫만 (팀원 분담용, 예: 1/4)")
        p.set_defaults(func=func)
        return p

    p = add("plan", cmd_plan, "케이스 목록 확인 및 폴더/저널 생성")
    p.add_argument("--dry-run", action="store_true", help="목록만 출력하고 파일은 만들지 않음")
    add("screen", cmd_screen, "저충실도 해석 + 민감도 그래프 + 케이스 선별")
    p = add("mesh", cmd_mesh, "형상별 메쉬 생성 (이미 있으면 건너뜀)")
    p.add_argument("--all", action="store_true", help="스크리닝 결과 무시하고 전체 형상 메쉬 생성")
    p.add_argument("--force", action="store_true", help="기존 메쉬도 다시 생성")
    p.add_argument("-j", "--jobs", type=int, help="동시에 만들 메쉬 수 (기본: CPU 코어 수, 최대 8. 메쉬당 메모리 약 300 MB)")
    p = add("check", cmd_check, "Fluent 실행 전 점검 (베이스 케이스, 메쉬, 저널, 경로, 디스크·시간 추정)")
    p.add_argument("-j", "--jobs", type=int, help="시간 추정에 쓸 동시 실행 수 (기본: [project] max_parallel)")
    p = add("run", cmd_run, "배치 실행 (필요한 메쉬 자동 생성)")
    p.add_argument("--force", action="store_true", help="완료된 케이스도 다시 실행")
    p.add_argument("-j", "--jobs", type=int, help="동시 실행 케이스 수 (라이선스 수 고려)")
    p.add_argument("--all", action="store_true", help="스크리닝 결과 무시하고 전체 케이스 실행")
    p.add_argument("--priority", action="store_true",
                   help="스크리닝 우선순위 순서로 실행 (스팟 체크 → 점수 높은 계열·형상 → 받음각)")
    p.add_argument("--limit", type=int, metavar="N", help="남은 케이스 중 앞의 N개만 실행 (나눠서 돌릴 때)")
    p.add_argument("--stage1", action="store_true",
                   help="1단계만: 스팟 체크 + 계열·조건별 대표 형상의 전체 받음각 (이후 narrow-aoa)")
    p.add_argument("--per", nargs="+", metavar="VAR",
                   help="--stage1 대표 형상을 이 변수 값마다 따로 (예: ground_clearance)")
    p = add("status", cmd_status, "진행 상태 확인 (완료된 케이스 실행 시간으로 남은 시간 추정)")
    p.add_argument("-j", "--jobs", type=int, help="남은 시간 추정에 쓸 동시 실행 수 (기본: [project] max_parallel)")
    p = add("collect", cmd_collect, "결과 요약 CSV, 형상 순위, 그래프 생성")
    p.add_argument("--no-plots", action="store_true", help="그래프 없이 CSV만 (케이스가 수천 개일 때 빠름)")
    p = add("narrow-aoa", cmd_narrow_aoa, "1단계 결과로 나머지 형상의 받음각을 대표 형상 최대 각도 주변만 남김")
    p.add_argument("--keep", type=int, default=3, help="남길 받음각 수 (기본 3: 최대 각도와 앞뒤 한 칸)")
    p.add_argument("--per", nargs="+", metavar="VAR",
                   help="그룹을 이 변수 값마다 따로 (기본: run --stage1 에서 쓴 값)")
    p.add_argument("--dry-run", action="store_true", help="결과만 보여주고 selection.json은 바꾸지 않음")
    p = add("gci", cmd_gci, "메쉬 독립성 확인: mesh_level 3단계 결과로 Richardson 외삽·GCI 계산")
    p.add_argument("--quantity", nargs="+", metavar="Q", help="계산할 값 (기본: cl, cd와 요소별 cl-*, cd-*)")
    p.add_argument("--no-plots", action="store_true", help="그래프 없이 CSV만")

    _safe_console()
    # 도움말(-h)도 지운 화면에 나오도록 인자 해석 전에 지운다 (터미널일 때만)
    if "--no-clear" not in (sys.argv[1:] if argv is None else argv):
        clear_screen()
    args = parser.parse_args(argv)
    if args.command is None:  # 명령 없이 실행하면 배너 + 도움말
        if not args.no_banner:
            print_banner()
        parser.print_help()
        return 0
    if not args.no_banner:
        print_banner()
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
