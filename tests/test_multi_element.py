import json
import textwrap
from pathlib import Path

import pytest

from aero_automation.cases import build_cases
from aero_automation.config import load_config
from aero_automation.fluent_msh import check_msh
from aero_automation.geometry import build_geometry, signed_distance
from aero_automation.meshing import generate_mesh, make_job, mesh_name
from aero_automation.panel import polar

THREE = dict(main_airfoil="2412", main_chord=350, flap1_airfoil="4412", flap1_chord=120,
             flap1_deflection=20, flap1_gap=7, flap1_overlap=5,
             flap2_airfoil="4412", flap2_chord=90, flap2_deflection=40, flap2_gap=5, flap2_overlap=3)


def test_three_element_geometry_in_mm():
    g = build_geometry(THREE, Path("."))
    main, f1, f2 = g.elements
    assert [e.name for e in g.elements] == ["main", "flap1", "flap2"]
    # 내부 좌표는 메인 코드 기준 → mm로 환산하면 입력 갭과 일치
    assert signed_distance(main.te_lower, f1.xy)[0] * 350 == pytest.approx(7, abs=1e-6)
    assert signed_distance(f1.te_lower, f2.xy)[0] * 350 == pytest.approx(5, abs=1e-6)
    # 플랩 코드 길이 (mm)
    assert (f1.xy[:, 0].max() - f1.xy[:, 0].min()) * 350 == pytest.approx(120 * 0.94, rel=0.06)
    assert [f["clearance"] for f in g.info["flaps"]] == pytest.approx([7, 5], abs=0.05)


def test_mesh_name_lists_every_element():
    assert mesh_name(THREE) == "2412c350_4412c120d20g7o5_4412c90d40g5o3"
    two = {k: v for k, v in THREE.items() if not k.startswith("flap2")}
    assert mesh_name(two) == "2412c350_4412c120d20g7o5"


def test_three_element_mesh(tmp_path):
    settings = dict(chord=0.35, reynolds=2e5, surface_cells=60, flap_surface_cells=40,
                    farfield_radius=10, farfield_size=1.0, near_size=0.03, wake_size=0.05, wake_length=1.0)
    job = make_job(THREE, settings, tmp_path)
    info = generate_mesh(job, Path("."))
    assert set(info["zones"]) == {"interior-fluid", "main", "flap1", "flap2", "farfield"}
    check = check_msh(job.mesh_file)
    assert check["bad_cells"] == 0 and check["open_cells"] == 0
    for f in ("mesh_slot1.png", "mesh_slot2.png", "geometry.png"):
        assert (job.dir / f).is_file()


def test_three_element_panel():
    two = {k: v for k, v in THREE.items() if not k.startswith("flap2")}
    p2 = polar(build_geometry(two, Path(".")), [4], re=3e5)[0]
    p3 = polar(build_geometry(THREE, Path(".")), [4], re=3e5)[0]
    assert len(p3.cl_elements) == 3
    assert p3.cl > p2.cl  # 두 번째 플랩이 양력 추가


def test_case_sets_two_and_three_element(tmp_path):
    (tmp_path / "j.jou").write_text("${case_set} ${base_case}\n", encoding="utf-8")
    (tmp_path / "project.toml").write_text(textwrap.dedent("""
        [project]
        template = "j.jou"

        [solver]
        kind = "custom"
        command = ["x"]

        [constants]
        main_airfoil = "2412"
        flap1_airfoil = "4412"
        flap1_overlap = 5

        [cases.sweep]          # 공통
        aoa = [0, 8]
        main_chord = [300, 450]

        [derived]
        chord = "main_chord / 1000"

        [mesh]

        [[cases.set]]
        label = "2el"
        name = "2el_m{main_chord}_f{flap1_chord}_a{aoa}"
        constants = { base_case = "base_2el.cas.h5", flap1_deflection = 25, flap1_gap = 6 }
        sweep = { flap1_chord = [80, 150] }

        [[cases.set]]
        label = "3el"
        name = "3el_m{main_chord}_f{flap1_chord}_{flap2_chord}_a{aoa}"
        constants = { base_case = "base_3el.cas.h5", flap1_deflection = 15, flap1_gap = 6, flap2_airfoil = "4412", flap2_deflection = 35, flap2_gap = 5, flap2_overlap = 3, flap1_chord = 100 }
        sweep = { flap2_chord = [80, 150] }
    """), encoding="utf-8")
    cfg = load_config(tmp_path / "project.toml")
    cases = build_cases(cfg)
    assert len(cases) == 2 * 2 * 2 + 2 * 2 * 2
    by_set = {s: [c for c in cases if c.params["case_set"] == s] for s in ("2el", "3el")}
    assert all("flap2_airfoil" not in c.params for c in by_set["2el"])
    assert all(c.params["base_case"] == "base_3el.cas.h5" for c in by_set["3el"])
    c = by_set["3el"][0]
    assert c.mesh.settings.chord == pytest.approx(0.3)
    assert c.mesh.name.count("_") == 2  # main + flap1 + flap2
    assert len({c.mesh.name for c in cases}) == 8  # 받음각만 공유


def test_screening_selects_within_each_case_set(tmp_path):
    from aero_automation.screening import screen

    test_case_sets_two_and_three_element(tmp_path)
    cfg = load_config(tmp_path / "project.toml")
    cases = build_cases(cfg)
    cfg.screening = {"keep_top": 1, "keep_within": 0.0, "spot_check": 0, "n_side": 50}
    r = screen(cfg, cases, log=lambda *_: None)
    labels = [c["condition"] for c in r["conditions"]]
    assert labels == ["case_set=2el", "case_set=3el"]
    kept_sets = {c.params["case_set"] for c in cases if c.name in r["kept"]}
    assert kept_sets == {"2el", "3el"}  # 3단이 L/D에서 불리해도 3단 안에서 따로 선별
    assert (r["dir"] / "case_set=3el" / "sensitivity_importance.png").is_file()
