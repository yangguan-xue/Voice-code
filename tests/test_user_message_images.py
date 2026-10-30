"""多模态用户消息构造测试。"""

from __future__ import annotations

from voice_code.agent.loop import _build_user_message


def test_plain_text_message_without_images():
    msg = _build_user_message("hello", None)
    assert msg.content == "hello"


def test_images_become_content_blocks():
    msg = _build_user_message(
        "look at this",
        [{"mime": "image/png", "data_base64": "QUJD"}],
    )
    assert isinstance(msg.content, list)
    assert msg.content[0] == {"type": "text", "text": "look at this"}
    assert msg.content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,QUJD"},
    }


def test_multiple_images_append_in_order():
    images = [
        {"mime": "image/png", "data_base64": "QQ=="},
        {"mime": "image/jpeg", "data_base64": "Qg=="},
    ]
    msg = _build_user_message("x", images)
    assert len(msg.content) == 3
    assert msg.content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")
