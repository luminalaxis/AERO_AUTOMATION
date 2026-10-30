import json
import textwrap
from pathlib import Path

import pytest

from aero_automation.cases import build_cases
from aero_automation.cli import main
from aero_automation.config import load_config
from aero_automation.geometry import build_geometry
from aero_automation.panel import polar
from aero_automation.runner import read_status
from aero_automation.screening import check_spot, screen

FAKE_SOLVER = Path(__file__).with_name("fake_solver.py")


def test_panel_matches_theory():
    g = build_geometry({"main_airfoil": "0012"}, Path("."))
    p0, p4 = polar(g, [0, 4], re=3e6)
    assert p0.cl == pytest.approx(0.0, abs=1e-3)       # 대칭 익형
    assert p4.cl == pytest.approx(0.478, abs=0.02)     # 패널법 기준값 (얇은 익형 이론 + 두께 효과)
    assert 0.007 < p0.cd < 0.011                       # 완전 난류 평판 수준
    g = build_geometry({"main_airfoil": "2412"}, Path("."))
    (p,) = polar(g, [0], re=3e6)
    assert p.cl == pytest.approx(0.24, abs=0.02)


def test_separation_grows_with_alpha():
    g = build_geometry({"main_airfoil": "0012"}, Path("."))
    seps = [p.sep_upper[0] for p in polar(g, [0, 8, 16, 20], re=3e6)]
    assert seps[0] < 0.01 and seps == sorted(seps) and seps[-1] > 0.3


def test_flap_adds_lift():
    single = polar(build_geometry({"main_airfoil": "2412"}, Path(".")), [4], re=3e6)[0]
    two = polar(build_geometry(dict(main_airfoil="2412", flap_airfoil="4412", flap_chord=0.3,
                                    flap_deflection=20, gap=0.02, overlap=0.01), Path(".")), [4], re=3e6)[0]
    assert two.cl > single.cl + 1.0
    assert len(two.cl_elements) == 2 and two.cl_elements[1] > 0


def make_project(tmp_path):
    (tmp_path / "journal.jou").write_text("AOA=${aoa}\n", encoding="utf-8")
    (tmp_path / "project.toml").write_text(textwrap.dedent(f"""
        [project]
        template = "journal.jou"

        [solver]
        kind = "custom"
        command = ["${{python}}", "{FAKE_SOLVER.as_posix()}", "${{journal}}"]

        [constants]
        flap_airfoil = "4412"
        flap_chord = 0.3
        flap_deflection = 20
        overlap = 0.01
        mach = 0.15

        [cases]
        name = "{{main_airfoil}}_g{{gap}}_a{{aoa}}"

        [cases.sweep]
        main_airfoil = ["0012", "2412", "4412"]
        gap = [0.015, 0.03]
        aoa = [0, 8, 16, 24]

        [screening]
        keep_top = 1
        keep_within = 0.0
        spot_check = 1
        n_side = 50

        [results]
        objective = "max_ld"
    """), encoding="utf-8")
    return tmp_path / "project.toml"


def test_screen_prunes_and_run_respects_selection(tmp_path, capsys):
    cfg_path = make_project(tmp_path)
    cfg = load_config(cfg_path)
    cases = build_cases(cfg)
    r = screen(cfg, cases, log=lambda *_: None)

    assert r["n_families"] == 3 and r["n_families_kept"] == 1
    best_family = r["geometries"][0]["main_airfoil"]
    kept_cases = [c for c in cases if c.name in r["kept"]]
    spot = {n for names in r["spot_check"].values() for n in names}
    assert spot and all(not n.startswith(best_family) for n in spot)
    # 유지 계열: 갭은 보호 변수라 두 갭 모두 남고, 실속 이후 24°는 제외
    main = [c for c in kept_cases if c.name not in spot]
    assert {c.params["main_airfoil"] for c in main} == {best_family}
    assert {c.params["gap"] for c in main} == {0.015, 0.03}
    assert all(c.params["aoa"] < 24 for c in main)
    assert len(r["kept"]) < len(cases) / 2

    out = r["dir"]
    for f in ("geometries.csv", "cases.csv", "selection.json", "sensitivity_main_effects.png",
              "sensitivity_importance.png", "screening_polars.png", "screening_scatter.png"):
        assert (out / f).is_file(), f

    assert main_cli(cfg_path, "run") == 0
    states = {c.name: read_status(c)["state"] for c in cases}
    assert all(states[n] == "done" for n in r["kept"])
    assert all(states[n] == "screened" for n in r["pruned"])
    assert "스크리닝 반영" in capsys.readouterr().out

    # --all 이면 제외된 케이스도 실행
    assert main_cli(cfg_path, "run", "--all") == 0
    assert all(read_status(c)["state"] == "done" for c in cases)
    assert (cfg.output_dir / "plots" / "screening_vs_cfd_cl.png").is_file()


def main_cli(cfg_path, *args):
    return main(["-c", str(cfg_path), *args])


def test_check_spot_warns_when_pruned_geometry_wins():
    selection = {"metric": "ld_max_est", "spot_check": {"geoB": ["b_a4"]}}
    ranking = [{"mesh": "geoA", "ld_max": 80.0}, {"mesh": "geoC", "ld_max": 60.0}, {"mesh": "geoB", "ld_max": 70.0}]
    warnings = check_spot(selection, ranking)
    assert len(warnings) == 1 and "geoB" in warnings[0]
    ranking[2]["ld_max"] = 50.0
    assert check_spot(selection, ranking) == []
