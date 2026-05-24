import json
import subprocess
from pathlib import Path

from stockhot.core.config import (
    PROJECT_ROOT,
    XHS_ACTION_TIMEOUT_MS,
    XHS_AUTO_SUBMIT,
    XHS_HEADLESS,
    XHS_LOGIN_TIMEOUT_MS,
    XHS_PUBLISH_URL,
    XHS_STORAGE_STATE_PATH,
)


RUNNER_PATH = PROJECT_ROOT / "stockhot" / "publisher" / "xiaohongshu_playwright_runner.cjs"


def publish_images(images: list[str], caption: str, dry_run: bool = False) -> dict:
    try:
        resolved_images = _resolve_images(images)
    except (ValueError, FileNotFoundError) as exc:
        return {
            "platform": "xiaohongshu",
            "status": "failed",
            "error": str(exc),
            "images_count": 0,
        }

    title = _build_title(caption)

    if dry_run:
        return {
            "platform": "xiaohongshu",
            "status": "dry_run",
            "images_count": len(resolved_images),
            "caption_length": len(caption),
            "title": title,
            "storage_state_path": str(XHS_STORAGE_STATE_PATH),
        }

    payload = {
        "images": [str(path) for path in resolved_images],
        "title": title,
        "caption": caption,
        "publishUrl": XHS_PUBLISH_URL,
        "storageStatePath": str(XHS_STORAGE_STATE_PATH),
        "headless": XHS_HEADLESS,
        "autoSubmit": XHS_AUTO_SUBMIT,
        "actionTimeoutMs": XHS_ACTION_TIMEOUT_MS,
        "loginTimeoutMs": XHS_LOGIN_TIMEOUT_MS,
    }

    try:
        result = subprocess.run(
            ["node", str(RUNNER_PATH)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            check=False,
        )
    except OSError as exc:
        return {
            "platform": "xiaohongshu",
            "status": "failed",
            "error": str(exc),
            "images_count": len(resolved_images),
        }

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        return {
            "platform": "xiaohongshu",
            "status": "failed",
            "error": detail,
            "images_count": len(resolved_images),
        }

    try:
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        data = json.loads(lines[-1] if lines else "")
    except json.JSONDecodeError:
        return {
            "platform": "xiaohongshu",
            "status": "failed",
            "error": result.stdout.strip() or "invalid runner output",
            "images_count": len(resolved_images),
        }

    if not isinstance(data, dict):
        return {
            "platform": "xiaohongshu",
            "status": "failed",
            "error": "invalid runner output shape",
            "images_count": len(resolved_images),
        }

    status = data.get("status")
    valid_statuses = {
        "authenticated",
        "login_required",
        "login_timeout",
        "draft_ready",
        "submitted",
        "failed",
    }
    if not isinstance(status, str) or status not in valid_statuses:
        return {
            "platform": "xiaohongshu",
            "status": "failed",
            "error": "invalid runner status",
            "images_count": len(resolved_images),
        }

    if status == "submitted":
        confirmed = data.get("confirmed")
        confirmation_signal = data.get("confirmationSignal")
        confirmation_text = data.get("confirmationText")
        if not isinstance(confirmed, bool):
            return {
                "platform": "xiaohongshu",
                "status": "failed",
                "error": "invalid submitted confirmation payload",
                "images_count": len(resolved_images),
            }
        if not confirmed and confirmation_signal not in (None, ""):
            return {
                "platform": "xiaohongshu",
                "status": "failed",
                "error": "incoherent confirmation payload",
                "images_count": len(resolved_images),
            }
        if not confirmed and confirmation_text not in (None, ""):
            return {
                "platform": "xiaohongshu",
                "status": "failed",
                "error": "incoherent confirmation payload",
                "images_count": len(resolved_images),
            }
        if confirmed and (
            not isinstance(confirmation_signal, str) or not confirmation_signal.strip()
        ):
            return {
                "platform": "xiaohongshu",
                "status": "failed",
                "error": "missing confirmation signal",
                "images_count": len(resolved_images),
            }
        if confirmed and (not isinstance(confirmation_text, str) or not confirmation_text.strip()):
            return {
                "platform": "xiaohongshu",
                "status": "failed",
                "error": "missing confirmation text",
                "images_count": len(resolved_images),
            }
        if confirmation_text not in (None, "") and not isinstance(confirmation_text, str):
            return {
                "platform": "xiaohongshu",
                "status": "failed",
                "error": "invalid confirmation text",
                "images_count": len(resolved_images),
            }

    data.setdefault("platform", "xiaohongshu")
    data.setdefault("images_count", len(resolved_images))
    data.setdefault("caption_length", len(caption))
    return data


def _resolve_images(images: list[str]) -> list[Path]:
    if not images:
        raise ValueError("没有可发布的图片")

    resolved = []
    missing = []
    for image in images:
        path = Path(image).expanduser().resolve()
        if path.exists() and path.is_file():
            resolved.append(path)
        else:
            missing.append(str(path))

    if missing:
        raise FileNotFoundError(f"图片不存在: {', '.join(missing)}")

    return resolved


def _build_title(caption: str) -> str:
    lines = [line.strip() for line in caption.splitlines() if line.strip()]
    if not lines:
        return "小红书自动发布"
    return lines[0][:20]
