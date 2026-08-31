from ytdl_bot.errors import redact


def test_redactor_removes_bot_tokens_proxy_credentials_and_absolute_paths():
    raw = (
        "https://api.telegram.org/bot" + "123456789:" + ("A" * 32) + "/getMe "
        "proxy=http://alice:secret@example.test /Users/person/private/file.mp4"
    )
    safe = redact(raw)
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in safe
    assert "alice:secret" not in safe
    assert "/Users/person" not in safe
