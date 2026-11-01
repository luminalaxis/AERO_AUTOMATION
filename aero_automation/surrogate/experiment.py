"""실험 실행(학습 → 예측 → 평가 → 파일 저장)과 실험 간 비교표.

실험 하나 = 모델 종류 + 입력(CST 16/8) + 하이퍼파라미터 + (실속 가중치, 분포 밖 형상 추가 여부).
결과: <output_dir>/experiments/<tag>/
    predictions.csv          val/test/ood 케이스별 정답, 예측, 예측 구간
    metrics.json             세트별 MAE(형상 부트스트랩 95% CI), 예측 구간 포함률, 모델 설정
    conditions.csv           세트 × 조건(regime_truth, aoa_band, reynolds, source, pi_width)별 성능과 표본 수
    boundary_candidates.csv  경계 사례 분석 후보
    parity.png, polars_test.png, polars_ood.png
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from ..config import ConfigError, ProjectConfig
from .dataset import TARGETS, TURB_FILE, SurrogateConfig, input_columns, load_table, read_csv, write_csv
from .evaluate import (boundary_candidates, condition_table, conformal_offsets, metrics, plot_parity,
                       plot_polars, turbulence_index)
from .models import make_model

EXP_DIR = "experiments"
STALL_REGIMES = ("near_stall", "post_stall")

# 기획서 3장 실험 구성. aero-auto train --suite plan / week14
SUITES = {
    "plan": [
        {"model": "baseline"},                                   # 기준 방법: 얇은 익형 이론
        {"model": "lgbm", "n_cst": 16},                          # 비교 모델 1
        {"model": "mlp", "n_cst": 16, "hidden": [128, 128]},     # 비교 모델 2 (실험 2의 중간 크기)
        {"model": "lgbm", "n_cst": 8},                           # 실험 1: 형상 계수 8개
        {"model": "mlp", "n_cst": 8, "hidden": [128, 128]},
        {"model": "mlp", "n_cst": 16, "hidden": [32, 32]},       # 실험 2: 은닉층 크기 3단계
        {"model": "mlp", "n_cst": 16, "hidden": [512, 512]},
    ],
    "week14": [
        {"model": "lgbm", "n_cst": 16, "stall_weight": 3.0},     # 실속 부근 학습 가중치
        {"model": "mlp", "n_cst": 16, "hidden": [128, 128], "stall_weight": 3.0},
        {"model": "lgbm", "n_cst": 16, "add_ood": True},         # 분포 밖 계열 일부를 학습에 추가
        {"model": "mlp", "n_cst": 16, "hidden": [128, 128], "add_ood": True},
    ],
}


def experiment_tag(spec: dict) -> str:
    if spec.get("tag"):
        return spec["tag"]
    if spec["model"] == "baseline":
        return "baseline"
    tag = f"{spec['model']}_cst{spec.get('n_cst', 16)}"
    if spec["model"] == "mlp":
        tag += "_h" + "x".join(str(h) for h in spec.get("hidden", [128, 128]))
    if spec.get("stall_weight", 1.0) != 1.0:
        tag += f"_sw{spec['stall_weight']:g}"
    if spec.get("add_ood"):
        tag += "_addood"
    if spec.get("calibrate", "none") != "none":
        tag += "_cal"
    return tag


def _prediction_dict(table, out: dict, set_labels: np.ndarray) -> dict:
    return {"y": table.matrix(list(TARGETS)), "mean": out["mean"], "lo": out.get("lo"), "hi": out.get("hi"),
            "airfoil": table["airfoil"], "case": table["case"], "aoa": table["aoa"], "reynolds": table["reynolds"],
            "source": table["source"], "regime_truth": table["regime_truth"], "set": set_labels}


def run_experiment(cfg: ProjectConfig, sc: SurrogateConfig, spec: dict, log=print) -> dict:
    out_root = sc.out_dir(cfg)
    table, split = load_table(out_root)
    spec = {"n_cst": 16, "hidden": [128, 128], "n_models": 5, "stall_weight": 1.0, "add_ood": False,
            "calibrate": sc.calibrate, **spec}
    tag = experiment_tag(spec)
    exp_dir = out_root / EXP_DIR / tag
    features = input_columns(int(spec["n_cst"]))

    addable = set(split.get("ood_addable", []))
    is_addable = np.isin(table["airfoil"], list(addable))
    train_mask = table["split"] == "train"
    if spec["add_ood"]:
        train_mask |= is_addable
    val_mask = table["split"] == "val"
    if not train_mask.any() or not val_mask.any():
        raise ConfigError("학습 또는 검증 세트가 비어 있습니다 (형상 수가 너무 적음). data_report.md를 확인하세요.")
    weight = np.ones(int(train_mask.sum()))
    if spec["stall_weight"] != 1.0:
        weight[np.isin(table["regime_truth"][train_mask], STALL_REGIMES)] = float(spec["stall_weight"])

    log(f"[{tag}] 학습 {int(train_mask.sum())}건 / 검증 {int(val_mask.sum())}건, 입력 {len(features)}개")
    start = time.monotonic()
    model = make_model(spec["model"], features, sc.interval, seed=sc.seed, hidden=spec["hidden"],
                       n_models=int(spec["n_models"]), epochs=spec.get("epochs"))
    model.fit(table.subset(train_mask), table.subset(val_mask), weight)
    train_sec = time.monotonic() - start

    eval_mask = ~train_mask
    sub = table.subset(eval_mask)
    out = model.predict(sub)
    offsets = None
    if spec["calibrate"] == "conformal" and out.get("lo") is not None:
        # 검증 세트(조기 종료에도 쓰였음)로 구간 폭 보정 → 평가 세트(test, ood)에 적용
        v = sub["split"] == "val"
        offsets = conformal_offsets(sub.matrix(list(TARGETS))[v], out["lo"][v], out["hi"][v], sc.interval)
        out["lo"], out["hi"] = out["lo"] - offsets, out["hi"] + offsets
        bad = out["lo"] > out["hi"]
        out["lo"][bad] = out["hi"][bad] = out["mean"][bad]
    pred = _prediction_dict(sub, out, sub["split"])
    ood = sub["split"] == "ood"
    holdout = ood & ~np.isin(sub["airfoil"], list(addable))
    sets = {"val": sub["split"] == "val", "test": sub["split"] == "test", "ood_holdout": holdout}
    if not spec["add_ood"]:
        sets["ood"] = ood  # 분포 밖 전체 (추가 학습 실험에서는 일부가 학습에 들어가 생략)
    sets = {k: v for k, v in sets.items() if v.any()}

    result = {"tag": tag, "spec": spec, "features": features, "train_rows": int(train_mask.sum()),
              "train_seconds": round(train_sec, 1), "interval": sc.interval, "model": model.describe(),
              "conformal_offsets": None if offsets is None else dict(zip(TARGETS, map(float, offsets))),
              "sets": {name: metrics(pred, mask, sc.bootstrap, sc.seed) for name, mask in sets.items()}}

    exp_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(sub.n):
        r = {"case": pred["case"][i], "set": pred["set"][i], "airfoil": pred["airfoil"][i],
             "source": pred["source"][i], "reynolds": pred["reynolds"][i], "aoa": pred["aoa"][i],
             "regime_truth": pred["regime_truth"][i], "ood_holdout": int(holdout[i])}
        for j, t in enumerate(TARGETS):
            r[f"{t}_true"], r[f"{t}_pred"] = pred["y"][i, j], pred["mean"][i, j]
            if pred["lo"] is not None:
                r[f"{t}_lo"], r[f"{t}_hi"] = pred["lo"][i, j], pred["hi"][i, j]
        rows.append(r)
    write_csv(exp_dir / "predictions.csv", rows)
    eval_sets = {k: v for k, v in sets.items() if k != "val"}
    write_csv(exp_dir / "conditions.csv", condition_table(pred, eval_sets, sc.bootstrap, sc.seed))
    turb_path = out_root / TURB_FILE
    turb = turbulence_index(read_csv(turb_path, {"case", "airfoil"})) if turb_path.is_file() else None
    test_like = sets.get("test", np.zeros(sub.n, bool)) | sets.get("ood_holdout", np.zeros(sub.n, bool))
    write_csv(exp_dir / "boundary_candidates.csv", boundary_candidates(pred, test_like, turb))
    plot_parity(pred, eval_sets, exp_dir / "parity.png", title=tag)
    if "test" in sets:
        plot_polars(pred, sets["test"], exp_dir / "polars_test.png", title=f"{tag} - test")
    if "ood_holdout" in sets:
        plot_polars(pred, sets["ood_holdout"], exp_dir / "polars_ood.png", title=f"{tag} - ood_holdout")
    (exp_dir / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=_json),
                                          encoding="utf-8")
    result["dir"] = exp_dir
    return result


def _json(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(type(o))


def _fmt_ci(m: dict, key: str) -> str:
    if key not in m:
        return "-"
    lo, hi = m.get(f"{key}_ci", [float("nan")] * 2)
    return f"{m[key]:.3f} [{lo:.3f}, {hi:.3f}]"


def compare(cfg: ProjectConfig, sc: SurrogateConfig) -> dict:
    """experiments/*/metrics.json → comparison.csv, comparison.md."""
    root = sc.out_dir(cfg) / EXP_DIR
    results = []
    for path in sorted(root.glob("*/metrics.json")):
        results.append(json.loads(path.read_text(encoding="utf-8")))
    if not results:
        raise ConfigError(f"실험 결과가 없습니다: {root} (먼저 aero-auto train)")
    rows = []
    for r in results:
        s = r["spec"]
        base = {"tag": r["tag"], "model": s["model"], "n_cst": s.get("n_cst"),
                "hidden": "x".join(map(str, s.get("hidden", []))) if s["model"] == "mlp" else "",
                "stall_weight": s.get("stall_weight", 1.0), "add_ood": int(bool(s.get("add_ood"))),
                "calibrate": s.get("calibrate", "none"),
                "train_rows": r["train_rows"], "train_seconds": r["train_seconds"]}
        for set_name in ("test", "ood_holdout", "ood"):
            m = r["sets"].get(set_name)
            if not m:
                continue
            for key in ("cl_mae", "log10cd_mae", "cl_coverage", "log10cd_coverage"):
                if key in m:
                    base[f"{set_name}_{key}"] = m[key]
                    base[f"{set_name}_{key}_lo"], base[f"{set_name}_{key}_hi"] = m[f"{key}_ci"]
            base[f"{set_name}_n"] = m["n"]
        rows.append(base)
    out_dir = sc.out_dir(cfg)
    write_csv(out_dir / "comparison.csv", rows)

    lines = ["# 실험 비교", "",
             f"MAE [형상 단위 부트스트랩 95% CI], 포함률은 {int(sc.interval * 100)}% 예측 구간에 정답이 들어간 비율.",
             "ood_holdout은 모든 실험에서 학습에 쓰지 않은 같은 분포 밖 형상 (실험 간 비교는 이 열로).", ""]
    for set_name in ("test", "ood_holdout"):
        lines += [f"## {set_name}", "",
                  "| 실험 | Cl MAE | log10Cd MAE | Cl 포함률 | log10Cd 포함률 | n |", "|---|---|---|---|---|---|"]
        for r in results:
            m = r["sets"].get(set_name)
            if m:
                lines.append(f"| {r['tag']} | {_fmt_ci(m, 'cl_mae')} | {_fmt_ci(m, 'log10cd_mae')} | "
                             f"{_fmt_ci(m, 'cl_coverage')} | {_fmt_ci(m, 'log10cd_coverage')} | {m['n']} |")
        lines.append("")
    md = "\n".join(lines)
    (out_dir / "comparison.md").write_text(md, encoding="utf-8")
    return {"csv": out_dir / "comparison.csv", "md": out_dir / "comparison.md", "text": md}
