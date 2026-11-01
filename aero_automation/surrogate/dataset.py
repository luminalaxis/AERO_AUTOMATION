"""summary.csv(CFD 결과) → 대리 모델 학습 데이터.

입력  : CST 계수(16 또는 8), 받음각 aoa [deg], log10Re
출력  : cl, log10cd
라벨 규칙:
  - state = done 이고 수렴 상태(convergence)가 use_convergence(기본 converged, oscillating)인 케이스만 사용.
    진동 케이스의 값은 collect 단계에서 이미 마지막 average_last회 평균이다.
  - 발산(diverged)·시간 초과·실패 케이스는 제외하고 건수만 data_report.md에 보고한다.
분할 (형상 단위, 같은 익형의 모든 받음각·Re는 같은 세트):
  - 최대 캠버 ≥ ood_camber 인 익형(+ ood_airfoils에 적은 익형) → "ood" (분포 밖 평가 세트)
  - 나머지 → 출처(source: naca/uiuc)별로 섞어 train 70% / val 15% / test 15%
  - ood 중 ood_add_fraction 비율은 "추가 학습 후보"(14주차 실험). 나머지는 모든 실험에서 공통 평가용 ood_holdout
조건 ①(regime_truth): 정답 극곡선(같은 익형·Re)에서 Cl 최대 받음각 α_clmax 기준
  pre_stall (α < α_clmax − margin) / near_stall (|α − α_clmax| ≤ margin) / post_stall (α > α_clmax + margin).
  정답을 알아야 판별할 수 있으므로 평가(조건별 성능)와 학습 가중치에만 쓰고 입력 특징량으로는 쓰지 않는다.
"""

from __future__ import annotations

import csv
import json
import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, fields
from pathlib import Path

import numpy as np

from ..config import ConfigError, ProjectConfig
from .cst import feature_names, shape_features

DATASET_FILE = "dataset.csv"
SHAPES_FILE = "shapes.csv"
SPLIT_FILE = "split.json"
REPORT_FILE = "data_report.md"
TURB_FILE = "turbulence_comparison.csv"
TARGETS = ("cl", "log10cd")
_NACA4 = re.compile(r"^(NACA)?\s*\d{4}$", re.IGNORECASE)


@dataclass
class SurrogateConfig:
    summaries: list = field(default_factory=lambda: ["runs/summary.csv"])  # 팀원별 결과를 여러 개 합칠 수 있음
    compare_summary: str | None = None   # 난류 모델 비교용 결과 (예: Transition SST, runs_tsst/summary.csv)
    output_dir: str = "runs/surrogate"
    airfoil_key: str = "main_airfoil"
    source_key: str = "source"           # 없으면 NACA 4자리 → naca, 그 외 → uiuc
    reynolds_key: str = "reynolds"
    use_convergence: list = field(default_factory=lambda: ["converged", "oscillating"])
    ood_camber: float = 0.05             # 최대 캠버(코드 비) 이 값 이상 → 분포 밖(고캠버) 평가 세트
    ood_airfoils: list = field(default_factory=list)  # 추가로 분포 밖 세트에 넣을 익형
    ood_add_fraction: float = 0.3        # 14주차: 분포 밖 형상 중 학습에 추가해 볼 비율
    split: list = field(default_factory=lambda: [0.70, 0.15, 0.15])
    seed: int = 0
    stall_margin: float = 2.0            # regime_truth의 near_stall 폭 [deg]
    interval: float = 0.90               # 예측 구간 (90%)
    calibrate: str = "none"              # "conformal": 검증 세트로 예측 구간 폭을 보정 (분위 회귀가 구간을 좁게 잡을 때)
    bootstrap: int = 1000                # 형상 단위 부트스트랩 반복 수
    turb_threshold: float = 0.1          # |Cl(SST) − Cl(비교 모델)| 이 이 값 이상이면 난류 모델 민감 케이스

    @classmethod
    def from_project(cls, cfg: ProjectConfig) -> "SurrogateConfig":
        data = cfg.surrogate or {}
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ConfigError(f"[surrogate]에 알 수 없는 항목: {', '.join(sorted(unknown))}")
        sc = cls(**data)
        if sc.calibrate not in ("none", "conformal"):
            raise ConfigError(f"[surrogate] calibrate는 'none' 또는 'conformal'이어야 합니다: {sc.calibrate!r}")
        if len(sc.split) != 3 or abs(sum(sc.split) - 1) > 1e-6:
            raise ConfigError(f"[surrogate] split은 [학습, 검증, 평가] 비율 3개(합 1)여야 합니다: {sc.split}")
        return sc

    def out_dir(self, cfg: ProjectConfig) -> Path:
        return (cfg.root / self.output_dir).resolve()


