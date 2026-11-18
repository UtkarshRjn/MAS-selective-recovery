"""Workspaces, patch artifacts, integration, and test execution for the coding pilot.

A patch artifact is the full content of an agent's writable files. Its digest
covers paths and contents, so a test report can be bound to the exact patch it
ran against. Integration applies patches in a frozen order onto the base tree
and resolves files that two agents may write with a three-way merge.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .registry import RegistryError, TaskRecord, sha256_file

CACHE = Path(os.environ.get("AGENTREC_CODEPILOT_CACHE", Path.home() / ".cache" / "agentrec" / "codepilot"))
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: float = 900) -> str:
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
    return result.stdout


# ------------------------------------------------------------------ patches

def patch_digest(files: dict[str, str]) -> str:
    body = "\n".join(f"{path}\0{hashlib.sha256(text.encode()).hexdigest()}" for path, text in sorted(files.items()))
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()[:20]


def make_patch(agent: str, files: dict[str, str], **meta: Any) -> dict[str, Any]:
    return {"agent": agent, "files": dict(sorted(files.items())), "digest": patch_digest(files), **meta}


# --------------------------------------------------------------- repository

@dataclass
class RepoCache:
    """One bare clone per repository; workspaces are cheap local clones at the frozen base SHA."""

    root: Path = CACHE
    benchmark_root: Path | None = None

    def bare(self, task: TaskRecord) -> Path:
        if not task.origin_url:
            raise RegistryError(f"{task.task_id} has no recorded origin URL")
        path = self.root / "repos" / f"{task.repository}.git"
        with _lock(str(path)):
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                _run(["git", "clone", "--bare", "-q", task.origin_url, str(path)])
            try:
                _run(["git", "--git-dir", str(path), "cat-file", "-e", f"{task.base_sha}^{{commit}}"])
            except RuntimeError:
                _run(["git", "--git-dir", str(path), "fetch", "origin", task.base_sha])
        return path

    def materialize(self, task: TaskRecord, dest: Path, *, overlays: bool = True) -> Path:
        """A working tree at the base SHA with the release's overlays applied (each sha256-checked)."""
        bare = self.bare(task)
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "-q", "--shared", "--no-checkout", str(bare), str(dest)])
        _run(["git", "-C", str(dest), "checkout", "-q", task.base_sha])
        if overlays and task.overlays:
            if self.benchmark_root is None:
                raise RegistryError("overlays need the benchmark checkout")
            for overlay in task.overlays:
                path = self.benchmark_root / overlay.path
                if sha256_file(path) != overlay.sha256:
                    raise RegistryError(f"overlay {overlay.path} changed since the release")
                _run(["git", "-C", str(dest), "apply", "--whitespace=nowarn", str(path)])
        return dest

    def file_at(self, task: TaskRecord, revision: str, path: str) -> str | None:
        try:
            return _run(["git", "--git-dir", str(self.bare(task)), "show", f"{revision}:{path}"])
        except RuntimeError:
            return None

    def reference_files(self, task: TaskRecord, paths: tuple[str, ...]) -> dict[str, str]:
        """The upstream implementation the Commit0 stubs were cut from (the base commit's parent).

        Used only by the reference agent, which validates the pipeline; never visible to the monitor.
        """
        out: dict[str, str] = {}
        for path in paths:
            text = self.file_at(task, f"{task.base_sha}^", path)
            if text is not None:
                out[path] = text
        return out


def base_files(workspace: Path, paths: tuple[str, ...]) -> dict[str, str]:
    return {path: (workspace / path).read_text() if (workspace / path).exists() else "" for path in paths}


