from collections import defaultdict
from contextlib import asynccontextmanager


class UserTaskLimiter:
    def __init__(self, max_tasks_per_user: int):
        self.max_tasks_per_user = max_tasks_per_user
        self._active: dict[int, int] = defaultdict(int)

    def active_for_user(self, user_id: int) -> int:
        return self._active[user_id]

    @asynccontextmanager
    async def reserve(self, user_id: int):
        if self._active[user_id] >= self.max_tasks_per_user:
            raise RuntimeError("You already have a download running. Please wait for it to finish.")
        self._active[user_id] += 1
        try:
            yield
        finally:
            self._active[user_id] -= 1
            if self._active[user_id] <= 0:
                self._active.pop(user_id, None)
