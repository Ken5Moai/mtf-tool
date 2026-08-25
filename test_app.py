"""Streamlit の各ページが例外なく描画されることを確認するスモークテスト。"""
import json

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = ["app.py", "pages/1_日報.py", "pages/2_分析.py"]

SAMPLE = {
    "config": {"usdjpy": 150.0},
    "trades": [
        {"id": "t00001", "date": "2026-08-20", "pnl": 25.0, "symbol": "BTC/USD", "side": "BUY",
         "entry": 64000, "exit": 64500, "sl": 63800, "memo": "押し目", "tags": ["押し目"]},
        {"id": "t00002", "date": "2026-08-21", "pnl": -12.0, "symbol": "USD/JPY", "side": "SELL",
         "fee": 0.3, "memo": "早仕掛け", "tags": ["逆張り"]},
    ],
}


@pytest.fixture
def data_file(tmp_path, monkeypatch):
    path = tmp_path / "trades.json"
    path.write_text(json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("store.DATA_PATH", path)
    import ui
    ui._handle.clear()
    yield path
    ui._handle.clear()


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(page, data_file):
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_empty_data(page, tmp_path, monkeypatch):
    monkeypatch.setattr("store.DATA_PATH", tmp_path / "empty.json")
    import ui
    ui._handle.clear()
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    ui._handle.clear()


def test_quick_input_records_a_trade(data_file):
    at = AppTest.from_file("app.py", default_timeout=60).run()
    at.text_input(key="quick").set_value("BTC/USD 買い +40ドル メモ：テスト記録").run()
    at.button(key="quick_go").click().run()

    assert not at.exception, [e.value for e in at.exception]
    saved = json.loads(data_file.read_text(encoding="utf-8"))["trades"]
    assert any(t["pnl"] == 40.0 and t["memo"] == "テスト記録" for t in saved)


def test_quick_input_rejects_unparsable_text(data_file):
    at = AppTest.from_file("app.py", default_timeout=60).run()
    at.text_input(key="quick").set_value("今日はノートレード").run()
    at.button(key="quick_go").click().run()

    assert not at.exception
    assert at.error, "パースできない入力にはエラーを表示する"
    assert len(json.loads(data_file.read_text(encoding="utf-8"))["trades"]) == 2
