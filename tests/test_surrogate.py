"""에어포일 대리 모델 확장: CST, 수렴 판정, 형상 단위 분담, inlet-outlet 메쉬, 데이터→학습→평가 파이프라인."""

import csv
import json
import math
import random
import textwrap
from pathlib import Path

import numpy as np
import pytest

from aero_automation.cases import build_cases, split_part
from aero_automation.config import ConfigError, load_config
from aero_automation.geometry import load_section
from aero_automation.meshing import generate_mesh, make_job
from aero_automation.results import classify_convergence
from aero_automation.surrogate.cst import fit_cst, shape_features, zero_lift_angle
from aero_automation.surrogate.evaluate import conformal_offsets, shape_bootstrap

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "airfoil_surrogate"


# ---------------------------------------------------------------- CST

def test_cst_fit_and_thin_airfoil_alpha0():
    el = load_section("0012", Path("."), "main")
    fit = fit_cst(el.xy, el.le_index, 8)
    assert fit.rms < 2e-4
    assert fit.upper == pytest.approx(-fit.lower, abs=1e-6)  # 대칭 익형
    assert zero_lift_angle(fit) == pytest.approx(0.0, abs=1e-6)

    f = shape_features("NACA2412", Path("."))
    assert f["alpha0_deg"] == pytest.approx(-2.08, abs=0.1)  # 얇은 익형 이론
    assert f["max_camber"] == pytest.approx(0.02, abs=0.002)
    assert f["max_thickness"] == pytest.approx(0.12, abs=0.002)
    assert len([k for k in f if k.startswith("cst16_") and k[-1].isdigit()]) == 16
    assert len([k for k in f if k.startswith("cst8_") and k[-1].isdigit()]) == 8
    assert f["cst8_rms"] > f["cst16_rms"]  # 계수가 적으면 오차가 커짐


# ---------------------------------------------------------------- 수렴 판정

def test_classify_convergence():
    it = np.arange(500)
    steady = classify_convergence(np.full(500, 0.8), np.full(500, 0.012), 500, 0.01)
    assert steady["convergence"] == "converged"
    wobble = classify_convergence(0.8 + 0.05 * np.sin(it / 7), 0.012 + 0.002 * np.sin(it / 7), 500, 0.01)
    assert wobble["convergence"] == "oscillating"
    assert wobble["cl_std"] == pytest.approx(0.05 / math.sqrt(2), rel=0.05)
    drift = classify_convergence(0.8 + 1e-4 * it, np.full(500, 0.012), 500, 0.01)
    assert drift["convergence"] == "oscillating"  # 아직 변하는 중


def test_collect_labels_convergence(tmp_path):
    from aero_automation.cli import main
    from test_workflow import make_project

    cfg_path = make_project(tmp_path, sweep="aoa = [4, 99]", extra="converge_tol = 0.01")
    main(["-c", str(cfg_path), "run", "--only", "M0.3*"])
    with open(tmp_path / "runs" / "summary.csv", encoding="utf-8-sig") as f:
        rows = {r["case"]: r for r in csv.DictReader(f)}
    assert rows["M0.3_AoA99"]["convergence"] == "diverged"   # 로그의 Divergence detected
    assert "Divergence" in rows["M0.3_AoA99"]["message"]
    # 가짜 솔버의 Cl은 반복마다 0.001씩 증가 → 마지막 2회 평균 구간에서도 변하는 중
    assert rows["M0.3_AoA4"]["convergence"] in ("converged", "oscillating")
    assert float(rows["M0.3_AoA4"]["cl_std"]) == pytest.approx(0.0005, rel=1e-3)


# ---------------------------------------------------------------- 분담, 메쉬

def test_split_part_by_geometry():
    cfg = load_config(EXAMPLE / "project.toml")
    cases = build_cases(cfg)
    parts = [split_part(cases, f"{k}/4") for k in range(1, 5)]
    names = [c.name for p in parts for c in p]
    assert sorted(names) == sorted(c.name for c in cases)  # 빠짐·중복 없음
    meshes = [{c.mesh.name for c in p} for p in parts]
    assert all(not (a & b) for i, a in enumerate(meshes) for b in meshes[i + 1:])  # 형상 단위로 나뉨
    assert max(map(len, meshes)) - min(map(len, meshes)) <= 1
    with pytest.raises(ConfigError):
        split_part(cases, "5/4")


