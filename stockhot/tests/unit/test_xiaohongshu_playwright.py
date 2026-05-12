import json
import subprocess
from pathlib import Path

import pytest

from stockhot.publisher import run_publish
from stockhot.publisher import xiaohongshu_playwright as xhs


def _fake_image(tmp_path: Path) -> Path:
    image = tmp_path / "sample.png"
    image.write_bytes(b"fake-image")
    return image


def _completed_process(
    stdout: dict, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["node", "runner"],
        returncode=returncode,
        stdout=json.dumps(stdout, ensure_ascii=False),
        stderr=stderr,
    )


@pytest.mark.parametrize(
    "status",
    [
        "authenticated",
        "login_required",
        "login_timeout",
        "draft_ready",
        "failed",
    ],
)
def test_publish_images_passes_through_runner_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: _completed_process(
            {"status": status, "currentUrl": "https://creator.xiaohongshu.com/"}
        ),
    )

    result = xhs.publish_images([str(image)], "标题\n正文")

    assert result["platform"] == "xiaohongshu"
    assert result["status"] == status
    assert result["images_count"] == 1
    assert result["caption_length"] == len("标题\n正文")


def test_publish_images_dry_run_returns_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])

    result = xhs.publish_images([str(image)], "标题\n正文", dry_run=True)

    assert result["status"] == "dry_run"
    assert result["platform"] == "xiaohongshu"
    assert result["images_count"] == 1
    assert result["caption_length"] == len("标题\n正文")
    assert result["title"] == "标题"
    assert "storage_state_path" in result


def test_publish_images_invokes_runner_with_expected_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])

    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _completed_process({"status": "draft_ready"})

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = xhs.publish_images([str(image)], "标题\n正文")

    assert result["status"] == "draft_ready"
    assert captured["args"] == (["node", str(xhs.RUNNER_PATH)],)
    assert captured["kwargs"]["cwd"] == xhs.PROJECT_ROOT
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    payload = json.loads(captured["kwargs"]["input"])
    assert payload["images"] == [str(image)]
    assert payload["title"] == "标题"
    assert payload["caption"] == "标题\n正文"
    assert payload["publishUrl"]
    assert payload["storageStatePath"]
    assert payload["headless"] == xhs.XHS_HEADLESS
    assert payload["autoSubmit"] == xhs.XHS_AUTO_SUBMIT
    assert payload["actionTimeoutMs"] == xhs.XHS_ACTION_TIMEOUT_MS
    assert payload["loginTimeoutMs"] == xhs.XHS_LOGIN_TIMEOUT_MS


def test_publish_images_preserves_confirmation_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: _completed_process(
            {
                "status": "submitted",
                "confirmed": True,
                "confirmationSignal": "success_text",
                "confirmationText": "发布成功，笔记已发布",
                "currentUrl": "https://creator.xiaohongshu.com/creator/home",
            }
        ),
    )

    result = xhs.publish_images([str(image)], "标题\n正文")

    assert result["status"] == "submitted"
    assert result["confirmed"] is True
    assert result["confirmationSignal"] == "success_text"
    assert result["confirmationText"] == "发布成功，笔记已发布"


def test_publish_images_accepts_success_url_confirmation_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: _completed_process(
            {
                "status": "submitted",
                "confirmed": True,
                "confirmationSignal": "success_url",
                "confirmationText": "success-url:https://creator.xiaohongshu.com/publish/success",
                "currentUrl": "https://creator.xiaohongshu.com/publish/success",
            }
        ),
    )

    result = xhs.publish_images([str(image)], "标题\n正文")

    assert result["status"] == "submitted"
    assert result["confirmed"] is True
    assert result["confirmationSignal"] == "success_url"
    assert result["confirmationText"].startswith("success-url:")


