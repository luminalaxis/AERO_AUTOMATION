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
    two = polar(build_geometry(dict(main_airfoil="2412", flap1_airfoil="4412", flap1_chord=0.3,
                                    flap1_deflection=20, flap1_gap=0.02, flap1_overlap=0.01), Path(".")), [4], re=3e6)[0]
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
        flap1_airfoil = "4412"
        flap1_chord = 0.3
        flap1_deflection = 20
        flap1_overlap = 0.01
        mach = 0.15

        [cases]
        name = "{{main_airfoil}}_g{{flap1_gap}}_a{{aoa}}"

        [cases.sweep]
        main_airfoil = ["0012", "2412", "4412"]
        flap1_gap = [0.015, 0.03]
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

    (cond,) = r["conditions"]
    assert cond["families"] == 3 and cond["families_kept"] == 1
    best_family = r["geometries"][0]["main_airfoil"]
    kept_cases = [c for c in cases if c.name in r["kept"]]
    spot = {n for s in r["spot_check"] for n in s["cases"]}
    assert spot and all(not n.startswith(best_family) for n in spot)
    # 유지 계열: 갭은 보호 변수라 두 갭 모두 남고, 실속 이후 24°는 제외
    main = [c for c in kept_cases if c.name not in spot]
    assert {c.params["main_airfoil"] for c in main} == {best_family}
    assert {c.params["flap1_gap"] for c in main} == {0.015, 0.03}
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
    selection = {"metric": "ld_max_est",
                 "spot_check": [{"geometry": "geoB", "condition": {"v": 40}, "cases": ["b_a4"]}]}
    ranking = [{"mesh": "geoA", "v": 40, "ld_max": 80.0}, {"mesh": "geoC", "v": 40, "ld_max": 60.0},
               {"mesh": "geoB", "v": 40, "ld_max": 70.0},
               {"mesh": "geoC", "v": 100, "ld_max": 10.0}]  # 다른 속도는 비교 대상 아님
    warnings = check_spot(selection, ranking)
    assert len(warnings) == 1 and "geoB" in warnings[0] and "v=40" in warnings[0]
    ranking[2]["ld_max"] = 50.0
    assert check_spot(selection, ranking) == []


def test_screen_per_speed(tmp_path):
    """속도별로 Re/Mach가 달라지고, 선별/그래프가 속도마다 따로 만들어진다."""
    cfg_path = make_project(tmp_path)
    text = cfg_path.read_text()
    text = text.replace('mach = 0.15\n', 'chord = 0.1157\n')
    text = text.replace('name = "{main_airfoil}_g{flap1_gap}_a{aoa}"', 'name = "{main_airfoil}_g{flap1_gap}_v{speed_kmh}_a{aoa}"')
    text = text.replace('aoa = [0, 8, 16, 24]', 'aoa = [0, 8, 16, 24]\nspeed_kmh = [40, 100]')
    text = text.replace('[screening]', '''[derived]
velocity = "speed_kmh / 3.6"
mach = "velocity / 340.29"
reynolds = "1.225 * velocity * chord / 1.7894e-5"

[mesh]
reynolds = 1e5

[screening]''')
    text = text.replace('objective = "max_ld"', 'objective = "max_ld"\nsplit_by = ["speed_kmh"]')
    cfg_path.write_text(text)
    cfg = load_config(cfg_path)
    cases = build_cases(cfg)

    c100 = next(c for c in cases if c.params["speed_kmh"] == 100)
    assert c100.params["reynolds"] == pytest.approx(2.2e5, rel=0.01)
    assert c100.params["mach"] == pytest.approx(0.0816, rel=0.01)
    # 메쉬는 속도와 무관하게 형상당 하나, 첫 셀은 최고 Re 기준
    c40 = next(c for c in cases if c.params["speed_kmh"] == 40 and c.mesh.name == c100.mesh.name)
    assert c40.mesh is not None and c40.mesh.settings.reynolds == pytest.approx(c100.params["reynolds"])
    assert len({c.mesh.name for c in cases}) == 6

    r = screen(cfg, cases, log=lambda *_: None)
    assert [c["condition"] for c in r["conditions"]] == ["speed_kmh=40", "speed_kmh=100"]
    re_by_speed = {g["speed_kmh"]: g["reynolds"] for g in r["geometries"]}
    assert re_by_speed[40] == pytest.approx(re_by_speed[100] * 0.4)
    for sub in ("speed_kmh=40", "speed_kmh=100"):
        assert (r["dir"] / sub / "sensitivity_main_effects.png").is_file()
    assert {s["condition"]["speed_kmh"] for s in r["spot_check"]} == {40, 100}


