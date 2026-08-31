import os

import pytest


pytestmark = pytest.mark.live


def test_telegram_receipt_gate_is_explicit():
    """A release cannot claim Telegram E2E without an authorized chat fixture."""
    if os.environ.get("RUN_LIVE_TELEGRAM") != "1":
        pytest.skip("Set RUN_LIVE_TELEGRAM=1 only with an authorized test chat and receipt harness")
    pytest.fail("Configure the local receipt harness to download and ffprobe Telegram's received file")
