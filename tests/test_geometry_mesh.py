import csv
import json
import textwrap
from pathlib import Path

import numpy as np
import pytest

from aero_automation.cases import build_cases
from aero_automation.cli import main
from aero_automation.config import ConfigError, load_config
from aero_automation.fluent_msh import Boundary, check_msh, read_msh, write_msh
from aero_automation.geometry import build_geometry, naca4, normalize, signed_distance

FAKE_SOLVER = Path(__file__).with_name("fake_solver.py")
FLAP = dict(main_airfoil="2412", flap1_airfoil="4412", flap1_chord=0.3, flap1_deflection=25, flap1_overlap=0.01)


def test_naca4_normalized():
    xy, le = normalize(naca4("2412"))
    assert xy[le] == pytest.approx([0, 0], abs=1e-9)
    assert xy[0, 0] == pytest.approx(1.0, abs=1e-3)
    assert xy[: le + 1, 1].mean() > xy[le:, 1].mean()  # 윗면 먼저 (Selig)
    assert xy[:, 1].max() - xy[:, 1].min() == pytest.approx(0.12, abs=0.01)


def test_read_dat_file(tmp_path):
    xy = naca4("0012", n=60)
    lines = ["NACA 0012 test"] + [f"{x:.6f} {y:.6f}" for x, y in xy]
    (tmp_path / "foil.dat").write_text("\n".join(lines))
    geom = build_geometry({"main_airfoil": "foil.dat"}, tmp_path)
    assert len(geom.elements) == 1
    assert geom.elements[0].xy[:, 0].max() == pytest.approx(1.0, abs=1e-3)


@pytest.mark.parametrize("gap", [0.01, 0.02, 0.035])
def test_flap_gap_is_exact(gap):
    geom = build_geometry({**FLAP, "flap1_gap": gap}, Path("."))
    main, flap = geom.elements
    assert signed_distance(main.te_lower, flap.xy)[0] == pytest.approx(gap, abs=1e-6)
    assert geom.info["min_clearance"] > 0
    # overlap: 메인익 뒷전 x - 플랩 앞쪽 끝 x
    assert main.te_lower[0] - flap.xy[:, 0].min() == pytest.approx(0.01, abs=1e-9)


def test_impossible_gap_rejected():
    with pytest.raises(ConfigError):
        build_geometry({**FLAP, "flap1_overlap": -0.05, "flap1_gap": 0.01}, Path("."))


def test_fluent_msh_orientation(tmp_path):
    # 2x1 사각형 2개 + 삼각형 섞인 작은 메쉬 (일부러 시계방향 셀 포함)
    nodes = np.array([[0, 0], [1, 0], [2, 0], [0, 1], [1, 1], [2, 1], [3, 0.5]], float)
    cells = [(0, 1, 4, 3), (1, 2, 5, 4)[::-1], (2, 6, 5)]
    edges = [(0, 1), (1, 2), (2, 6), (6, 5), (5, 4), (4, 3), (3, 0)]
    stats = write_msh(tmp_path / "t.msh", nodes, cells, [Boundary("wall", "wall", edges)])
    assert stats["zones"] == {"interior-fluid": 2, "wall": 7}
    check = check_msh(tmp_path / "t.msh")
    assert check["bad_cells"] == 0 and check["open_cells"] == 0
    assert check["total_area"] == pytest.approx(2.5)
    # Fluent 규칙: n0→n1 진행 방향 기준 c0 셀은 왼쪽, c1 셀은 오른쪽, 경계면은 c1 = 0
    data = read_msh(tmp_path / "t.msh")
    centroid = {i: nodes[list(c)].mean(0) for i, c in enumerate(cells, start=1)}
    for n0, n1, c0, c1, _ in data["faces"]:
        a, b = data["nodes"][n0 - 1], data["nodes"][n1 - 1]
        def side(c):  # +: 왼쪽
            d, v = b - a, centroid[c] - a
            return d[0] * v[1] - d[1] * v[0]

        assert c0 != 0 and side(c0) > 0
        if c1:
            assert side(c1) < 0


def test_unassigned_boundary_rejected(tmp_path):
    nodes = np.array([[0, 0], [1, 0], [0, 1]], float)
    with pytest.raises(ValueError, match="경계"):
        write_msh(tmp_path / "t.msh", nodes, [(0, 1, 2)], [Boundary("wall", "wall", [(0, 1)])])


