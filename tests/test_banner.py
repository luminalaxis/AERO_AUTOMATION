import io

from aero_automation.banner import build_banner, print_banner, render_text
from aero_automation.cli import main


def test_render_text_has_six_rows():
    rows = render_text("AERO-AUTO")
    assert len(rows) == 6
    assert "█" in rows[0]


def test_banner_variants():
    full = build_banner(color=False, width=120)
    assert "moving ground" in full and "\033[" not in full
    assert "\033[" in build_banner(color=True, width=120)
    assert len(build_banner(color=False, width=40).splitlines()) == 1   # 좁은 터미널
    assert build_banner(unicode=False).isascii()                         # cp949 등


def test_banner_skipped_when_not_tty(monkeypatch):
    monkeypatch.delenv("AERO_AUTO_NO_BANNER", raising=False)
    buf = io.StringIO()
    assert print_banner(buf) is False and buf.getvalue() == ""
    assert print_banner(buf, force=True) is True and buf.getvalue()


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: aero-auto" in capsys.readouterr().out
