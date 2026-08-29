#!/usr/bin/env python3
# content_publisher/vision.py — 视觉任务外包给轻量视觉模型(GLM flash 系)
# 用途:发布按钮定位、卡片目检、失败截图诊断——省主模型上下文,坐标/结论以 JSON 返回。
# 配置(.env,复用现有 glm key):LLM_API_KEY / LLM_BASE_URL / LLM_VISION_MODEL(默认 glm-5.3-flash)
# 用法:
#   库:   ask_vision(image_path, prompt) -> dict(模型输出的 JSON)
#   CLI:  .venv/bin/python scripts/content_publisher/vision.py <image.png> --prompt "..."
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent.parent

_FALLBACK_MODELS = ["glm-5.3-flash", "glm-4.5v-flash"]


def _load_env() -> tuple[str, str, str]:
    """从仓库根 .env 读 (api_key, base_url, model)——不依赖 python-dotenv,最小解析。"""
    env_path = REPO_ROOT / ".env"
    cfg = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    model = os.environ.get("LLM_VISION_MODEL") or cfg.get("LLM_VISION_MODEL") or _FALLBACK_MODELS[0]
    return (os.environ.get("LLM_API_KEY") or cfg.get("LLM_API_KEY", ""),
            (os.environ.get("LLM_BASE_URL") or cfg.get("LLM_BASE_URL", "https://open.bigmodel.cn/api/coding/paas/v4")).rstrip("/"),
            model)


def ask_vision(image_path: Path, prompt: str, timeout_s: int = 60) -> dict:
    """图片+prompt → 模型输出的 JSON dict;失败抛 VisionError(调用方自行兜底)。"""
    import httpx

    api_key, base_url, model = _load_env()
    if not api_key:
        raise RuntimeError(".env 未配置 LLM_API_KEY")
    img = Path(image_path).read_bytes()
    mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    data_url = f"data:{mime};base64,{base64.b64encode(img).decode()}"

    last_err: Exception | None = None
    for candidate in [model] + [m for m in _FALLBACK_MODELS if m != model]:
        body = {
            "model": candidate,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "temperature": 0.1,
        }
        try:
            resp = httpx.post(f"{base_url}/chat/completions", json=body, timeout=timeout_s,
                              headers={"Authorization": f"Bearer {api_key}"})
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                return _extract_json(text)
            last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            if resp.status_code == 400 and "model" in resp.text.lower():
                continue  # 模型名不识别 → 试下一个候选
            raise last_err
        except httpx.HTTPError as e:  # 网络/超时:不换模型直接抛
            raise RuntimeError(f"视觉模型请求失败: {e}") from e
    raise RuntimeError(f"视觉模型全部不可用,最后错误: {last_err}")


def _extract_json(text: str) -> dict:
    """从模型回复中提取第一个完整 JSON 对象(容忍 markdown 代码块与前后闲话,支持嵌套)。"""
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise RuntimeError(f"视觉模型未返回 JSON: {text[:120]}")


def locate_button(image_path: Path, button_text: str) -> tuple[float, float]:
    """在截图中定位按钮中心(按包围盒取心,flash 单点坐标精度 ±40px 不可靠)。

    要求模型返回 {x1,y1,x2,y2} 包围盒;找不到返回全 -1。
    """
    out = ask_vision(
        image_path,
        f"在这张浏览器截图中找到文字为「{button_text}」的红色背景按钮,"
        f"返回其包围盒的像素坐标,严格只输出 JSON:"
        f" {{\"x1\": <int>, \"y1\": <int>, \"x2\": <int>, \"y2\": <int>}}。找不到则全返回 -1。")
    x1, y1 = float(out.get("x1", -1)), float(out.get("y1", -1))
    x2, y2 = float(out.get("x2", -1)), float(out.get("y2", -1))
    if min(x1, y1, x2, y2) < 0 or x2 <= x1 or y2 <= y1:
        raise RuntimeError(f"视觉模型未定位到按钮「{button_text}」: {out}")
    return (x1 + x2) / 2, (y1 + y2) / 2


def main() -> None:
    ap = argparse.ArgumentParser(description="视觉任务外包(GLM flash)")
    ap.add_argument("image", type=Path)
    ap.add_argument("--prompt", default="")
    ap.add_argument("--locate", help="定位按钮文字,直接输出坐标 JSON")
    args = ap.parse_args()
    if not args.prompt and not args.locate:
        ap.error("需要 --prompt 或 --locate 之一")
    if args.locate:
        x, y = locate_button(args.image, args.locate)
        print(json.dumps({"x": x, "y": y}, ensure_ascii=False))
    else:
        print(json.dumps(ask_vision(args.image, args.prompt), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