# ---------------------------------------------------------------- CSV 입출력

def _num(v):
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def read_csv(path: Path, str_keys: set = frozenset()) -> list[dict]:
    """숫자로 읽을 수 있는 값은 float로. str_keys 열은 문자열 유지 (예: 익형 "0012"가 12.0이 되지 않게)."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{k: (v if k in str_keys else _num(v)) for k, v in r.items() if v not in (None, "")}
                for r in csv.DictReader(f)]


def write_csv(path: Path, rows: list[dict]) -> Path:
    columns: list[str] = []
    for r in rows:
        columns += [k for k in r if k not in columns]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return path


def _source(airfoil: str, row: dict, key: str) -> str:
    if row.get(key) not in (None, ""):
        return str(row[key])
    return "naca" if _NACA4.match(airfoil.strip()) else "uiuc"


def _airfoil_name(spec: str) -> str:
    """'airfoils/e387.dat' → 'e387', '2412' → 'NACA2412'."""
    spec = str(spec).strip()
    if _NACA4.match(spec):
        return "NACA" + spec[-4:]
    return Path(spec).stem


def _read_summaries(cfg: ProjectConfig, paths: list[str], str_keys: set = frozenset()) -> list[dict]:
    rows: dict[str, dict] = {}
    str_keys = {"case", "group", "state", "message", "convergence", *str_keys}
    for p in paths:
        path = (cfg.root / p).resolve()
        if not path.is_file():
            raise ConfigError(f"결과 요약 파일이 없습니다: {path} (먼저 aero-auto collect)")
        for r in read_csv(path, str_keys):
            r["_summary"] = p
            name = str(r.get("case"))
            # 여러 사람이 같은 케이스를 돌렸으면 완료된 쪽을 쓴다
            if name not in rows or (rows[name].get("state") != "done" and r.get("state") == "done"):
                rows[name] = r
    return list(rows.values())


# ---------------------------------------------------------------- 데이터 구성

def _yplus_columns(rows: list[dict]) -> list[str]:
    cols = []
    for r in rows:
        cols += [k for k in r if ("yplus" in k.lower() or "y+" in k.lower()) and k not in cols]
    return cols


def _label_regime(records: list[dict], margin: float) -> None:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        groups[(r["airfoil"], r["reynolds"])].append(r)
    for rs in groups.values():
        best = max(rs, key=lambda r: r["cl"])
        a_max = best["aoa"]
        for r in rs:
            r["aoa_clmax_truth"] = a_max
            d = r["aoa"] - a_max
            r["regime_truth"] = "pre_stall" if d < -margin else ("post_stall" if d > margin else "near_stall")


def _split_shapes(shapes: list[dict], sc: SurrogateConfig) -> dict:
    rng = random.Random(sc.seed)
    ood_names = {_airfoil_name(a) for a in sc.ood_airfoils}
    split: dict[str, str] = {}
    ood = []
    by_source: dict[str, list[str]] = defaultdict(list)
    for s in sorted(shapes, key=lambda s: s["airfoil"]):
        if abs(s["max_camber"]) >= sc.ood_camber or s["airfoil"] in ood_names:
            ood.append(s["airfoil"])
        else:
            by_source[s["source"]].append(s["airfoil"])
    for names in by_source.values():
        rng.shuffle(names)
        n = len(names)
        n_train = round(n * sc.split[0])
        n_val = round(n * sc.split[1])
        if n >= 3:  # 작은 출처도 세 세트에 최소 1개씩
            n_train = min(max(n_train, 1), n - 2)
            n_val = min(max(n_val, 1), n - n_train - 1)
        for i, name in enumerate(names):
            split[name] = "train" if i < n_train else ("val" if i < n_train + n_val else "test")
    rng.shuffle(ood)
    n_add = round(len(ood) * sc.ood_add_fraction)
    for name in ood:
        split[name] = "ood"
    return {"shapes": split, "ood_addable": sorted(ood[:n_add]), "seed": sc.seed,
            "ood_camber": sc.ood_camber, "split": sc.split}


def _turbulence_comparison(cfg: ProjectConfig, sc: SurrogateConfig, records: list[dict]) -> list[dict]:
    """주 난류 모델(SST) 결과와 비교 모델(예: Transition SST) 결과를 (익형, Re, 받음각)으로 짝짓는다."""
    other = _read_summaries(cfg, [sc.compare_summary], {sc.airfoil_key, sc.source_key})
    index = {}
    for r in other:
        if r.get("state") != "done" or not isinstance(r.get("cl"), float) or not isinstance(r.get("cd"), float):
            continue
        key = (_airfoil_name(r.get(sc.airfoil_key, "")), float(r.get(sc.reynolds_key, math.nan)),
               float(r.get(cfg.results.x_param, math.nan)))
        index[key] = r
    out = []
    for rec in records:
        o = index.get((rec["airfoil"], rec["reynolds"], rec["aoa"]))
        if o is None or o["cd"] <= 0:
            continue
        out.append({"case": rec["case"], "airfoil": rec["airfoil"], "reynolds": rec["reynolds"], "aoa": rec["aoa"],
                    "regime_truth": rec["regime_truth"], "cl_main": rec["cl"], "cl_compare": o["cl"],
                    "dcl": o["cl"] - rec["cl"], "log10cd_main": rec["log10cd"],
                    "log10cd_compare": math.log10(o["cd"]), "dlog10cd": math.log10(o["cd"]) - rec["log10cd"],
                    "convergence_compare": o.get("convergence", "")})
    return out


def build_dataset(cfg: ProjectConfig, sc: SurrogateConfig, extra_summaries: list[str] = (),
                  log=print) -> dict:
    out_dir = sc.out_dir(cfg)
    rows = _read_summaries(cfg, [*sc.summaries, *extra_summaries], {sc.airfoil_key, sc.source_key})
    aoa_key = cfg.results.x_param
    yplus_cols = _yplus_columns(rows)

    counts = Counter()
    conv_counts = Counter()
    accepted = []
    for r in rows:
        state = str(r.get("state", "pending"))
        conv = str(r.get("convergence", "")) or ("unknown" if state == "done" else state)
        counts[state] += 1
        conv_counts[conv] += 1
        if state != "done" or (conv not in sc.use_convergence and conv != "unknown"):
            continue
        if not all(isinstance(r.get(k), (int, float)) for k in ("cl", "cd", aoa_key, sc.reynolds_key)):
            conv_counts["missing_values"] += 1
            continue
        if r["cd"] <= 0:
            conv_counts["nonpositive_cd"] += 1
            continue
        if sc.airfoil_key not in r:
            raise ConfigError(f"결과에 익형 열 {sc.airfoil_key!r}이 없습니다 ([surrogate] airfoil_key 확인).")
        accepted.append(r)
    if not accepted:
        raise ConfigError("학습에 쓸 수 있는 완료 케이스가 없습니다 (state=done, convergence 확인).")

    shapes: dict[str, dict] = {}
    failed_shapes = {}
    for r in accepted:
        spec = str(r[sc.airfoil_key])
        name = _airfoil_name(spec)
        if name in shapes or name in failed_shapes:
            continue
        try:
            feats = shape_features(spec, cfg.root)
        except (ConfigError, ValueError, np.linalg.LinAlgError) as exc:
            failed_shapes[name] = str(exc)
            continue
        shapes[name] = {"airfoil": name, "spec": spec, "source": _source(spec, r, sc.source_key), **feats}
    if failed_shapes:
        log(f"[경고] 형상 피팅 실패 {len(failed_shapes)}개 (제외): {', '.join(failed_shapes)}")

    records = []
    for r in accepted:
        name = _airfoil_name(str(r[sc.airfoil_key]))
        if name not in shapes:
            continue
        rec = {"case": r["case"], "airfoil": name, "source": shapes[name]["source"],
               "reynolds": float(r[sc.reynolds_key]), "log10re": math.log10(float(r[sc.reynolds_key])),
               "aoa": float(r[aoa_key]), "cl": float(r["cl"]), "cd": float(r["cd"]),
               "log10cd": math.log10(float(r["cd"])), "convergence": r.get("convergence", "unknown")}
        rec.update({k: r[k] for k in yplus_cols if k in r})
        rec.update({k: v for k, v in shapes[name].items() if k not in ("airfoil", "spec", "source")})
        records.append(rec)
    _label_regime(records, sc.stall_margin)

    split = _split_shapes(list(shapes.values()), sc)
    for rec in records:
        rec["split"] = split["shapes"][rec["airfoil"]]
    for s in shapes.values():
        s["split"] = split["shapes"][s["airfoil"]]
        s["ood_addable"] = int(s["airfoil"] in split["ood_addable"])

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / DATASET_FILE, records)
    write_csv(out_dir / SHAPES_FILE, list(shapes.values()))
    (out_dir / SPLIT_FILE).write_text(json.dumps(split, ensure_ascii=False, indent=2), encoding="utf-8")

    turb = []
    if sc.compare_summary and not (cfg.root / sc.compare_summary).is_file():
        log(f"[경고] 난류 모델 비교 결과가 아직 없습니다: {sc.compare_summary} (비교 생략)")
    elif sc.compare_summary:
        turb = _turbulence_comparison(cfg, sc, records)
        if turb:
            write_csv(out_dir / TURB_FILE, turb)

    report = _report(rows, records, shapes, split, counts, conv_counts, failed_shapes, yplus_cols, turb, sc)
    (out_dir / REPORT_FILE).write_text(report, encoding="utf-8")
    return {"dir": out_dir, "records": records, "shapes": shapes, "split": split, "counts": counts,
            "convergence": conv_counts, "report": report, "turbulence": turb}


def _report(rows, records, shapes, split, counts, conv_counts, failed_shapes, yplus_cols, turb, sc) -> str:
    lines = ["# 학습 데이터 보고", "",
             f"- 결과 케이스 {len(rows)}개 → 학습 데이터 {len(records)}건 (형상 {len(shapes)}개)",
             "- 상태: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
             "- 수렴: " + ", ".join(f"{k} {v}" for k, v in sorted(conv_counts.items())),
             f"  (사용: {', '.join(sc.use_convergence)}. 발산·시간 초과·실패는 제외하고 건수만 보고)"]
    if failed_shapes:
        lines.append(f"- 형상 피팅 실패로 제외: {', '.join(failed_shapes)}")
    lines += ["", "## 분할 (형상 단위)", "", "| 세트 | 형상 수 | 건수 | 출처 |", "|---|---|---|---|"]
    for name in ("train", "val", "test", "ood"):
        sh = [s for s in shapes.values() if s["split"] == name]
        n = sum(1 for r in records if r["split"] == name)
        src = Counter(s["source"] for s in sh)
        lines.append(f"| {name} | {len(sh)} | {n} | {', '.join(f'{k} {v}' for k, v in sorted(src.items()))} |")
    lines += ["", f"- 분포 밖(ood): 최대 캠버 ≥ {sc.ood_camber:g}c" +
              (f" 또는 지정 익형 {', '.join(sc.ood_airfoils)}" if sc.ood_airfoils else ""),
              f"- ood 중 14주차 추가 학습 후보 {len(split['ood_addable'])}개, "
              f"공통 평가용 ood_holdout {sum(1 for s in shapes.values() if s['split'] == 'ood') - len(split['ood_addable'])}개"]
    lines += ["", "## 조건별 건수", "", "| 조건 | 값 | 건수 |", "|---|---|---|"]
    for key in ("reynolds", "regime_truth", "source", "convergence"):
        for v, n in sorted(Counter(r[key] for r in records).items(), key=lambda x: str(x[0])):
            lines.append(f"| {key} | {v:g} | {n} |" if isinstance(v, float) else f"| {key} | {v} | {n} |")
    rms = [s["cst16_rms"] for s in shapes.values()]
    worst = sorted(shapes.values(), key=lambda s: -s["cst16_rms"])[:5]
    lines += ["", "## CST 피팅", "",
              f"- 16계수 RMS 오차: 중앙값 {np.median(rms):.2e}, 최대 {max(rms):.2e} (코드 기준)",
              "- 오차가 큰 형상 (좌표 파일 확인): " + ", ".join(f"{s['airfoil']} {s['cst16_rms']:.1e}" for s in worst)]
    if yplus_cols:
        lines += ["", "## y+", ""]
        for col in yplus_cols:
            vals = np.array([r[col] for r in records if isinstance(r.get(col), (int, float))])
            if len(vals):
                lines.append(f"- {col}: 중앙값 {np.median(vals):.2f}, 최대 {vals.max():.2f}, "
                             f"> 1 인 케이스 {int((vals > 1).sum())}건, > 5 인 케이스 {int((vals > 5).sum())}건")
    else:
        lines += ["", "- y+ 열이 없습니다. Fluent 리포트 파일에 y+ 리포트(yplus-avg, yplus-max)를 넣으면 자동 집계됩니다."]
    if turb:
        dcl = np.array([t["dcl"] for t in turb])
        dcd = np.array([t["dlog10cd"] for t in turb])
        lines += ["", "## 난류 모델 비교", "",
                  f"- 짝지은 케이스 {len(turb)}건: |ΔCl| 평균 {np.abs(dcl).mean():.3f}, 최대 {np.abs(dcl).max():.3f}; "
                  f"|Δlog10Cd| 평균 {np.abs(dcd).mean():.3f}",
                  f"- |ΔCl| ≥ {sc.turb_threshold:g} 인 케이스 {int((np.abs(dcl) >= sc.turb_threshold).sum())}건 "
                  f"→ {TURB_FILE}"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 학습 단계에서 읽기

class Table(dict):
    """열 이름 → numpy 배열. 숫자 열은 float, 나머지는 object."""

    @property
    def n(self) -> int:
        """행 수."""
        return len(next(iter(self.values()))) if dict.__len__(self) else 0

    def subset(self, mask: np.ndarray) -> "Table":
        return Table({k: v[mask] for k, v in self.items()})

    def matrix(self, columns: list[str]) -> np.ndarray:
        missing = [c for c in columns if c not in self]
        if missing:
            raise ConfigError(f"데이터에 열이 없습니다: {', '.join(missing)}")
        return np.column_stack([self[c].astype(float) for c in columns])


def load_table(out_dir: Path) -> tuple[Table, dict]:
    path = out_dir / DATASET_FILE
    if not path.is_file():
        raise ConfigError(f"학습 데이터가 없습니다: {path} (먼저 aero-auto dataset)")
    rows = read_csv(path, {"case", "airfoil", "source", "split", "regime_truth", "convergence"})
    split = json.loads((out_dir / SPLIT_FILE).read_text(encoding="utf-8"))
    columns: list[str] = []
    for r in rows:
        columns += [k for k in r if k not in columns]
    table = Table()
    for c in columns:
        vals = [r.get(c) for r in rows]
        if all(isinstance(v, (int, float)) for v in vals):
            table[c] = np.array(vals, float)
        else:
            table[c] = np.array(["" if v is None else str(v) for v in vals], object)
    return table, split


def input_columns(n_cst: int) -> list[str]:
    if n_cst not in (16, 8):
        raise ConfigError(f"CST 계수 수는 16 또는 8이어야 합니다: {n_cst}")
    return feature_names(n_cst) + ["aoa", "log10re"]
