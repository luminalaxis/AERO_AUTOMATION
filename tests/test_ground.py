import textwrap
from pathlib import Path

import numpy as np
import pytest

from aero_automation.cases import build_cases
from aero_automation.config import ConfigError, load_config
from aero_automation.fluent_msh import check_msh
from aero_automation.geometry import build_geometry
from aero_automation.meshing import generate_mesh, make_job, mesh_name
from aero_automation.results import _wind_axes
from aero_automation.screening import ScreeningConfig, evaluate_geometry, screen

TWO = dict(main_airfoil="4412", main_chord=375, flap1_airfoil="4412", flap1_chord=115,
           flap1_deflection=20, flap1_gap=6, flap1_overlap=5, downforce=True)


def test_ground_placement_lowest_and_rotation_about_main_le():
    g0 = build_geometry({**TWO, "ground_clearance": 40, "aoa": 0}, Path("."))
    g8 = build_geometry({**TWO, "ground_clearance": 40, "aoa": 8}, Path("."))
    for g in (g0, g8):
        lowest = min(el.xy[:, 1].min() for el in g.elements)
        assert (lowest - g.info["ground_y"]) * 375 == pytest.approx(40)
        # 메인 앞전이 회전 중심 → 앞전 위치는 그대로
        main = g.elements[0]
        assert main.xy[main.le_index] == pytest.approx([0, 0], abs=1e-9)
    # 뒤집힌 날개: 양의 aoa = 앞전이 내려가는(뒷전이 올라가는) 회전 → 다운포스 증가 방향
    assert g8.elements[0].te_upper[1] > g0.elements[0].te_upper[1] + 0.1
    assert g8.info["main_le_height"] < g0.info["main_le_height"]


def test_ground_ref_main_le():
    g = build_geometry({**TWO, "ground_clearance": 60, "aoa": 8, "ground_ref": "main_le"}, Path("."))
    assert g.info["main_le_height"] == pytest.approx(60)
    with pytest.raises(ConfigError, match="지면과 겹칩니다"):
        build_geometry({**TWO, "ground_clearance": 30, "aoa": 0, "ground_ref": "main_le"}, Path("."))


def test_mesh_name_includes_clearance_and_aoa():
    assert mesh_name({**TWO, "ground_clearance": 30, "aoa": 4}).endswith("_inv_h30_a4")
    assert mesh_name({**TWO, "ground_clearance": 30}).endswith("_inv_h30")


def test_ground_mesh_boundaries(tmp_path):
    settings = dict(chord=0.375, reynolds=5e5, surface_cells=60, flap_surface_cells=40,
                    near_size=0.03, wake_size=0.05, wake_length=1.0, ground_downstream=4, ground_height=3)
    job = make_job({**TWO, "ground_clearance": 30, "aoa": 4}, settings, tmp_path)
    info = generate_mesh(job, Path("."))
    assert set(info["zones"]) == {"interior-fluid", "main", "flap1", "ground", "inlet", "outlet", "top"}
    check = check_msh(job.mesh_file)
    assert check["bad_cells"] == 0 and check["open_cells"] == 0
    bc_of = {name: bc for bc, name in check["names"].values()}
    assert bc_of["ground"] == "wall"
    assert bc_of["inlet"] == "velocity-inlet"
    assert bc_of["outlet"] == "pressure-outlet"
    assert bc_of["top"] == "symmetry"


def test_screening_ground_effect_trend():
    sc = ScreeningConfig(n_side=50, transition="free")
    res = {h: evaluate_geometry({**TWO, "ground_clearance": h}, Path("."), [4], 5e5, 0.05, sc, "aoa")
           for h in (30, 60, 400)}
    cl = {h: res[h]["_by_alpha"][4.0].cl for h in res}
    assert cl[30] > cl[60] > cl[400] > 0  # 지면이 가까울수록 다운포스 증가 (양수 = 아래 방향)


def test_wind_axes_ground_downforce():
    row = {"aoa": 6, "flow_angle": 0.0, "downforce": True, "cx": 0.12, "cy": -3.0}
    _wind_axes(row, "aoa")
    assert row["cl"] == pytest.approx(3.0) and row["cd"] == pytest.approx(0.12)


def test_ground_cases_and_screening(tmp_path):
    (tmp_path / "j.jou").write_text("${velocity} ${mesh_file}\n", encoding="utf-8")
    (tmp_path / "project.toml").write_text(textwrap.dedent("""
        [project]
        template = "j.jou"
        [solver]
        kind = "custom"
        command = ["x"]
        [constants]
        main_airfoil = "4412"
        main_chord = 375
        flap1_airfoil = "4412"
        flap1_chord = 115
        flap1_gap = 6
        flap1_overlap = 5
        downforce = true
        [cases]
        name = "d{flap1_deflection}_h{ground_clearance}_a{aoa}"
        [cases.sweep]
        flap1_deflection = [15, 30]
        ground_clearance = [30, 60]
        aoa = [0, 6]
        [derived]
        chord = "main_chord / 1000"
        velocity = "100 / 3.6"
        reynolds = "1.225 * velocity * chord / 1.7894e-5"
        [mesh]
        [screening]
        n_side = 50
        workers = 1
        spot_check = 0
    """), encoding="utf-8")
    cfg = load_config(tmp_path / "project.toml")
    cases = build_cases(cfg)
    assert all(c.params["flow_angle"] == 0.0 for c in cases)
    assert len({c.mesh.name for c in cases}) == 8  # 플랩각 2 × 지상고 2 × 받음각 2
    r = screen(cfg, cases, log=lambda *_: None)
    assert len(r["geometries"]) == 4  # 받음각은 형상 단위에서 빠짐
    assert {g["ground_clearance"] for g in r["geometries"]} == {30, 60}
