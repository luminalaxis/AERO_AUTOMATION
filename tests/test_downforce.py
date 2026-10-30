import math
import textwrap
from pathlib import Path

import numpy as np
import pytest

from aero_automation.cases import build_cases
from aero_automation.config import load_config
from aero_automation.fluent_msh import check_msh
from aero_automation.geometry import build_geometry, signed_distance
from aero_automation.meshing import generate_mesh, make_job, mesh_name
from aero_automation.results import _forces_per_span, _wind_axes
from aero_automation.screening import ScreeningConfig, evaluate_geometry

THREE = dict(main_airfoil="2412", main_chord=350, flap1_airfoil="4412", flap1_chord=120,
             flap1_deflection=20, flap1_gap=7, flap1_overlap=5,
             flap2_airfoil="4412", flap2_chord=90, flap2_deflection=40, flap2_gap=5, flap2_overlap=3)
DOWN = {**THREE, "downforce": True}


def test_inverted_geometry_is_mirror_image():
    up = build_geometry(THREE, Path("."))
    dn = build_geometry(DOWN, Path("."))
    for a, b in zip(up.elements, dn.elements):
        assert np.allclose(np.sort(a.xy[:, 0]), np.sort(b.xy[:, 0]))
        assert np.allclose(np.sort(a.xy[:, 1]), np.sort(-b.xy[:, 1]))
        assert b.xy[: b.le_index + 1, 1].mean() > b.xy[b.le_index:, 1].mean()  # 위쪽 면 먼저 (Selig)
    main, f1, f2 = dn.elements
    # 플랩이 메인 뒷전 위쪽에 있고, 갭은 그대로
    assert f1.xy[:, 1].mean() > main.te_upper[1]
    assert signed_distance(main.te_upper, f1.xy)[0] * 350 == pytest.approx(7, abs=1e-6)
    assert signed_distance(f1.te_upper, f2.xy)[0] * 350 == pytest.approx(5, abs=1e-6)
    assert mesh_name(DOWN).endswith("_inv")


def test_inverted_mesh(tmp_path):
    settings = dict(chord=0.35, reynolds=2e5, surface_cells=60, flap_surface_cells=40,
                    farfield_radius=10, farfield_size=1.0, near_size=0.03, wake_size=0.05, wake_length=1.0)
    job = make_job(DOWN, settings, tmp_path)
    info = generate_mesh(job, Path("."))
    assert info["geometry_info"]["downforce"] is True
    check = check_msh(job.mesh_file)
    assert check["bad_cells"] == 0 and check["open_cells"] == 0
    assert (job.dir / "mesh_slot2.png").is_file()


def test_wind_axes_downforce_sign():
    # 뒤집힌 날개, 받음각 5° (다운포스 증가 방향) → Fluent 유입 각도 -5°
    downforce, drag, aoa = 2.0, 0.1, 5.0
    th = math.radians(-aoa)
    cl_std = -downforce  # 표준 좌표에서 아래 방향 힘
    fx = drag * math.cos(th) - cl_std * math.sin(th)
    fy = drag * math.sin(th) + cl_std * math.cos(th)
    row = {"aoa": aoa, "flow_angle": -aoa, "downforce": True, "cx": fx, "cy": fy,
           "velocity": 20.0, "chord": 0.35}
    _wind_axes(row, "aoa")
    assert row["cl"] == pytest.approx(downforce)
    assert row["cd"] == pytest.approx(drag)
    row["cl"], row["cd"] = float(row["cl"]), float(row["cd"])
    _forces_per_span(row)
    assert row["downforce_N_per_m"] == pytest.approx(0.5 * 1.225 * 400 * 0.35 * downforce)
    assert "lift_N_per_m" not in row


def test_screening_downforce_equals_upright():
    sc = ScreeningConfig(n_side=50)
    up = evaluate_geometry(THREE, Path("."), [0, 8], 3e5, 0.05, sc, "aoa")
    dn = evaluate_geometry(DOWN, Path("."), [0, 8], 3e5, 0.05, sc, "aoa")
    assert dn["cl_max_est"] == pytest.approx(up["cl_max_est"]) and dn["cl_max_est"] > 0


def test_flow_angle_variable(tmp_path):
    (tmp_path / "j.jou").write_text("${flow_angle}\n", encoding="utf-8")
    (tmp_path / "project.toml").write_text(textwrap.dedent("""
        [project]
        template = "j.jou"
        [solver]
        kind = "custom"
        command = ["x"]
        [constants]
        main_airfoil = "2412"
        downforce = true
        [cases]
        name = "a{aoa}"
        [cases.sweep]
        aoa = [0, 6]
    """), encoding="utf-8")
    cases = build_cases(load_config(tmp_path / "project.toml"))
    assert [c.params["flow_angle"] for c in cases] == [0, -6]