def write_files(workspace: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def merge3(base: str, ours: str, theirs: str) -> tuple[str, bool]:
    """Three-way merge of one file. Returns (text, clean)."""
    if ours == theirs or theirs == base:
        return ours, True
    if ours == base:
        return theirs, True
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            path = Path(tmp) / name
            path.write_text(text)
            paths.append(str(path))
        result = subprocess.run(["git", "merge-file", "-p", *paths], capture_output=True, text=True)
        return result.stdout, result.returncode == 0


def integrate(base: Path, patches: list[dict[str, Any]], dest: Path) -> dict[str, Any]:
    """Copy the base tree and apply patches in order; overlapping files are merged three-way."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(base, dest, ignore=shutil.ignore_patterns(".git"))
    conflicts: list[str] = []
    for patch in patches:
        for path, text in patch["files"].items():
            target = dest / path
            original = (base / path).read_text() if (base / path).exists() else ""
            current = target.read_text() if target.exists() else ""
            merged, clean = merge3(original, current, text)
            if not clean:
                conflicts.append(path)
            write_files(dest, {path: merged})
    files = {}
    for patch in patches:
        for path in patch["files"]:
            files[path] = (dest / path).read_text()
    return {
        "digest": patch_digest(files),
        "patches": {patch["agent"]: patch["digest"] for patch in patches},
        "conflicts": sorted(set(conflicts)),
    }


# ----------------------------------------------------------------- faults

def _spans(text: str) -> dict[str, tuple[int, int]]:
    """Line spans of top-level functions and methods, keyed by qualified name."""
    tree = ast.parse(text)
    spans: dict[str, tuple[int, int]] = {}

    def visit(nodes, prefix: str) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
                spans[prefix + node.name] = (start, node.end_lineno or node.lineno)
            elif isinstance(node, ast.ClassDef):
                visit(node.body, prefix + node.name + ".")

    visit(tree.body, "")
    return spans


def functions(text: str) -> list[str]:
    try:
        return sorted(_spans(text))
    except SyntaxError:
        return []


def revert_function(current: str, stub: str, qualname: str) -> str | None:
    """Replace one function in `current` with its body from `stub` (the unfinished base version)."""
    try:
        now = _spans(current)
        old = _spans(stub)
    except SyntaxError:
        return None
    if qualname not in now or qualname not in old:
        return None
    lines = current.splitlines(keepends=True)
    stub_lines = stub.splitlines(keepends=True)
    start, end = now[qualname]
    s_start, s_end = old[qualname]
    replacement = stub_lines[s_start - 1:s_end]
    indent_now = len(lines[start - 1]) - len(lines[start - 1].lstrip())
    indent_old = len(stub_lines[s_start - 1]) - len(stub_lines[s_start - 1].lstrip())
    if indent_now != indent_old:
        delta = indent_now - indent_old
        replacement = [(" " * delta + line) if delta > 0 else line[-delta:] for line in replacement]
    out = "".join([*lines[:start - 1], *replacement, *lines[end:]])
    if out == current:
        return None
    return out


# --------------------------------------------------------------- execution

_RESULT = re.compile(r"^(PASSED|FAILED|ERROR|XPASS|XFAIL|SKIPPED)\s+(\S+)")
_TEST_EXTRA = re.compile(r"test|dev", re.I)


def test_extras(workspace: Path) -> list[str]:
    """Optional-dependency groups that look like test requirements, as the project declares them."""
    names: set[str] = set()
    pyproject = workspace / "pyproject.toml"
    if pyproject.exists():
        import tomllib

        data = tomllib.loads(pyproject.read_text())
        names.update((data.get("project", {}).get("optional-dependencies") or {}).keys())
    setup_cfg = workspace / "setup.cfg"
    if setup_cfg.exists():
        import configparser

        parser = configparser.ConfigParser()
        parser.read(setup_cfg)
        if parser.has_section("options.extras_require"):
            names.update(parser.options("options.extras_require"))
    setup_py = workspace / "setup.py"
    if setup_py.exists():
        block = re.search(r"extras_require\s*=\s*\{(.*?)\}", setup_py.read_text(), re.S)
        if block:
            names.update(re.findall(r"[\"']([\w.-]+)[\"']\s*:", block.group(1)))
    return sorted(name for name in names if _TEST_EXTRA.search(name))


@dataclass
class TestRun:
    passed: tuple[str, ...]
    failed: tuple[str, ...]
    wall_s: float
    output: str = ""

    def all_passed(self, required: tuple[str, ...]) -> bool:
        return all(probe_passed(item, self.passed, self.failed) for item in required)

    def failing(self, required: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item for item in required if not probe_passed(item, self.passed, self.failed))

    def row(self) -> dict[str, Any]:
        return {"passed": len(self.passed), "failed": list(self.failed)[:50], "wall_s": round(self.wall_s, 3)}


def _covers(probe: str, node: str) -> bool:
    """A probe id names a test, or every parametrization of it (`test_x` covers `test_x[a]`)."""
    return node == probe or node.startswith(probe + "[") or node.startswith(probe + "::")


def probe_passed(probe: str, passed: tuple[str, ...], failed: tuple[str, ...]) -> bool:
    if any(_covers(probe, node) for node in failed):
        return False
    return any(_covers(probe, node) for node in passed)


def parse_results(output: str, requested: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    passed: set[str] = set()
    failed: set[str] = set()
    for line in output.splitlines():
        match = _RESULT.match(line.strip())
        if not match:
            continue
        status, node = match.groups()
        if status in {"PASSED", "XFAIL", "XPASS"}:
            passed.add(node)
        elif status in {"FAILED", "ERROR"}:
            failed.add(node)
    for node in requested:
        if "::" in node and not any(_covers(node, item) for item in passed | failed):
            failed.add(node)
    return tuple(sorted(passed)), tuple(sorted(failed - passed))


@dataclass
class LocalExecutor:
    """Runs pytest in a per-repository virtualenv on this machine. Not the official environment."""

    root: Path = CACHE
    python: str = f"{sys.version_info.major}.{sys.version_info.minor}"
    timeout: float = 600
    label: str = "local-venv (not the official container)"
    _ready: set[str] = field(default_factory=set)

    def venv(self, task: TaskRecord, workspace: Path) -> Path:
        path = self.root / "venvs" / task.repository
        with _lock(str(path)):
            if task.repository in self._ready and path.exists():
                return path
            python = path / "bin" / "python"
            marker = path / ".agentrec-installed"
            if not marker.exists():
                if path.exists():
                    shutil.rmtree(path)
                _run(["uv", "venv", "-q", "--python", self.python, str(path)])
                packages = ["pytest", "pytest-timeout"]
                extras = test_extras(workspace)
                target = f"{workspace}[{','.join(extras)}]" if extras else str(workspace)
                # The copied tree has no .git, so VCS-derived versions need a placeholder.
                env = dict(os.environ, SETUPTOOLS_SCM_PRETEND_VERSION="0.0.0")
                try:
                    _run(["uv", "pip", "install", "-q", "--python", str(python), target, *packages], env=env)
                except RuntimeError:
                    _run(["uv", "pip", "install", "-q", "--python", str(python), str(workspace), *packages], env=env)
                for name in ("requirements-dev.txt", "test-requirements.txt", "tests/requirements.txt",
                             "requirements/tests.txt", "requirements-test.txt"):
                    if (workspace / name).exists():
                        try:
                            _run(["uv", "pip", "install", "-q", "--python", str(python), "-r", str(workspace / name)], env=env)
                        except RuntimeError:
                            pass
                marker.write_text(str(workspace))
            self._ready.add(task.repository)
            return path

    def run(self, task: TaskRecord, workspace: Path, tests: tuple[str, ...]) -> TestRun:
        import time

        venv = self.venv(task, workspace)
        env = dict(os.environ)
        source = workspace / "src" if (workspace / "src").is_dir() else workspace
        env["PYTHONPATH"] = f"{source}{os.pathsep}{workspace}"
        env.pop("VIRTUAL_ENV", None)
        cmd = [str(venv / "bin" / "python"), "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", "-o", "addopts=",
               "--timeout=60", *tests]
        started = time.perf_counter()
        try:
            result = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True, timeout=self.timeout)
            output = result.stdout + result.stderr
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        passed, failed = parse_results(output, tests)
        return TestRun(passed, failed, time.perf_counter() - started, output[-4000:])


@dataclass
class DockerExecutor:
    """Runs pytest in the release's pinned task image (by digest), with the workspace mounted at /testbed."""

    timeout: float = 900
    platform: str = "linux/amd64"
    label: str = "official-image"

    def run(self, task: TaskRecord, workspace: Path, tests: tuple[str, ...]) -> TestRun:
        import time

        if not task.image or not task.image_digest:
            raise RegistryError(f"{task.task_id} has no pinned image")
        image = f"{task.image.split(':')[0]}@{task.image_digest}"
        cmd = [
            "docker", "run", "--rm", "--network", "none", "--platform", self.platform,
            "-v", f"{workspace}:/testbed", "-w", "/testbed", image,
            "python", "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", "-o", "addopts=", *tests,
        ]
        started = time.perf_counter()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
            output = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            output = ""
        passed, failed = parse_results(output, tests)
        return TestRun(passed, failed, time.perf_counter() - started, output[-4000:])


__all__ = [
    "CACHE", "DockerExecutor", "LocalExecutor", "RepoCache", "TestRun", "base_files", "functions", "integrate",
    "make_patch", "merge3", "parse_results", "patch_digest", "revert_function", "write_files",
]