def test_publish_images_uses_last_non_empty_json_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["node", "runner"],
            returncode=0,
            stdout='debug line\n\n{"status": "submitted", "confirmed": true, "confirmationSignal": "success_text", "confirmationText": "发布成功"}\n',
            stderr="",
        ),
    )

    result = xhs.publish_images([str(image)], "标题\n正文")

    assert result["status"] == "submitted"


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        ({"status": "submitted"}, "invalid submitted confirmation payload"),
        ({"status": "submitted", "confirmed": "yes"}, "invalid submitted confirmation payload"),
        ({"status": "submitted", "confirmed": True}, "missing confirmation signal"),
        (
            {"status": "submitted", "confirmed": True, "confirmationSignal": "success_text"},
            "missing confirmation text",
        ),
        (
            {"status": "submitted", "confirmed": False, "confirmationSignal": "success_text"},
            "incoherent confirmation payload",
        ),
        (
            {"status": "submitted", "confirmed": False, "confirmationText": "发布成功"},
            "incoherent confirmation payload",
        ),
    ],
)
def test_publish_images_rejects_invalid_submitted_confirmation_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: dict, expected_error: str
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _completed_process(payload))

    result = xhs.publish_images([str(image)], "标题\n正文")

    assert result["status"] == "failed"
    assert result["error"] == expected_error


def test_publish_images_rejects_invalid_confirmation_text_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: _completed_process(
            {
                "status": "submitted",
                "confirmed": True,
                "confirmationSignal": "success_text",
                "confirmationText": {"oops": True},
            }
        ),
    )

    result = xhs.publish_images([str(image)], "标题\n正文")

    assert result["status"] == "failed"
    assert result["error"] == "missing confirmation text"


def test_publish_images_returns_failed_when_subprocess_raises_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])

    def raise_oserror(*args, **kwargs):
        raise OSError("node missing")

    monkeypatch.setattr(subprocess, "run", raise_oserror)

    result = xhs.publish_images([str(image)], "标题\n正文")

    assert result["status"] == "failed"
    assert result["error"] == "node missing"
    assert result["images_count"] == 1


def test_publish_images_returns_failed_when_runner_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: _completed_process({}, returncode=1, stderr="runner exploded"),
    )

    result = xhs.publish_images([str(image)], "标题\n正文")

    assert result["status"] == "failed"
    assert result["error"] == "runner exploded"
    assert result["images_count"] == 1


def test_publish_images_returns_failed_when_runner_output_is_invalid_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["node", "runner"],
            returncode=0,
            stdout="not-json",
            stderr="",
        ),
    )

    result = xhs.publish_images([str(image)], "标题\n正文")

    assert result["status"] == "failed"
    assert result["error"] == "not-json"


@pytest.mark.parametrize("stdout", ["[]", '"ok"', "null"])
def test_publish_images_returns_failed_when_runner_output_shape_is_not_dict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout: str
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["node", "runner"],
            returncode=0,
            stdout=stdout,
            stderr="",
        ),
    )

    result = xhs.publish_images([str(image)], "标题\n正文")

    assert result["status"] == "failed"
    assert result["error"] == "invalid runner output shape"


@pytest.mark.parametrize("payload", [{}, {"status": "weird"}, {"status": None}])
def test_publish_images_returns_failed_when_runner_status_is_missing_or_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: dict
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: _completed_process(payload),
    )

    result = xhs.publish_images([str(image)], "标题\n正文")

    assert result["status"] == "failed"
    assert result["error"] == "invalid runner status"


def test_publish_images_rejects_legacy_success_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image = _fake_image(tmp_path)
    monkeypatch.setattr(xhs, "_resolve_images", lambda images: [image])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: _completed_process({"status": "success"}),
    )

    result = xhs.publish_images([str(image)], "标题\n正文")

    assert result["status"] == "failed"
    assert result["error"] == "invalid runner status"


