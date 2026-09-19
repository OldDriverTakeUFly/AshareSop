# davis_analyzer/tests/test_recap_vision_qc.py
"""recap 视觉质检:ask_vision 包装/结论汇总(mock 模型)。"""
from __future__ import annotations

from pathlib import Path

from davis_analyzer.systems.recap import vision_qc


def test_qc_card_parses_verdict(tmp_path, monkeypatch):
    png = tmp_path / "a.png"
    png.write_bytes(b"x")
    monkeypatch.setattr(vision_qc, "_ask_vision",
                        lambda img, prompt: {"pass": True, "issues": []})
    out = vision_qc.qc_card(png)
    assert out["pass"] is True and out["issues"] == []


def test_qc_card_fail_issues(tmp_path, monkeypatch):
    png = tmp_path / "a.png"
    png.write_bytes(b"x")
    monkeypatch.setattr(vision_qc, "_ask_vision",
                        lambda img, prompt: {"pass": False, "issues": ["文字被裁切"]})
    out = vision_qc.qc_card(png)
    assert out["pass"] is False and "文字被裁切" in out["issues"]


def test_qc_dir_aggregates(tmp_path, monkeypatch):
    for n in ("a.png", "b.png"):
        (tmp_path / n).write_bytes(b"x")
    verdicts = {"a.png": {"pass": True, "issues": []},
                "b.png": {"pass": False, "issues": ["数字模糊"]}}
    monkeypatch.setattr(vision_qc, "_ask_vision",
                        lambda img, prompt: verdicts[Path(img).name])
    rep = vision_qc.qc_dir(tmp_path)
    assert rep["pass"] is False
    assert rep["frames"][0]["pass"] is True and rep["frames"][1]["pass"] is False


def test_qc_card_bad_payload_defaults_fail(tmp_path, monkeypatch):
    png = tmp_path / "a.png"
    png.write_bytes(b"x")
    monkeypatch.setattr(vision_qc, "_ask_vision", lambda img, prompt: {"unexpected": 1})
    out = vision_qc.qc_card(png)
    assert out["pass"] is False and out["issues"]


def test_qc_empty_dir(tmp_path):
    rep = vision_qc.qc_dir(tmp_path)
    assert rep["pass"] is False and rep["frames"] == []