def test_example_case_matrix():
    cfg = load_config(EXAMPLE / "project.toml")
    cases = build_cases(cfg)
    c = next(c for c in cases if c.name == "NACA2412_Re200k_a12")
    assert c.params["reynolds"] == 200000
    assert 1.225 * c.params["velocity"] * c.params["chord"] / 1.7894e-5 == pytest.approx(2e5)
    assert c.params["flow_angle"] == 12
    assert c.mesh.settings.reynolds == 500000  # 형상의 최고 Re로 첫 셀 높이
    assert len({x.params["aoa"] for x in cases}) == 14


def test_mesh_study_names_distinct():
    cfg = load_config(EXAMPLE / "project_mesh.toml")
    meshes = {c.mesh.name for c in build_cases(cfg)}
    assert len(meshes) == 3
    assert any("surface_cells350" in m for m in meshes)


def test_inlet_outlet_farfield_mesh(tmp_path):
    settings = {"farfield_type": "inlet-outlet", "farfield_radius": 20, "surface_cells": 60,
                "near_size": 0.04, "wake_size": 0.05, "wake_length": 1.0, "reynolds": 2e5}
    job = make_job({"main_airfoil": "0012"}, settings, tmp_path)
    generate_mesh(job, tmp_path, plots=False)
    text = job.mesh_file.read_text(encoding="utf-8", errors="replace")
    assert "velocity-inlet inlet" in text and "pressure-outlet outlet" in text
    assert "pressure-far-field" not in text
    with pytest.raises(ConfigError):
        make_job({"main_airfoil": "0012"}, {"farfield_type": "box"}, tmp_path)


# ---------------------------------------------------------------- 평가 함수

def test_shape_bootstrap_and_conformal():
    rng = np.random.default_rng(0)
    vals = np.r_[np.full(10, 1.0), np.full(10, 3.0)]
    shapes = np.array(["a"] * 10 + ["b"] * 10)
    m, lo, hi = shape_bootstrap(vals, shapes, 500, rng)
    assert m == 2.0 and lo == 1.0 and hi == 3.0  # 형상 2개 → 형상 단위로만 흔들림

    y = rng.normal(size=(2000, 2))
    lo0, hi0 = np.full((2000, 2), -0.5), np.full((2000, 2), 0.5)  # 너무 좁은 구간
    q = conformal_offsets(y, lo0, hi0, 0.9)
    inside = ((y >= lo0 - q) & (y <= hi0 + q)).mean(0)
    assert inside == pytest.approx([0.9, 0.9], abs=0.02)


# ---------------------------------------------------------------- 파이프라인

NACA = ["0006", "0009", "0012", "0015", "0018", "1408", "1412", "1415", "2408", "2410", "2412", "2415",
        "2418", "3412", "3415", "4409", "4412", "4415", "4418", "2312", "2512", "4312", "4512", "6409", "6412",
        "6415", "5412"]