def test_publish_images_returns_failed_when_images_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        xhs,
        "_resolve_images",
        lambda images: (_ for _ in ()).throw(FileNotFoundError("图片不存在")),
    )

    result = xhs.publish_images(["/missing.png"], "标题")

    assert result["status"] == "failed"
    assert result["images_count"] == 0
    assert "图片不存在" in result["error"]


def test_publish_images_returns_failed_when_image_list_is_empty() -> None:
    result = xhs.publish_images([], "标题")

    assert result["status"] == "failed"
    assert result["images_count"] == 0
    assert "没有可发布的图片" in result["error"]


def test_run_publish_returns_no_images_when_database_has_no_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("stockhot.publisher.get_images_by_date", lambda date: [])
    monkeypatch.setattr("stockhot.publisher.get_analysis_result", lambda date, kind: None)

    result = run_publish("2026-04-17")

    assert result == {"date": "2026-04-17", "status": "no_images"}


def test_run_publish_uses_today_when_date_is_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stockhot.publisher.get_images_by_date", lambda date: [])
    monkeypatch.setattr("stockhot.publisher.get_analysis_result", lambda date, kind: None)

    class _FakeNow:
        @staticmethod
        def strftime(fmt: str) -> str:
            return "2026-04-19"

    class _FakeDateTime:
        @staticmethod
        def now():
            return _FakeNow()

    monkeypatch.setattr("stockhot.publisher.datetime", _FakeDateTime)

    result = run_publish(None)

    assert result == {"date": "2026-04-19", "status": "no_images"}


@pytest.mark.xfail(reason="scope-creep: publisher feature not implemented")
def test_run_publish_returns_dry_run_result_without_saving_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[tuple[str, str, dict]] = []
    captured: dict = {}
    monkeypatch.setattr(
        "stockhot.publisher.get_images_by_date",
        lambda date: [{"file_path": "/tmp/a.png"}, {"file_path": "/tmp/b.png"}],
    )
    monkeypatch.setattr(
        "stockhot.publisher.get_analysis_result",
        lambda date, kind: {"result_json": {"text": "报告正文"}},
    )

    def fake_publish(images, caption, dry_run=False):
        captured["images"] = images
        captured["caption"] = caption
        captured["dry_run"] = dry_run
        return {"status": "dry_run", "images_count": len(images), "caption": caption}

    monkeypatch.setattr("stockhot.publisher.publish_to_xiaohongshu", fake_publish)
    monkeypatch.setattr(
        "stockhot.publisher._save_publish_record",
        lambda date, platform, result: saved.append((date, platform, result)),
    )

    result = run_publish("2026-04-17", dry_run=True)

    assert result["status"] == "dry_run"
    assert result["dry_run"] is True
    assert result["result"]["images_count"] == 2
    assert result["result"]["caption"] == "报告正文"
    assert captured["dry_run"] is True
    assert saved == []


@pytest.mark.xfail(reason="scope-creep: publisher feature not implemented")
def test_generate_caption_uses_result_json_string(monkeypatch: pytest.MonkeyPatch) -> None:
    analysis = {"result_json": json.dumps({"text": "来自字符串JSON的正文"}, ensure_ascii=False)}

    caption = __import__("stockhot.publisher", fromlist=["_generate_caption"])._generate_caption(
        analysis, "2026-04-17"
    )

    assert caption == "来自字符串JSON的正文"


@pytest.mark.xfail(reason="scope-creep: publisher feature not implemented")
def test_generate_caption_falls_back_when_result_json_string_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher_module = __import__(
        "stockhot.publisher", fromlist=["_generate_caption", "_default_caption"]
    )
    monkeypatch.setattr(publisher_module, "_default_caption", lambda date: f"默认文案:{date}")

    caption = publisher_module._generate_caption({"result_json": "{bad-json"}, "2026-04-17")

    assert caption == "默认文案:2026-04-17"


