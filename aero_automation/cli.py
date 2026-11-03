"""명령줄 인터페이스.

    python -m aero_automation plan     # 케이스 목록 확인 + 폴더/저널 생성
    python -m aero_automation screen   # 저충실도 해석으로 민감도 확인 + 가망 없는 케이스 제외
    python -m aero_automation mesh     # 형상별 메쉬 생성 + 형상/메쉬 이미지
    python -m aero_automation run      # 배치 실행 (완료된 케이스는 건너뜀)
    python -m aero_automation status   # 케이스별 진행 상태
    python -m aero_automation collect  # 결과 요약 CSV, 순위, 그래프 생성

    python -m aero_automation fetch-airfoils  # UIUC 좌표 파일 내려받기
    python -m aero_automation dataset         # CST 계수 + 형상 단위 분할 → runs/dataset/
    python -m aero_automation train           # 기준 방법 / LightGBM / MLP 학습·조건별 평가 → runs/ml/
    python -m aero_automation compare A B     # 학습 결과끼리 같은 케이스로 비교 (실험 1·2, 개선 실험)
    python -m aero_automation edge-cases      # 경계 사례 10개 선정 + 사례별 그래프
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from .banner import print_banner
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
    _print_collect(collect(cfg, cases, plots=not args.no_plots), cfg)
    return 0


def cmd_fetch_airfoils(args) -> int:
    from pathlib import Path

    from .airfoil_db import UIUC_URL, UIUC_ZIP_URL, fetch_airfoils

    cfg, cases = _load(args)
    r = fetch_airfoils(cfg, cases, url=args.url or UIUC_URL,
                       zip_path=Path(args.zip).resolve() if args.zip else None, force=args.force)
    print(f"받음 {len(r['ok'])}개, 이미 있음 {len(r['skipped'])}개, 실패 {len(r['failed'])}개")
    for spec, reason in r["failed"].items():
        print(f"  [실패] {spec}: {reason}")
    if r["failed"]:
        print(f"사이트 접속이 안 되면 {UIUC_ZIP_URL} 을 받아 --zip 으로 지정하세요.")
    return 1 if r["failed"] else 0


def cmd_dataset(args) -> int:
    from .dataset import build_dataset

    cfg, cases = _load(args)
    r = build_dataset(cfg, cases, resplit=args.resplit)
    print(f"데이터셋: {r['dir']}  (dataset.csv, airfoils.csv, split.json, cst_fits.png)")
    print("분할       형상   케이스")
    for split, c in r["counts"].items():
        print(f"  {split:<9} {c['shapes']:>4}  {c['cases']:>6}")
    print("수렴 상태: " + ", ".join(f"{k} {v}" for k, v in sorted(r["states"].items()))
          + "  (diverged는 학습·평가에서 빼고 건수만 보고)")
    print(f"실속 부근(CL 최대 받음각 ± 창) 케이스: {r['near_stall']}")
    if r["yplus"]:
        y = r["yplus"]
        print(f"y+ (yplus_max, 학습에 쓰는 케이스 {y['n']}개): 중앙값 {y['median']:.2f}, 최대 {y['max']:.2f}")
    if r["tsst"] is not None:
        t = r["tsst"]
        print(f"SST vs Transition SST: 같은 케이스 {t['n']}개 비교 (차이 = T-SST − SST)")
        for re, st in t["by_re"].items():
            print(f"  Re {re:,.0f}: {st['n']}개, |ΔCL| 중앙값 {st['median_abs_d_cl']:.3f} · 최대 {st['max_abs_d_cl']:.3f}"
                  f" ({st['max_d_cl_case']}), |Δlog10 CD| 중앙값 {st['median_abs_d_log10_cd']:.3f}"
                  f" · 최대 {st['max_abs_d_log10_cd']:.3f} ({st['max_d_log10_cd_case']})")
        if not t["n"]:
            print("  [경고] 이름이 같은 케이스가 없습니다. Transition SST 프로젝트도 같은 케이스 이름 패턴을 쓰세요.")
    for w in r["warnings"]:
        print(f"[경고] {w}")
    return 0


def _ci(m: dict, key: str, fmt: str = ".4f") -> str:
    """값 [95% 신뢰구간]."""
    if m.get(key) is None:
        return "-"
    if m.get(f"{key}_lo95") is None:
        return format(m[key], fmt)
    return f"{m[key]:{fmt}} [{m[f'{key}_lo95']:{fmt}}, {m[f'{key}_hi95']:{fmt}}]"


def cmd_train(args) -> int:
    from .learn import PI_TAG, TrainOptions, train

    cfg = load_config(args.config)
    opts = TrainOptions(models=args.model, features=args.features,
                        hidden=[int(h) for h in args.hidden.split(",") if h],
                        n_seeds=args.seeds, epochs=args.epochs, stall_weight=args.stall_weight,
                        ood_train_fraction=args.ood_train_fraction, name=args.name)
    r = train(cfg, opts)
    print(f"학습 케이스 {r['n_train']}개, 검증 {r['n_val']}개, 입력 {len(r['features'])}개 ({opts.features} + aoa + log10_re)")
    if r["moved"]:
        print(f"test_ood 형상 {len(r['moved'])}개를 학습에 추가: {', '.join(r['moved'])}")
    print(f"[ ] = 형상 단위 부트스트랩 95% 신뢰구간, PI{PI_TAG} = {PI_TAG}% 예측 구간 (CL)")
    print(f"\n{'model':<9}{'split':<10}{'condition':<12}{'n':>6}  {'MAE CL [95% CI]':<27}{'MAE log10CD [95% CI]':<27}"
          f"{'PI' + PI_TAG + ' width':>11}  {'PI' + PI_TAG + ' cover [95% CI]':<20}")
    for m in r["metrics"]:
        if m["condition"].startswith("state="):
            continue
        width = f"{m[f'width{PI_TAG}_cl']:.3f}" if f"width{PI_TAG}_cl" in m else "-"
        print(f"{m['model']:<9}{m['split']:<10}{m['condition']:<12}{m['n']:>6}  {_ci(m, 'mae_cl', '.3f'):<27}"
              f"{_ci(m, 'mae_log10_cd', '.3f'):<27}{width:>11}  {_ci(m, f'cover{PI_TAG}_cl', '.2f'):<20}")
    if any(r["diverged"].values()):
        print("\n발산(diverged) 케이스는 학습·평가에서 빼고 건수만 보고: "
              + ", ".join(f"{s} {n}" for s, n in r["diverged"].items()))
    print(f"\n결과: {r['dir']}  (metrics.csv: 수렴 상태별 포함, predictions.csv, run.json)")
    return 0


def _delta(e: dict, key: str) -> str:
    """기준 대비 차이 [95% 신뢰구간], 구간이 0을 포함하지 않으면 *."""
    if e.get(key) is None:
        return "(기준)"
    lo, hi = e[f"{key}_lo95"], e[f"{key}_hi95"]
    return f"{e[key]:+.4f} [{lo:+.4f}, {hi:+.4f}]" + ("*" if lo > 0 or hi < 0 else "")


def cmd_compare(args) -> int:
    from .analysis import compare
    from .learn import PI_TAG

    cfg = load_config(args.config)
    r = compare(cfg, args.runs, args.model, args.name)
    print(f"모든 실행에 공통인 평가 케이스 {r['n_cases']}개(test, test_ood)로 비교합니다.")
    print("ΔMAE = MAE − 기준 MAE (음수 = 개선), [ ] = 짝지은 형상 단위 부트스트랩 95% 신뢰구간, "
          "* = 구간이 0을 포함하지 않음")
    for run, n in r["not_common"].items():
        if n:
            print(f"  {run}: 다른 실행에 없는 케이스 {n}개 제외 (학습에 넣은 처음 보는 형상 등)")
    if r["mismatch"]:
        print(f"[경고] 실행마다 정답·분할이 다른 케이스 {len(r['mismatch'])}개 제외 "
              f"(사이에 dataset을 다시 만든 것 같습니다): {', '.join(r['mismatch'][:5])}")
    print(f"\n{'split':<10}{'condition':<12}{'n':>6}  {'run/model':<26}{'MAE CL':>8}  {'ΔMAE CL [95% CI]':<30}"
          f"{'MAE logCD':>10}  {'ΔMAE logCD [95% CI]':<30}{'PI' + PI_TAG + ' cover CL':>15}")
    for e in r["rows"]:
        cover = e.get(f"cover{PI_TAG}_cl")
        print(f"{e['split']:<10}{e['condition']:<12}{e['n']:>6}  {e['run'] + '/' + e['model']:<26}"
              f"{e['mae_cl']:>8.4f}  {_delta(e, 'd_mae_cl'):<30}{e['mae_log10_cd']:>10.4f}  "
              f"{_delta(e, 'd_mae_log10_cd'):<30}{'-' if cover is None else f'{cover:.0%}':>15}")
    print(f"\n결과: {r['path']}")
    return 0


def cmd_edge_cases(args) -> int:
    from .analysis import edge_cases
    from .learn import PI_TAG

    cfg = load_config(args.config)
    r = edge_cases(cfg, args.run, args.model, plots=not args.no_plots)
    s = r["scale"]
    print(f"경계 사례 {len(r['rows'])}개 ({args.run}, {args.model}). 오차 점수 = |CL 오차| / {s['cl']:.4f} "
          f"+ |log10 CD 오차| / {s['log10_cd']:.4f}  (평가 세트 평균 오차의 몇 배인지, 1 = 평균)")
    print(f"PI{PI_TAG}: CFD 정답이 {PI_TAG}% 예측 구간 안이면 O (CL/log10 CD)\n")
    print(f"{'no':>3}  {'category':<14}{'pick':<13}{'case':<34}{'split':<10}{'CL err':>8}{'logCD err':>10}"
          f"{'score':>7}{'PI' + PI_TAG:>7}")
    for e in r["rows"]:
        if f"in_pi{PI_TAG}_cl" in e:
            pi = "/".join("O" if e.get(f"in_pi{PI_TAG}_{t}") else "X" for t in ("cl", "log10_cd"))
        else:
            pi = "-"
        extra = ""
        if e["category"] == "turbulence":
            extra = f"   T-SST − SST: ΔCL {e['d_cl_tsst']:+.3f}, Δlog10 CD {e['d_log10_cd_tsst']:+.3f}"
        print(f"{e['no']:>3}  {e['category']:<14}{e['pick']:<13}{e['case']:<34}{e['eval_split']:<10}"
              f"{e['err_cl']:>+8.3f}{e['err_log10_cd']:>+10.3f}{e['error_score']:>7.1f}{pi:>7}{extra}")
    if not r["has_tsst"]:
        print("\n[참고] Transition SST 결과가 없어 난류 모델 민감 사례 2개는 고르지 못했습니다. "
              "Transition SST로 일부 케이스를 다시 푼 뒤 [ml] tsst_summary를 지정하고 dataset → train → edge-cases 순서로 다시 실행하세요.")
    other = [c for c in r["missing"] if c != "turbulence"]
    if other:
        print(f"[참고] 해당하는 평가 케이스가 없는 범주: {', '.join(other)}")
    print(f"\n결과: {r['dir']}  (edge_cases.csv, 사례별 CL-α · log10 CD-α 그래프)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="aero-auto", description="ANSYS 해석 케이스 배치 자동화")
    parser.add_argument("-c", "--config", default="project.toml", help="프로젝트 설정 파일 (기본: project.toml)")
    parser.add_argument("--no-banner", action="store_true",
                        help="시작 배너 숨기기 (환경 변수 AERO_AUTO_NO_BANNER=1 과 같음)")
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
    p = add("run", cmd_run, "배치 실행 (필요한 메쉬 자동 생성)")
    p.add_argument("--force", action="store_true", help="완료된 케이스도 다시 실행")
    p.add_argument("-j", "--jobs", type=int, help="동시 실행 케이스 수 (라이선스 수 고려)")
    p.add_argument("--all", action="store_true", help="스크리닝 결과 무시하고 전체 케이스 실행")
    add("status", cmd_status, "진행 상태 확인")
    p = add("collect", cmd_collect, "결과 요약 CSV, 형상 순위, 그래프 생성")
    p.add_argument("--no-plots", action="store_true", help="그래프 없이 CSV만 (케이스가 수천 개일 때 빠름)")
    p = add("fetch-airfoils", cmd_fetch_airfoils, "UIUC 좌표 파일 내려받기 (없는 파일만)")
    p.add_argument("--zip", help="UIUC coord_seligFmt.zip 경로 (사이트 대신 압축 파일에서 꺼냄)")
    p.add_argument("--url", help="내려받을 주소 템플릿 (기본: UIUC, {name} = 파일 이름)")
    p.add_argument("--force", action="store_true", help="이미 있는 파일도 다시 받기")
    p = add("dataset", cmd_dataset, "학습용 데이터셋 (CST 계수, 수렴 상태, 형상 단위 분할)")
    p.add_argument("--resplit", action="store_true", help="split.json을 무시하고 형상 분할을 새로 함")
    p = sub.add_parser("train", help="회귀 모델 학습·평가 (dataset 결과 사용)")
    p.add_argument("--model", nargs="+", default=["baseline", "lgbm", "mlp"],
                   choices=["baseline", "lgbm", "mlp"], help="학습할 모델 (기본: 셋 다)")
    p.add_argument("--features", default="cst16", help="형상 특성: cst16(면당 8개) 또는 cst8(면당 4개)")
    p.add_argument("--hidden", default="64,64", help="MLP 은닉층 크기 (예: 32,32 / 128,128,128)")
    p.add_argument("--seeds", type=int, default=5, help="MLP 앙상블 시드 수")
    p.add_argument("--epochs", type=int, default=2000, help="MLP 최대 에폭 (검증 손실로 조기 종료)")
    p.add_argument("--stall-weight", type=float, default=1.0, help="실속 부근 학습 케이스 가중치 (개선 실험)")
    p.add_argument("--ood-train-fraction", type=float, default=0.0,
                   help="처음 보는 형상(test_ood) 중 학습에 추가할 비율 (개선 실험)")
    p.add_argument("--name", help="결과 폴더 이름 (기본: 특성_은닉층)")
    p.set_defaults(func=cmd_train)
    p = sub.add_parser("compare", help="학습 결과끼리 같은 케이스·같은 지표로 비교 (실험 1·2, 개선 실험)")
    p.add_argument("runs", nargs="+", metavar="RUN", help="runs/ml/ 아래 결과 폴더 이름. 첫 번째가 기준")
    p.add_argument("--model", nargs="+", choices=["baseline", "lgbm", "mlp"],
                   help="비교할 모델 (기본: 결과에 있는 모델 전부)")
    p.add_argument("--name", help="결과 파일 이름 (기본: 실행 이름을 _vs_로 이음)")
    p.set_defaults(func=cmd_compare)
    p = sub.add_parser("edge-cases", help="경계 사례 10개 선정 + 사례별 그래프 (train 결과 사용)")
    p.add_argument("--run", default="cst16_h64x64", help="runs/ml/ 아래 결과 폴더 이름 (기본: cst16_h64x64)")
    p.add_argument("--model", default="mlp", choices=["baseline", "lgbm", "mlp"], help="분석할 모델 (기본: mlp)")
    p.add_argument("--no-plots", action="store_true", help="그래프 없이 CSV만")
    p.set_defaults(func=cmd_edge_cases)

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
