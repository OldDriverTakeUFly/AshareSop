# davis_analyzer/recap/vision_qc.py
"""recap 视觉质检闸:数据卡/成片帧过 vision.py(结构化 JSON,主模型只消费结论)。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from loguru import logger

from davis_analyzer.systems.recap.constants import REPO_ROOT

_QC_PROMPT = (
    "这是A股复盘短视频里的一张数据卡截图。请只检查以下问题并返回JSON"
    '(不要多余文字):{"pass": true/false, "issues": ["问题描述", ...]}。'
    "检查项:①文字是否清晰可读(无模糊/锯齿);②是否有文字溢出卡片边界或被裁切;"
    "③排版是否有元素重叠遮挡;④数字是否完整显示(无截断);⑤配色对比度是否足以看清。"
    "没有问题则 pass=true、issues 为空数组。"
)


def _load_vision():
    """按路径加载 scripts/content_publisher/vision.py(只读复用,不 import 进包)。"""
    mod_path = REPO_ROOT / "scripts" / "content_publisher" / "vision.py"
    spec = importlib.util.spec_from_file_location("recap_vision_shim", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ask_vision(image_path: Path, prompt: str) -> dict:
    return _load_vision().ask_vision(image_path, prompt)


def qc_card(png: Path) -> dict:
    try:
        verdict = _ask_vision(png, _QC_PROMPT)
    except Exception as e:  # noqa: BLE001 —— 模型不可用按 fail 处置,报告人工
        logger.warning(f"视觉质检调用失败 {png.name}: {e!r}")
        return {"pass": False, "issues": [f"质检调用异常: {e!r}"]}
    if not isinstance(verdict, dict) or "pass" not in verdict:
        return {"pass": False, "issues": [f"质检返回结构异常: {verdict!r}"]}
    return {"pass": bool(verdict["pass"]),
            "issues": [str(i) for i in verdict.get("issues", [])]}


def qc_dir(card_dir: Path) -> dict:
    frames = [{"file": p.name, **qc_card(p)} for p in sorted(card_dir.glob("*.png"))]
    return {"pass": bool(frames) and all(f["pass"] for f in frames), "frames": frames}