def _synthetic_project(tmp_path: Path) -> Path:
    (tmp_path / "journal.jou").write_text("; dummy\n", encoding="utf-8")
    (tmp_path / "project.toml").write_text(textwrap.dedent("""
        [project]
        template = "journal.jou"

        [cases.sweep]
        aoa = [0]

        [results]
        x_param = "aoa"

        [surrogate]
        summaries = ["runs/summary.csv"]
        compare_summary = "runs_tsst/summary.csv"
        ood_camber = 0.045
        bootstrap = 200
    """), encoding="utf-8")
    rng = random.Random(0)
    rows, tsst = [], []
    for code in NACA:
        f = shape_features(code, tmp_path)
        for re_k in (200, 500):
            for aoa in (-4, 0, 4, 8, 10, 12, 14, 16):
                a_s = 10 + 30 * f["max_thickness"]
                lin = 2 * math.pi * 0.9 * math.radians(min(aoa, a_s) - f["alpha0_deg"])
                cl = lin - (0.05 * (aoa - a_s) if aoa > a_s else 0) + rng.gauss(0, 0.005)
                cd = 0.008 + 0.02 * f["max_thickness"] + 0.01 * cl**2 + (0.01 * (aoa - a_s) if aoa > a_s else 0)
                name = f"N{code}_Re{re_k}k_a{aoa}"
                row = {"case": name, "main_airfoil": code, "re_k": re_k, "reynolds": re_k * 1000, "aoa": aoa,
                       "state": "done", "convergence": "converged", "cl": cl, "cd": cd, "yplus-avg": 0.5}
                if aoa == 16 and code == "0006":
                    row.update(state="failed", convergence="diverged", cl="", cd="")
                rows.append(row)
                if re_k == 200 and code in ("0012", "2412"):
                    tsst.append({**row, "cl": row["cl"] + (0.2 if aoa >= 12 else 0.01) if row["cl"] != "" else ""})
    for path, data in ((tmp_path / "runs" / "summary.csv", rows), (tmp_path / "runs_tsst" / "summary.csv", tsst)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
    return tmp_path / "project.toml"


def test_dataset_and_experiments(tmp_path):
    from aero_automation.surrogate.dataset import SurrogateConfig, build_dataset, load_table
    from aero_automation.surrogate.experiment import compare, run_experiment

    cfg = load_config(_synthetic_project(tmp_path))
    sc = SurrogateConfig.from_project(cfg)
    r = build_dataset(cfg, sc, log=lambda *a: None)
    assert r["counts"]["failed"] == 2 and r["convergence"]["diverged"] == 2  # NACA0006 α16° (Re 2개)
    assert "NACA0012" in r["shapes"] and r["shapes"]["NACA0012"]["source"] == "naca"
    assert {s for s, v in r["split"]["shapes"].items() if v == "ood"} == {
        "NACA6409", "NACA6412", "NACA6415", "NACA5412"}
    assert {"train", "val", "test"} <= set(r["split"]["shapes"].values())
    assert r["turbulence"] and any(abs(t["dcl"]) > 0.1 for t in r["turbulence"])
    assert "yplus-avg" in r["report"]

    table, split = load_table(sc.out_dir(cfg))
    assert table["airfoil"][0].startswith("NACA")
    # 같은 형상의 모든 케이스는 같은 세트
    for shape in set(table["airfoil"]):
        assert len(set(table["split"][table["airfoil"] == shape])) == 1
    assert set(table["regime_truth"]) <= {"pre_stall", "near_stall", "post_stall"}

    base = run_experiment(cfg, sc, {"model": "baseline"}, log=lambda *a: None)
    assert base["sets"]["test"]["cl_mae"] < 0.3
    assert "cl_coverage" not in base["sets"]["test"]

    pytest.importorskip("torch")
    mlp = run_experiment(cfg, sc, {"model": "mlp", "hidden": [16, 16], "n_models": 2, "epochs": 150},
                         log=lambda *a: None)
    m = mlp["sets"]["test"]
    assert {"cl_mae", "log10cd_mae", "cl_coverage", "cl_mae_ci"} <= set(m)
    assert m["cl_mae_ci"][0] <= m["cl_mae"] <= m["cl_mae_ci"][1]
    exp_dir = mlp["dir"]
    for name in ("predictions.csv", "conditions.csv", "boundary_candidates.csv", "metrics.json", "parity.png"):
        assert (exp_dir / name).is_file(), name
    with open(exp_dir / "conditions.csv", encoding="utf-8-sig") as f:
        conds = {(r["set"], r["condition"]) for r in csv.DictReader(f)}
    assert ("test", "regime_truth") in conds and ("ood_holdout", "pi_width") in conds

    added = run_experiment(cfg, sc, {"model": "mlp", "hidden": [16, 16], "n_models": 2, "epochs": 150,
                                     "add_ood": True, "stall_weight": 3.0, "calibrate": "conformal"},
                           log=lambda *a: None)
    assert added["tag"] == "mlp_cst16_h16x16_sw3_addood_cal"
    assert "ood" not in added["sets"] and added["sets"]["ood_holdout"]["n"] == mlp["sets"]["ood_holdout"]["n"]
    assert added["train_rows"] > mlp["train_rows"]
    assert added["conformal_offsets"] is not None

    c = compare(cfg, sc)
    assert "baseline" in c["text"] and "mlp_cst16_h16x16" in c["text"]
    saved = json.loads((exp_dir / "metrics.json").read_text(encoding="utf-8"))
    assert saved["features"][-2:] == ["aoa", "log10re"] and len(saved["features"]) == 18


def test_lgbm_experiment(tmp_path):
    pytest.importorskip("lightgbm")
    from aero_automation.surrogate.dataset import SurrogateConfig, build_dataset
    from aero_automation.surrogate.experiment import run_experiment

    cfg = load_config(_synthetic_project(tmp_path))
    sc = SurrogateConfig.from_project(cfg)
    build_dataset(cfg, sc, log=lambda *a: None)
    r = run_experiment(cfg, sc, {"model": "lgbm", "n_cst": 8}, log=lambda *a: None)
    assert r["tag"] == "lgbm_cst8" and len(r["features"]) == 10
    assert r["sets"]["test"]["log10cd_mae"] < 0.2
