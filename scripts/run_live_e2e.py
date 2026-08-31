from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    environment = os.environ.copy()
    environment["RUN_LIVE_YOUTUBE"] = "1"
    return subprocess.call([sys.executable, "-m", "pytest", "-m", "live", "-v"], env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
