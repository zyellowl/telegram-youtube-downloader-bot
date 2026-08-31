import os
import time
from pathlib import Path

from ytdl_bot.cleanup import cleanup_old_files
from ytdl_bot.cleanup import remove_task_directory


def test_cleanup_old_files_removes_expired_files(tmp_path: Path):
    old_file = tmp_path / "old.tmp"
    old_file.write_text("old")
    old_time = time.time() - 3600
    os.utime(old_file, (old_time, old_time))

    removed = cleanup_old_files(tmp_path, max_age_seconds=60)

    assert removed == [old_file]
    assert not old_file.exists()


def test_cleanup_keeps_recent_files(tmp_path: Path):
    recent_file = tmp_path / "recent.tmp"
    recent_file.write_text("recent")

    removed = cleanup_old_files(tmp_path, max_age_seconds=3600)

    assert removed == []
    assert recent_file.exists()


def test_remove_task_directory_removes_parent_inside_download_root(tmp_path: Path):
    task_dir = tmp_path / "task-1"
    task_file = task_dir / "video.mp4"
    task_dir.mkdir()
    task_file.write_text("video")

    remove_task_directory(task_file, download_root=tmp_path)

    assert not task_dir.exists()


def test_cleanup_unlinks_expired_symlink_without_touching_target(tmp_path: Path):
    outside = tmp_path.parent / "outside-cleanup-target"
    outside.mkdir(exist_ok=True)
    target = outside / "keep.txt"
    target.write_text("keep")
    link = tmp_path / "old-link"
    link.symlink_to(outside, target_is_directory=True)
    old_time = time.time() - 3600
    os.utime(link, (old_time, old_time), follow_symlinks=False)

    cleanup_old_files(tmp_path, max_age_seconds=60)

    assert target.exists()
    assert not link.exists()
