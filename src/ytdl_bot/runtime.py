from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Callable

from packaging.version import Version
from yt_dlp.utils._jsruntime import BunJsRuntime, DenoJsRuntime, NodeJsRuntime, QuickJsRuntime


@dataclass(frozen=True)
class CapabilityIssue:
    capability: str
    message: str


@dataclass(frozen=True)
class RuntimeReport:
    ready: bool
    versions: dict[str, str]
    js_runtime: str | None
    issues: tuple[CapabilityIssue, ...]

    @property
    def summary(self) -> str:
        if self.ready:
            return "Runtime capabilities are ready."
        return "Runtime is not ready: " + ", ".join(issue.capability for issue in self.issues)


def _lookup_executable(name: str) -> str | None:
    sibling = Path(sys.executable).with_name(name)
    if sibling.is_file():
        return str(sibling)
    return shutil.which(name)


def youtube_js_runtimes(
    executable_lookup: Callable[[str], str | None] = _lookup_executable,
) -> dict[str, dict[str, str]]:
    """Return the shared yt-dlp runtime configuration for inspect and download."""
    runtimes: dict[str, dict[str, str]] = {}
    for name in ("deno", "node", "quickjs", "bun"):
        path = executable_lookup(name)
        runtimes[name] = {"path": path} if path else {}
    return runtimes


def check_runtime_capabilities(
    download_dir: Path,
    *,
    executable_lookup: Callable[[str], str | None] = _lookup_executable,
    package_version: Callable[[str], str] = metadata.version,
    runtime_supported: Callable[[str, str], bool] | None = None,
) -> RuntimeReport:
    issues: list[CapabilityIssue] = []
    versions: dict[str, str] = {}
    for package, minimum in (("yt-dlp", "2026.8.19"), ("yt-dlp-ejs", "0")):
        try:
            value = package_version(package)
            versions[package] = value
            if package == "yt-dlp" and Version(value) < Version(minimum):
                issues.append(CapabilityIssue(package, f"{package} must be at least {minimum}."))
        except (metadata.PackageNotFoundError, KeyError, ValueError):
            issues.append(CapabilityIssue(package, f"{package} is not installed."))

    runtime_supported = runtime_supported or _runtime_supported
    js_runtime = None
    for name in ("deno", "node", "quickjs", "bun"):
        path = executable_lookup(name)
        if path and runtime_supported(name, path):
            js_runtime = name
            break
    if js_runtime is None:
        issues.append(CapabilityIssue("javascript-runtime", "Install Node.js, Deno, Bun, or QuickJS."))
    for binary in ("ffmpeg", "ffprobe"):
        if not executable_lookup(binary):
            issues.append(CapabilityIssue(binary, f"{binary} is not installed or not on PATH."))
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
        probe = download_dir / ".write-check"
        probe.touch(exist_ok=False)
        probe.unlink()
    except OSError:
        issues.append(CapabilityIssue("download-directory", "Configured download directory is not writable."))
    return RuntimeReport(not issues, versions, js_runtime, tuple(issues))


def _runtime_supported(name: str, path: str) -> bool:
    runtime_class = {
        "deno": DenoJsRuntime,
        "node": NodeJsRuntime,
        "quickjs": QuickJsRuntime,
        "bun": BunJsRuntime,
    }[name]
    info = runtime_class(path).info
    return bool(info and info.supported)
