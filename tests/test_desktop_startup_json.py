from __future__ import annotations

import json

from voice_code.desktop.startup import serialize_startup_json


def test_startup_json_is_ascii_safe_for_windows_subprocess_stdout() -> None:
    payload = {
        "title": "历史任务",
        "cwd": r"D:\语码工作区",
        "message": "语音桥接已启动",
    }

    encoded = serialize_startup_json(payload)

    encoded.encode("ascii")
    assert json.loads(encoded) == payload
    assert "\\u5386\\u53f2\\u4efb\\u52a1" in encoded