@pytest.mark.xfail(reason="scope-creep: publisher feature not implemented")
def test_generate_caption_falls_back_when_text_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    publisher_module = __import__(
        "stockhot.publisher", fromlist=["_generate_caption", "_default_caption"]
    )
    monkeypatch.setattr(publisher_module, "_default_caption", lambda date: f"默认文案:{date}")

    caption = publisher_module._generate_caption({"result_json": {}}, "2026-04-17")

    assert caption == "默认文案:2026-04-17"


@pytest.mark.xfail(reason="scope-creep: publisher feature not implemented")
@pytest.mark.parametrize("result_json", ["[]", "null", '"text"'])
def test_generate_caption_falls_back_when_valid_json_shape_is_not_dict(
    monkeypatch: pytest.MonkeyPatch, result_json: str
) -> None:
    publisher_module = __import__(
        "stockhot.publisher", fromlist=["_generate_caption", "_default_caption"]
    )
    monkeypatch.setattr(publisher_module, "_default_caption", lambda date: f"默认文案:{date}")

    caption = publisher_module._generate_caption({"result_json": result_json}, "2026-04-17")

    assert caption == "默认文案:2026-04-17"


@pytest.mark.xfail(reason="scope-creep: publisher feature not implemented")
@pytest.mark.parametrize("text_value", [123, ["a"], {"x": 1}])
def test_generate_caption_falls_back_when_text_is_not_string(
    monkeypatch: pytest.MonkeyPatch, text_value
) -> None:
    publisher_module = __import__(
        "stockhot.publisher", fromlist=["_generate_caption", "_default_caption"]
    )
    monkeypatch.setattr(publisher_module, "_default_caption", lambda date: f"默认文案:{date}")

    caption = publisher_module._generate_caption(
        {"result_json": {"text": text_value}}, "2026-04-17"
    )

    assert caption == "默认文案:2026-04-17"


@pytest.mark.xfail(reason="scope-creep: publisher feature not implemented")
@pytest.mark.parametrize("status", ["draft_ready", "failed", "login_required"])
def test_run_publish_persists_non_dry_run_result(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    saved: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "stockhot.publisher.get_images_by_date", lambda date: [{"file_path": "/tmp/a.png"}]
    )
    monkeypatch.setattr(
        "stockhot.publisher.get_analysis_result",
        lambda date, kind: {"result_json": {"text": "报告正文"}},
    )
    monkeypatch.setattr(
        "stockhot.publisher.publish_to_xiaohongshu",
        lambda images, caption, dry_run=False: {"status": status, "images_count": len(images)},
    )
    monkeypatch.setattr(
        "stockhot.publisher._save_publish_record",
        lambda date, platform, result: saved.append((date, platform, result)),
    )

    result = run_publish("2026-04-17", dry_run=False)

    assert result["status"] == status
    assert result["dry_run"] is False
    assert saved == [("2026-04-17", "xiaohongshu", {"status": status, "images_count": 1})]


@pytest.mark.xfail(reason="scope-creep: publisher feature not implemented")
def test_run_publish_marks_unconfirmed_submitted_result_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "stockhot.publisher.get_images_by_date", lambda date: [{"file_path": "/tmp/a.png"}]
    )
    monkeypatch.setattr(
        "stockhot.publisher.get_analysis_result",
        lambda date, kind: {"result_json": {"text": "报告正文"}},
    )
    monkeypatch.setattr(
        "stockhot.publisher.publish_to_xiaohongshu",
        lambda images, caption, dry_run=False: {
            "status": "submitted",
            "confirmed": False,
            "images_count": len(images),
        },
    )
    monkeypatch.setattr(
        "stockhot.publisher._save_publish_record",
        lambda date, platform, result: saved.append((date, platform, result)),
    )

    result = run_publish("2026-04-17", dry_run=False)

    assert result["status"] == "failed"
    assert result["result"]["confirmed"] is False
    assert result["result"]["error"] == "submitted without confirmation"
    assert saved == [
        (
            "2026-04-17",
            "xiaohongshu",
            {
                "status": "failed",
                "confirmed": False,
                "images_count": 1,
                "error": "submitted without confirmation",
            },
        )
    ]