def make_flap_project(tmp_path, gaps="[0.015, 0.02, 0.03]"):
    (tmp_path / "journal.jou").write_text(
        "AOA=${aoa} GAP=${flap1_gap} MESH=${mesh_file}\n/solve/iterate ${iterations}\n", encoding="utf-8")
    (tmp_path / "project.toml").write_text(textwrap.dedent(f"""
        [project]
        template = "journal.jou"
        max_parallel = 2

        [solver]
        kind = "custom"
        command = ["${{python}}", "{FAKE_SOLVER.as_posix()}", "${{journal}}"]

        [constants]
        main_airfoil = "2412"
        flap1_airfoil = "4412"
        flap1_chord = 0.3
        flap1_deflection = 25
        flap1_overlap = 0.01
        iterations = 10

        [cases]
        name = "g{{flap1_gap}}_a{{aoa}}"
        group = "g{{flap1_gap}}"

        [cases.sweep]
        flap1_gap = {gaps}
        aoa = [0, 5, 10]

        [mesh]   # 테스트용 거친 메쉬
        reynolds = 1e5
        surface_cells = 60
        flap_surface_cells = 40
        farfield_radius = 10
        farfield_size = 1.0
        near_size = 0.03
        wake_size = 0.05
        wake_length = 1.0
    """), encoding="utf-8")
    return tmp_path / "project.toml"


def test_flap_pipeline_end_to_end(tmp_path, capsys):
    cfg_path = make_flap_project(tmp_path)
    cfg = load_config(cfg_path)
    cases = build_cases(cfg)
    assert len(cases) == 9
    assert len({c.mesh.name for c in cases}) == 3  # 받음각만 다른 케이스는 메쉬 공유

    assert main(["-c", str(cfg_path), "run"]) == 0
    out = capsys.readouterr().out
    assert "형상 3개 중 3개 생성 필요" in out

    mesh_dir = cases[0].mesh.dir
    for f in ("mesh.msh", "mesh_info.json", "geometry.png", "mesh.png", "mesh_slot1.png"):
        assert (mesh_dir / f).is_file(), f
    info = json.loads((mesh_dir / "mesh_info.json").read_text(encoding="utf-8"))
    assert set(info["zones"]) == {"interior-fluid", "main", "flap1", "farfield"}
    assert info["min_quality"] > 0.05
    check = check_msh(mesh_dir / "mesh.msh")
    assert check["bad_cells"] == 0 and check["open_cells"] == 0
    assert check["names"][4] == ("wall", "main")

    with open(cfg.output_dir / "summary.csv", encoding="utf-8-sig") as f:
        rows = {r["case"]: r for r in csv.DictReader(f)}
    r = rows["g0.03_a10"]
    assert float(r["cl"]) == pytest.approx(1.0 + 1.0 - 50 * 0.01**2, rel=1e-6)  # cx,cy → cl 회전
    assert float(r["cd"]) == pytest.approx(0.03, rel=1e-6)
    assert float(r["l/d"]) == pytest.approx(float(r["cl"]) / float(r["cd"]))

    with open(cfg.output_dir / "ranking.csv", encoding="utf-8-sig") as f:
        ranking = list(csv.DictReader(f))
    assert [row["flap1_gap"] for row in ranking][0] == "0.02"  # 가상 해석에서 갭 0.02가 최적
    assert ranking[0]["rank"] == "1"
    for f in ("cl_alpha.png", "cd_alpha.png", "ld_alpha.png", "polar.png"):
        assert (cfg.output_dir / "plots" / f).is_file()
    assert (cases[0].dir / "convergence.png").is_file()

    # 다시 실행하면 메쉬도 케이스도 건너뜀
    assert main(["-c", str(cfg_path), "run"]) == 0
    out = capsys.readouterr().out
    assert "형상 3개 중 0개" in out or "메쉬:" not in out


def test_mesh_failure_marks_cases_failed(tmp_path):
    cfg_path = make_flap_project(tmp_path, gaps="[0.02]")
    text = cfg_path.read_text().replace("flap1_overlap = 0.01", "flap1_overlap = -0.2")
    cfg_path.write_text(text)
    assert main(["-c", str(cfg_path), "run"]) == 1
    cfg = load_config(cfg_path)
    status = json.loads((build_cases(cfg)[0].dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed" and "메쉬 실패" in status["message"]
    assert "gap" in status["message"]  # 실제 실패 사유가 남아야 함
