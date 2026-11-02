import csv
import functools
import http.server
import threading
from pathlib import Path

import pytest

import aero_automation.airfoils as af
from aero_automation.config import ConfigError
from aero_automation.geometry import build_geometry, naca4


def _selig(code, title):
    return title + "\n" + "\n".join(f" {x:.6f}  {y:.6f}" for x, y in naca4(code, n=60)) + "\n"


def _lednicer(code, title):
    xy = naca4(code, n=40)
    upper, lower = xy[:40][::-1], xy[39:]
    lines = [title, f"{len(upper)}. {len(lower)}.", ""]
    lines += [f"{x:.6f} {y:.6f}" for x, y in upper] + [""] + [f"{x:.6f} {y:.6f}" for x, y in lower]
    return "\n".join(lines) + "\n"


FILES = {
    "naca0012.dat": _selig("0012", "NACA 0012"),
    "naca23012.dat": _lednicer("2312", "NACA 23012 (Lednicer)"),
    "s1223.dat": _selig("6412", "S1223"),
    "s1223rtl.dat": _selig("6410", "S1223 RTL"),
    "sd7037.dat": _selig("2410", "SD7037"),
    "e423.dat": _selig("5412", "E423"),
    "e387.dat": _selig("2409", "E387"),
    "broken.dat": "not an airfoil\n1 2\n",
}


@pytest.fixture
def server(tmp_path):
    """UIUC와 같은 구조의 로컬 서버: coord_seligFmt/ (디렉터리 목록) + coord_database.html."""
    site = tmp_path / "site"
    (site / "ads" / "coord_seligFmt").mkdir(parents=True)
    (site / "ads" / "coord").mkdir(parents=True)
    for name, text in FILES.items():
        (site / "ads" / "coord_seligFmt" / name).write_text(text)
        (site / "ads" / "coord" / name).write_text(text)
    links = "".join(f'<a href="coord/{n}">{n}</a><br>\n' for n in FILES)
    (site / "ads" / "coord_database.html").write_text(f"<html><body>{links}</body></html>")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/ads/"
    yield base
    httpd.shutdown()


def test_list_and_select(server, monkeypatch):
    remote = af.list_remote([server + "coord_seligFmt/"])
    assert remote["s1223"].endswith("/ads/coord_seligFmt/s1223.dat")
    assert af.select(remote, ["s"]) == ["s1223", "s1223rtl"]  # SD 시리즈 제외
    assert af.select(remote, ["e"]) == ["e387", "e423"]
    assert af.select(remote, ["naca"]) == ["naca0012", "naca23012"]
    assert af.select(remote, ["sd.*"]) == ["sd7037"]


def test_fallback_source(server):
    remote = af.list_remote([server + "missing/", server + "coord_database.html"])
    assert remote["e423"].endswith("/ads/coord/e423.dat")


def test_fetch_series(server, tmp_path):
    out = tmp_path / "airfoils"
    rows = af.fetch_series(["naca", "s", "e"], out, sources=[server + "coord_seligFmt/"], log=lambda *_: None)
    assert {r["name"] for r in rows} == {"naca0012", "naca23012", "s1223", "s1223rtl", "e387", "e423"}
    assert all(r["status"] == "ok" for r in rows)
    with open(out / "index.csv", encoding="utf-8-sig") as f:
        index = {r["name"]: r for r in csv.DictReader(f)}
    assert float(index["naca0012"]["thickness"]) == pytest.approx(0.12, abs=0.005)
    assert float(index["naca23012"]["thickness"]) == pytest.approx(0.12, abs=0.005)  # Lednicer 형식
    # 다시 실행하면 새로 받지 않음
    rows2 = af.fetch_series(["s"], out, sources=[server + "coord_seligFmt/"], log=lambda *_: None)
    assert not any(r.get("downloaded") for r in rows2)
    assert af.plot_previews(out)


def test_bad_file_flagged(server, tmp_path):
    rows = af.fetch_series([r"broken"], tmp_path / "a", sources=[server + "coord_seligFmt/"], log=lambda *_: None)
    assert rows[0]["status"] == "bad"


def test_resolve_downloads_on_demand(server, tmp_path, monkeypatch):
    monkeypatch.setattr(af, "UIUC_SOURCES", [server + "coord_seligFmt/"])
    geom = build_geometry({"main_airfoil": "e423", "flap1_airfoil": "s1223", "flap1_chord": 0.3,
                           "flap1_gap": 0.02, "flap1_overlap": 0.01, "flap1_deflection": 20}, tmp_path)
    assert (tmp_path / "airfoils" / "e423.dat").is_file()
    assert (tmp_path / "airfoils" / "s1223.dat").is_file()
    assert len(geom.elements) == 2
    with pytest.raises(ConfigError, match="없습니다"):
        af.resolve("zz9999", tmp_path)
    with pytest.raises(ConfigError, match="찾을 수 없습니다"):
        af.resolve("airfoils/nothere.dat", tmp_path)