def test_ranking_per_condition(tmp_path):
    from aero_automation.plots import plot_condition_trend
    from aero_automation.results import rank_geometries

    rows = []
    for v, bonus in ((40, 0.0), (100, 1.0)):
        for geo, ld in (("A", 50), ("B", 60)):
            for aoa in (0, 4):
                # 40 km/h에서는 B가, 100 km/h에서는 A가 우세하도록
                score = ld + aoa + (30 * bonus if geo == "A" else 0)
                rows.append({"state": "done", "geo": geo, "speed_kmh": v, "aoa": aoa,
                             "cl": 1.0, "cd": 1.0 / score, "l/d": float(score)})
    ranking = rank_geometries(rows, ["geo", "speed_kmh"], "aoa", "max_ld", ["speed_kmh"])
    best = {e["speed_kmh"]: e["geo"] for e in ranking if e["rank"] == 1}
    assert best == {40: "B", 100: "A"}
    assert sorted(e["rank"] for e in ranking) == [1, 1, 2, 2]
    assert plot_condition_trend(ranking, "speed_kmh", ["geo"], "max_ld", tmp_path / "t.png").is_file()


def test_chord_sweep_meshes_and_forces(tmp_path):
    """코드 길이를 스윕하면 코드별로 메쉬가 따로 생기고, Re는 속도·코드로 계산된다."""
    cfg_path = make_project(tmp_path)
    text = cfg_path.read_text()
    text = text.replace('mach = 0.15\n', '')
    text = text.replace('aoa = [0, 8, 16, 24]', 'aoa = [0, 8]\nspeed_kmh = [40, 100]\nchord = [0.1, 0.2]')
    text = text.replace('name = "{main_airfoil}_g{flap1_gap}_a{aoa}"',
                        'name = "{main_airfoil}_c{chord}_g{flap1_gap}_v{speed_kmh}_a{aoa}"')
    text = text.replace('[screening]', '''[derived]
velocity = "speed_kmh / 3.6"
mach = "velocity / 340.29"
reynolds = "1.225 * velocity * chord / 1.7894e-5"

[mesh]

[screening]''')
    cfg_path.write_text(text)
    cfg = load_config(cfg_path)
    cases = build_cases(cfg)

    # 형상 6개 × 코드 2개 = 메쉬 12개, 속도는 공유
    assert len({c.mesh.name for c in cases}) == 12
    c = next(c for c in cases if c.params["chord"] == 0.2 and c.params["speed_kmh"] == 40)
    assert c.mesh.name.endswith("_c0.2")
    assert c.mesh.settings.chord == 0.2
    re_100 = 1.225 * (100 / 3.6) * 0.2 / 1.7894e-5
    assert c.mesh.settings.reynolds == pytest.approx(re_100)  # 같은 코드의 최고 속도 기준

    r = screen(cfg, cases, log=lambda *_: None)
    assert {g["chord"] for g in r["geometries"]} == {0.1, 0.2}


def test_forces_per_span():
    from aero_automation.results import _forces_per_span

    row = {"cl": 1.5, "cd": 0.05, "velocity": 20.0, "chord": 0.15}
    _forces_per_span(row)
    q = 0.5 * 1.225 * 20.0**2
    assert row["lift_N_per_m"] == pytest.approx(q * 0.15 * 1.5)
    assert row["drag_N_per_m"] == pytest.approx(q * 0.15 * 0.05)
