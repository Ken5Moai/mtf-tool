"""Streamlit の各ページが例外なく描画され、記録・削除ができることの確認。"""
import json

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

VIEWS = ["views/calendar_view.py", "views/journal_view.py", "views/analysis_view.py"]

SAMPLE = {
    "config": {"usdjpy": 150.0},
    "trades": [
        {"id": "t00001", "date": "2026-08-20", "pnl": 25.0, "symbol": "BTC/USD", "side": "BUY",
         "entry": 64000, "exit": 64500, "sl": 63800, "memo": "押し目", "tags": ["押し目"]},
        {"id": "t00002", "date": "2026-08-21", "pnl": -12.0, "symbol": "USD/JPY", "side": "SELL",
         "fee": 0.3, "memo": "早仕掛け", "tags": ["逆張り"]},
    ],
}


def run(view: str | None = None, **state) -> AppTest:
    """app.py を起点に、指定のページを開いた状態まで進める。"""
    at = AppTest.from_file("app.py", default_timeout=60)
    at.run()
    if view:
        at.switch_page(view)
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


@pytest.fixture
def data_file(tmp_path, monkeypatch):
    path = tmp_path / "trades.json"
    path.write_text(json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("store.DATA_PATH", path)
    import ui
    ui._handle.clear()
    yield path
    ui._handle.clear()


def saved(path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["trades"]


def button(at: AppTest, label: str):
    for b in at.button:
        if b.label == label:
            return b
    raise AssertionError(f"ボタンが見つかりません: {label}")


# ------------------------------------------------------------------ render
@pytest.mark.parametrize("view", VIEWS)
def test_view_renders(view, data_file):
    run(view)


@pytest.mark.parametrize("view", VIEWS)
def test_view_renders_with_no_data(view, tmp_path, monkeypatch):
    monkeypatch.setattr("store.DATA_PATH", tmp_path / "empty.json")
    import ui
    ui._handle.clear()
    run(view)
    ui._handle.clear()


def test_default_page_is_the_calendar(data_file):
    at = run()
    assert "2026年" in at.title[0].value or "月" in at.title[0].value


# ---------------------------------------------------------------- calendar
def test_clicking_a_day_selects_it(data_file):
    at = run("views/calendar_view.py", ym=(2026, 8))
    at.button(key="d2026-08-20").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["sel"] == "2026-08-20"


def test_month_navigation(data_file):
    at = run("views/calendar_view.py", ym=(2026, 1))
    button(at, "◀ 前月").click().run()
    assert at.session_state["ym"] == (2025, 12)

    button(at, "翌月 ▶").click().run()
    button(at, "翌月 ▶").click().run()
    assert at.session_state["ym"] == (2026, 2)
    assert not at.exception, [e.value for e in at.exception]


def test_form_records_a_trade_on_the_selected_day(data_file):
    at = run("views/calendar_view.py", ym=(2026, 8), sel="2026-08-19")
    next(n for n in at.number_input if n.label == "損益 ($)").set_value(40.0)
    next(t for t in at.text_input if t.label == "メモ").set_value("テスト記録")
    button(at, "この日に記録する").click().run()

    assert not at.exception, [e.value for e in at.exception]
    rec = [t for t in saved(data_file) if t["date"] == "2026-08-19"]
    assert len(rec) == 1 and rec[0]["pnl"] == 40.0 and rec[0]["memo"] == "テスト記録"


def test_deleting_a_trade(data_file):
    at = run("views/calendar_view.py", ym=(2026, 8), sel="2026-08-20")
    at.selectbox(key="del").set_value("t00001").run()
    button(at, "削除").click().run()

    assert not at.exception, [e.value for e in at.exception]
    assert [t["id"] for t in saved(data_file)] == ["t00002"]


def test_config_sidebar_persists(data_file):
    at = run()
    next(n for n in at.sidebar.number_input if n.label == "USD/JPY").set_value(140.0).run()
    button(at, "設定を保存").click().run()

    assert not at.exception, [e.value for e in at.exception]
    cfg = json.loads(data_file.read_text(encoding="utf-8"))["config"]
    assert cfg["usdjpy"] == 140.0
