import pytest

from ytdl_bot.limits import UserTaskLimiter


@pytest.mark.asyncio
async def test_user_limiter_blocks_second_same_user_task():
    limiter = UserTaskLimiter(max_tasks_per_user=1)

    async with limiter.reserve(user_id=7):
        assert limiter.active_for_user(7) == 1
        with pytest.raises(RuntimeError):
            async with limiter.reserve(user_id=7):
                pass

    assert limiter.active_for_user(7) == 0


@pytest.mark.asyncio
async def test_user_limiter_allows_different_users():
    limiter = UserTaskLimiter(max_tasks_per_user=1)

    async with limiter.reserve(user_id=7):
        async with limiter.reserve(user_id=8):
            assert limiter.active_for_user(7) == 1
            assert limiter.active_for_user(8) == 1
