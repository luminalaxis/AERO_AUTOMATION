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


def _safe_console() -> None:
    """출력을 파일로 넘길 때(예: aero-auto run > log.txt) 한국어 Windows 기본 인코딩(cp949)에 없는
    문자(−, ≈ 등)가 나와도 멈추지 않도록, 표현할 수 없는 문자는 ?로 바꿔 쓴다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


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
    _safe_console()
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
