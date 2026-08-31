import shutil
import time
from pathlib import Path


def cleanup_old_files(root: Path, max_age_seconds: int) -> list[Path]:
    if not root.exists():
        return []

    root = root.resolve()
    now = time.time()
    removed: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.exists():
            continue
        try:
            resolved = path.resolve()
            if root not in resolved.parents and resolved != root and not path.is_symlink():
                continue
            age = now - path.lstat().st_mtime
        except OSError:
            continue
        if age <= max_age_seconds:
            continue
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(path)

    _remove_empty_dirs(root)
    return removed


def remove_task_directory(file_path: Path, download_root: Path) -> None:
    task_dir = file_path.parent.resolve()
    root = download_root.resolve()
    if task_dir == root or root not in task_dir.parents:
        return
    if task_dir.exists():
        shutil.rmtree(task_dir)


def _remove_empty_dirs(root: Path) -> None:
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
