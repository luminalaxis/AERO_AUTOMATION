"""공통 코어 테스트: 수렴 판정, CSV 변환, 자유류 영역(분할·직사각형) 메쉬, 단면 뒷전 처리."""
import textwrap

import numpy as np
import pytest

from aero_automation.cases import _coerce
from aero_automation.convergence import ConvergenceConfig, classify, solver_log_info
from aero_automation.geometry import load_section, naca4, normalize


# ---------------------------------------------------------------- 수렴 판정

def _hist(cl, cd=None):
    cl = np.asarray(cl, float)
    return cl, np.full_like(cl, 0.02) if cd is None else np.asarray(cd, float)


def test_convergence_states():
    cfg = ConvergenceConfig(window=100)
    it = np.arange(500)
    steady = _hist(0.8 + 0.5 * np.exp(-it / 20))
    assert classify(steady, cfg)["conv_state"] == "converged"

    osc = classify(_hist(0.8 + 0.05 * np.sin(it / 5)), cfg)
    assert osc["conv_state"] == "oscillating" and osc["conv_reason"].startswith("amplitude")
    assert osc["conv_cl_amp"] == pytest.approx(0.1, rel=0.05)

    drift = classify(_hist(0.5 + 0.001 * it), cfg)
    assert drift["conv_state"] == "oscillating" and drift["conv_reason"].startswith("drift")

    bad = _hist(np.r_[np.full(450, 0.5), np.full(50, np.nan)])
    assert classify(bad, cfg)["conv_state"] == "diverged"
    assert classify(_hist(np.full(500, 50.0)), cfg)["conv_state"] == "diverged"
    assert classify(None, cfg, {"state": "failed", "message": "로그 오류: Divergence detected"})["conv_state"] == "diverged"
    assert classify(None, cfg, {"state": "failed", "message": "종료 코드 3"})["conv_state"] == "no_data"
    # 진동하지만 Fluent가 잔차 기준으로 수렴을 선언한 경우
    assert classify(_hist(0.8 + 0.05 * np.sin(it / 5)), cfg, log_info={"solver_converged": True})["conv_state"] == "converged"


def test_solver_log_residuals(tmp_path):
    log = tmp_path / "solver.log"
    log.write_text(textwrap.dedent("""\
        iter  continuity  x-velocity  y-velocity           k       omega     cx      cy   time/iter
         1  1.0000e+00  1.0e-02  1.0e-02  1.0e-01  1.0e-01  0.01  0.5  0:00:01  2999
      3000  2.5000e-05  3.0e-07  4.0e-07  5.0e-06  6.0e-06  0.02  0.8  0:00:00     0
        """), encoding="utf-8")
    info = solver_log_info(log)
    assert info["res_continuity"] == pytest.approx(2.5e-5)
    assert info["res_omega"] == pytest.approx(6e-6)
    assert "res_cx" not in info and "solver_converged" not in info


# ---------------------------------------------------------------- CSV

def test_csv_keeps_zero_padded_naca_codes():
    assert _coerce("0012") == "0012"
    assert _coerce("2412") == 2412
    assert _coerce("0.5") == 0.5 and _coerce("0") == 0


# ---------------------------------------------------------------- 메쉬

def test_split_farfield_mesh(tmp_path):
    from aero_automation.meshing import make_job, generate_mesh

    job = make_job({"main_airfoil": "0012", "chord": 0.3, "reynolds": 5e5},
                   {"farfield_split": True, "surface_cells": 80, "near_size": 0.03, "wake_size": 0.03,
                    "farfield_radius": 20}, tmp_path)
    info = generate_mesh(job, tmp_path, plots=False)
    assert set(info["zones"]) == {"interior-fluid", "main", "inlet", "outlet"}


def test_box_enclosure_mesh(tmp_path):
    from aero_automation.fluent_msh import check_msh, read_msh
    from aero_automation.meshing import make_job, generate_mesh

    job = make_job({"main_airfoil": "2412", "chord": 0.3, "reynolds": 5e5},
                   {"farfield_shape": "box", "box_upstream": 10, "box_downstream": 20, "box_height": 8,
                    "box_far_size": 1.0, "surface_cells": 80, "near_size": 0.03, "wake_size": 0.03}, tmp_path)
    info = generate_mesh(job, tmp_path)
    assert set(info["zones"]) == {"interior-fluid", "main", "inlet", "outlet", "top", "bottom"}
    # 앞 변만 inlet, 위·아래 변은 wall (top/bottom), 뒤 변은 outlet
    msh = read_msh(job.mesh_file)
    nodes = msh["nodes"]
    zone = {name: (zid, bc) for zid, (bc, name) in msh["names"].items()}
    for name, bc, axis, pos in (("inlet", "velocity-inlet", 0, -10), ("top", "wall", 1, 8),
                                ("bottom", "wall", 1, -8), ("outlet", "pressure-outlet", 0, 20)):
        zid, zone_bc = zone[name]
        assert zone_bc == bc
        pts = np.array([nodes[n - 1] for f in msh["zones"][zid] for n in msh["faces"][f][:2]])
        np.testing.assert_allclose(pts[:, axis], pos * 0.3, atol=1e-9)
    check = check_msh(job.mesh_file)
    assert not check["bad_cells"] and not check["open_cells"]
    # 직사각형 영역: x −10c ~ 20c, y ±8c (코드 0.3 m → m 단위)
    np.testing.assert_allclose(nodes.min(0), [-10 * 0.3, -8 * 0.3], atol=1e-9)
    np.testing.assert_allclose(nodes.max(0), [20 * 0.3, 8 * 0.3], atol=1e-9)
    assert (job.dir / "mesh_domain.png").is_file()
    assert info["settings"]["farfield_shape"] == "box"