@pytest.mark.xfail(reason="scope-creep: publisher feature not implemented")
def test_run_publish_accepts_confirmed_submitted_result(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "stockhot.publisher.get_images_by_date", lambda date: [{"file_path": "/tmp/a.png"}]
    )
    monkeypatch.setattr(
        "stockhot.publisher.get_analysis_result",
        lambda date, kind: {"result_json": {"text": "报告正文"}},
    )
    monkeypatch.setattr(
        "stockhot.publisher.publish_to_xiaohongshu",
        lambda images, caption, dry_run=False: {
            "status": "submitted",
            "confirmed": True,
            "confirmationSignal": "success_text",
            "confirmationText": "发布成功，笔记已发布",
            "images_count": len(images),
        },
    )
    monkeypatch.setattr(
        "stockhot.publisher._save_publish_record",
        lambda date, platform, result: saved.append((date, platform, result)),
    )

    result = run_publish("2026-04-17", dry_run=False)

    assert result["status"] == "submitted"
    assert result["result"]["confirmed"] is True
    assert saved == [
        (
            "2026-04-17",
            "xiaohongshu",
            {
                "status": "submitted",
                "confirmed": True,
                "confirmationSignal": "success_text",
                "confirmationText": "发布成功，笔记已发布",
                "images_count": 1,
            },
        )
    ]


@pytest.mark.xfail(reason="scope-creep: publisher feature not implemented")
def test_run_publish_rejects_legacy_success_result(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "stockhot.publisher.get_images_by_date", lambda date: [{"file_path": "/tmp/a.png"}]
    )
    monkeypatch.setattr(
        "stockhot.publisher.get_analysis_result",
        lambda date, kind: {"result_json": {"text": "报告正文"}},
    )
    monkeypatch.setattr(
        "stockhot.publisher.publish_to_xiaohongshu",
        lambda images, caption, dry_run=False: {"status": "success", "images_count": len(images)},
    )
    monkeypatch.setattr(
        "stockhot.publisher._save_publish_record",
        lambda date, platform, result: saved.append((date, platform, result)),
    )

    result = run_publish("2026-04-17", dry_run=False)

    assert result["status"] == "failed"
    assert result["result"]["error"] == "legacy success status is not accepted"
    assert saved == [
        (
            "2026-04-17",
            "xiaohongshu",
            {
                "status": "failed",
                "images_count": 1,
                "error": "legacy success status is not accepted",
            },
        )
    ]


@pytest.mark.xfail(reason="scope-creep: publisher feature not implemented")
def test_run_publish_uses_unknown_when_result_status_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "stockhot.publisher.get_images_by_date", lambda date: [{"file_path": "/tmp/a.png"}]
    )
    monkeypatch.setattr(
        "stockhot.publisher.get_analysis_result",
        lambda date, kind: {"result_json": {"text": "报告正文"}},
    )
    monkeypatch.setattr(
        "stockhot.publisher.publish_to_xiaohongshu",
        lambda images, caption, dry_run=False: {"images_count": len(images)},
    )
    monkeypatch.setattr(
        "stockhot.publisher._save_publish_record",
        lambda date, platform, result: saved.append((date, platform, result)),
    )

    result = run_publish("2026-04-17", dry_run=False)

    assert result["status"] == "unknown"
    assert saved == [("2026-04-17", "xiaohongshu", {"images_count": 1})]


def test_build_title_falls_back_for_blank_caption() -> None:
    assert xhs._build_title("\n  \n") == "小红书自动发布"


def test_build_title_truncates_to_twenty_characters() -> None:
    title = xhs._build_title("12345678901234567890ABC\n正文")
    assert title == "12345678901234567890"
