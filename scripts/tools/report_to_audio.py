#!/usr/bin/env python
"""研报朗读工具——将 markdown 研报转为 MP3 音频。

使用微软 Edge TTS（免费、Azure 级中文语音质量）。
默认女声 zh-CN-XiaoxiaoNeural（晓晓），语速 +10%。

Usage:
    .venv/bin/python scripts/report_to_audio.py <markdown_file> [--voice NAME] [--rate N] [--output FILE]

Examples:
    # 基本用法（默认女声晓晓，语速+10%）
    python scripts/report_to_audio.py "docs/个股研报/半导体电子/昊华科技深度研报.md"

    # 男声云希，语速 +20%
    python scripts/report_to_audio.py report.md --voice zh-CN-YunxiNeural --rate +20%

    # 指定输出文件
    python scripts/report_to_audio.py report.md --output report.mp3

Voices (中文):
    zh-CN-XiaoxiaoNeural  女声-晓晓（默认，自然亲切）
    zh-CN-YunxiNeural     男声-云希（沉稳专业）
    zh-CN-XiaoyiNeural    女声-晓伊（温柔）
    zh-CN-YunjianNeural   男声-云健（运动感）
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import edge_tts

# 默认配置
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"  # 晓晓
DEFAULT_RATE = "+10%"  # 语速加快 10%
# 跳过这些段落（非内容部分）
SKIP_SECTIONS = {
    "免责声明",
    "版本历史",
    "附录",
    "数据来源索引",
    "Source of Truth",
    "Ultraworked with",
    "Co-authored-by",
}


def clean_markdown(md_text: str) -> str:
    """将 markdown 清理为适合 TTS 朗读的纯文本。

    - 移除 YAML frontmatter
    - 移除代码块（```）
    - 移除 markdown 表格（转为简要文字）
    - 移除图片/链接 URL
    - 移除 HTML 注释
    - 跳过非内容段落（免责声明/版本历史等）
    - 移除 markdown 格式标记（**、`、# 等）
    """
    lines = md_text.split("\n")
    result = []
    skip_until_next_section = False

    # 移除 frontmatter
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                lines = lines[i + 1 :]
                break

    in_code_block = False

    for line in lines:
        stripped = line.strip()

        # 代码块跳过
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # HTML 注释跳过
        if stripped.startswith("<!--") or stripped.endswith("-->"):
            continue

        # 检测需要跳过的段落
        if stripped.startswith("#"):
            section_title = stripped.lstrip("#").strip()
            # 检查是否是需要跳过的段落
            should_skip = any(kw in section_title for kw in SKIP_SECTIONS)
            if should_skip:
                skip_until_next_section = True
                continue
            else:
                skip_until_next_section = False

        if skip_until_next_section:
            continue

        # 表格处理：跳过表格分隔行，简化表格内容
        if stripped.startswith("|") and stripped.endswith("|"):
            # 跳过纯分隔行 |---|---|
            if re.match(r"^\|[\s\-:]+\|", stripped):
                continue
            # 表格内容行：提取单元格文字
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            # 简化为"列名：值" 的朗读方式
            cleaned_cells = [re.sub(r"\*\*|`", "", c) for c in cells if c]
            if cleaned_cells:
                result.append("，".join(cleaned_cells))
            continue

        # 移除图片 ![alt](url)
        line = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", r"\1", line)
        # 移除链接 [text](url) → text
        line = re.sub(r"\[([^\]]*)\]\([^\)]+\)", r"\1", line)
        # 移除加粗/斜体/代码标记
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"\*(.+?)\*", r"\1", line)
        line = re.sub(r"`(.+?)`", r"\1", line)
        # 移除标题标记 #
        line = re.sub(r"^#+\s*", "", line)
        # 移除引用标记 >
        line = re.sub(r"^>\s*", "", line)
        # 移除列表标记 -
        line = re.sub(r"^\s*[-*]\s+", "", line)

        # 移除纯 URL 行
        if re.match(r"^https?://", stripped):
            continue

        # 移除 emoji（TTS 会朗读 emoji 描述，很奇怪）
        line = re.sub(
            r"[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001F600-\U0001F64F]",
            "",
            line,
        )

        result.append(line)

    # 合并，确保段落间有空行
    text = "\n".join(result)
    # 压缩多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def text_to_speech(text: str, output_path: str, voice: str, rate: str) -> None:
    """将文本转为 MP3 音频。"""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="将 markdown 研报转为 MP3 音频")
    parser.add_argument("input", help="Markdown 研报文件路径")
    parser.add_argument(
        "--voice",
        default=DEFAULT_VOICE,
        help=f"语音角色（默认：{DEFAULT_VOICE}）",
    )
    parser.add_argument(
        "--rate",
        default=DEFAULT_RATE,
        help=f"语速（默认：{DEFAULT_RATE}，如 +20%/-10%）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出 MP3 路径（默认：--mirror 模式下输出到 docs_audio/ 镜像目录）",
    )
    parser.add_argument(
        "--mirror",
        action="store_true",
        default=True,
        help="镜像模式：docs/xxx/yyy.md → docs_audio/xxx/yyy.mp3（默认开启）",
    )
    parser.add_argument(
        "--no-mirror",
        action="store_false",
        dest="mirror",
        help="关闭镜像模式，输出到输入文件同目录",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误：文件不存在 {input_path}")
        return 1

    # 输出路径：优先 --output，其次镜像模式，最后同目录
    if args.output:
        output_path = args.output
    elif args.mirror:
        # 镜像模式：docs/ → docs_audio/，保持子目录结构
        input_str = str(input_path)
        if input_str.startswith("docs/"):
            output_path = input_str.replace("docs/", "docs_audio/", 1).replace(".md", ".mp3")
        elif "/docs/" in input_str:
            # 绝对路径含 /docs/
            output_path = input_str.replace("/docs/", "/docs_audio/", 1).replace(".md", ".mp3")
        else:
            # 不在 docs/ 下，退回到同目录
            output_path = str(input_path.with_suffix(".mp3"))
    else:
        output_path = str(input_path.with_suffix(".mp3"))

    # 创建输出目录
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 读取并清理 markdown
    md_text = input_path.read_text(encoding="utf-8")
    clean_text = clean_markdown(md_text)

    # 统计
    char_count = len(clean_text)
    est_minutes = char_count / 300  # 中文 TTS 约 300 字/分钟
    print(f"输入：{input_path}")
    print(f"清理后文本：{char_count} 字，预计朗读 ~{est_minutes:.0f} 分钟")
    print(f"语音：{args.voice}，语速：{args.rate}")
    print(f"输出：{output_path}")
    print("生成中...")

    # TTS
    asyncio.run(text_to_speech(clean_text, output_path, args.voice, args.rate))

    # 结果
    file_size = Path(output_path).stat().st_size / 1024 / 1024
    print(f"\n✅ 完成！文件大小：{file_size:.1f} MB")
    print(f"   {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