def test_box_settings_validated(tmp_path):
    from aero_automation.config import ConfigError
    from aero_automation.meshing import make_job

    with pytest.raises(ConfigError, match="farfield_shape"):
        make_job({"main_airfoil": "0012"}, {"farfield_shape": "square"}, tmp_path)
    with pytest.raises(ConfigError, match="box_"):
        make_job({"main_airfoil": "0012"}, {"farfield_shape": "box", "box_height": 0}, tmp_path)


FLAP_PARAMS = {"main_airfoil": "2412", "flap1_airfoil": "4412", "flap1_chord": 0.3, "flap1_deflection": 25,
               "flap1_overlap": 0.01, "flap1_gap": 0.02}


def test_mesh_fingerprint_unchanged_by_new_settings(tmp_path):
    from aero_automation.meshing import make_job

    # 새 항목(farfield_split, farfield_shape, box_*, timeout) 추가 전과 같은 값 → 기존 메쉬를 다시 만들지 않음
    assert make_job(FLAP_PARAMS, {}, tmp_path).fingerprint() == "28eb3c2799f1"
    assert make_job(FLAP_PARAMS, {"timeout": 10}, tmp_path).fingerprint() == "28eb3c2799f1"
    assert make_job(FLAP_PARAMS, {"farfield_shape": "circle", "box_height": 20}, tmp_path).fingerprint() == "28eb3c2799f1"
    assert make_job(FLAP_PARAMS, {"farfield_split": True}, tmp_path).fingerprint() != "28eb3c2799f1"
    assert make_job(FLAP_PARAMS, {"farfield_shape": "box"}, tmp_path).fingerprint() != "28eb3c2799f1"


def test_cusp_trailing_edge_is_opened(tmp_path):
    from aero_automation.geometry import CUSP_WEDGE_DEG, split_te_wedge

    xy, le = normalize(naca4("2412", closed_te=True))
    # 뒷전 바로 앞 점 쌍을 거의 겹치게 만든 칼날형 뒷전
    xy[1] = [0.9968, 0.00037]
    xy[-2] = [0.9967, 0.00036]
    path = tmp_path / "cusp.dat"
    path.write_text("cusp\n" + "\n".join(f"{x:.6f} {y:.6f}" for x, y in xy), encoding="utf-8")
    el = load_section("cusp.dat", tmp_path, "main")
    assert len(el.xy) == len(xy) - 2
    assert split_te_wedge(el.xy) >= CUSP_WEDGE_DEG
    assert len(load_section("2412", tmp_path, "main").xy) == len(normalize(naca4("2412"))[0])  # NACA는 그대로


def test_mesh_timeout_fails_only_that_geometry(tmp_path):
    from aero_automation.config import ConfigError
    from aero_automation.meshing import ERROR_FILE, generate_mesh, make_job

    job = make_job({"main_airfoil": "0012"}, {"timeout": 0.01}, tmp_path)
    with pytest.raises(ConfigError, match="시간 초과"):
        generate_mesh(job, tmp_path, plots=False)
    assert "시간 초과" in (job.dir / ERROR_FILE).read_text(encoding="utf-8")
    assert not job.mesh_file.exists()


def test_tiny_trailing_edge_gap_is_closed(tmp_path):
    xy, _ = normalize(naca4("2412", closed_te=True))
    xy[-1] = xy[0] + [0.0, 1e-5]  # 반올림으로 생긴 0.00001c 간격 (S1221 좌표 파일과 같은 상황)
    path = tmp_path / "gap.dat"
    path.write_text("gap\n" + "\n".join(f"{x:.7f} {y:.7f}" for x, y in xy), encoding="utf-8")
    el = load_section("gap.dat", tmp_path, "main")
    assert el.sharp_te
    assert not load_section("2412", tmp_path, "main").sharp_te  # 표준 NACA(뒷전 두께 0.0025c)는 그대로
