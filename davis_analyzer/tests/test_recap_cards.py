# davis_analyzer/tests/test_recap_cards.py
"""recap 数据卡:html 生成(数字=display 原样)/渲染落盘(playwright 真跑)。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from davis_analyzer.recap import card_renderer as cr


def _ep_dir(tmp_path):
    day = "2026-09-18"
    d = tmp_path / "episodes" / day
    d.mkdir(parents=True, exist_ok=True)
    (d / "episode.json").write_text(json.dumps({
        "trade_date": day, "title": "五连板之夜",
        "facts": [
            {"id": "idx_sh_close", "value": "3875", "unit": "点", "display": "上证指数3876点",
             "as_of": day, "source": {"kind": "stockhot", "ref": "r"}},
            {"id": "idx_sh_chg", "value": "-0.411", "unit": "%", "display": "上证指数-0.41%",
             "as_of": day, "source": {"kind": "stockhot", "ref": "r"}},
            {"id": "breadth_up", "value": "3200", "unit": "家", "display": "上涨3200家",
             "as_of": day, "source": {"kind": "stockhot", "ref": "r"}},
        ],
        "segments": [{"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH", "lines": []}],
    }, ensure_ascii=False), "utf-8")
    (d / "candidates.json").write_text(json.dumps([{
        "ts_code": "605577.SH", "name": "龙版传媒", "sector": "出版", "drama_score": 98.0,
        "events": [], "replay_start": "09:27:00", "replay_end": "14:51:00",
        "notes": ["5连板", "3度炸板后回封"], "educational": True,
        "facts": [{"id": "605577.SH_boards", "value": "5", "unit": "板", "display": "5连板",
                   "as_of": day, "source": {"kind": "stockhot", "ref": "r"}}],
    }], ensure_ascii=False), "utf-8")
    return tmp_path


def test_scoreboard_html_uses_display_verbatim(tmp_path):
    ep = json.loads((_ep_dir(tmp_path) / "episodes" / "2026-09-18" / "episode.json").read_text("utf-8"))
    html = cr.scoreboard_html(ep)
    assert "上证指数3876点" in html          # display 原样,不重排数字
    assert "上涨3200家" in html
    assert "1080" in html                    # 竖屏宽度声明


def test_stock_card_html(tmp_path):
    _ep_dir(tmp_path)
    cand = json.loads((_ep_dir(tmp_path) / "episodes" / "2026-09-18" / "candidates.json")
                      .read_text("utf-8"))[0]
    html = cr.stock_card_html(cand)
    assert "龙版传媒" in html and "5连板" in html and "09:27-14:51" in html


@pytest.mark.integration
def test_render_cards_smoke(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    _ep_dir(tmp_path)
    monkeypatch.setattr(cr, "EPISODES_DIR", tmp_path / "episodes")
    pngs = cr.render_cards("2026-09-18")
    assert pngs and all(p.suffix == ".png" and p.stat().st_size > 10_000 for p in pngs)
