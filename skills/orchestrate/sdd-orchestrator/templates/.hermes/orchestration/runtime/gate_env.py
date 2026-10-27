#!/usr/bin/env python3
"""The environment every gate command runs with, shared by ``sdd.py gate`` and ``gate_validator``.

A gate executes repository code and repository tools. A writing worker can plant interpreter
bytecode in the repository's cache directories that the workspace audit leaves out as a
test-run artifact (``runtime/audit_scope.py``) when its header copies its source's mtime and
size; an interpreter that reads that cache would run the planted code instead of the source.
A type checker or linter trusts its incremental cache the same way: a planted cache entry
whose recorded mtime and size match a file makes the tool skip that file and report no error.
So each gate run gets the caller's environment plus, all inside one fresh temporary directory
outside the repository that is deleted when the run ends:

* a bytecode-cache prefix and the switch that stops the interpreter writing bytecode at all
  (``detect_stack.BYTECODE_PREFIX_ENV``, ``detect_stack.BYTECODE_ISOLATION_ENV``);
* one empty cache directory per tool named in ``detect_stack.GATE_CACHE_DIR_ENVS`` (the
  documented ``MYPY_CACHE_DIR`` and ``RUFF_CACHE_DIR``), replacing any inherited value.

Ecosystem names live only in ``detect_stack``. The child then neither loads a cached file from
the repository nor writes one into it.

Not covered: a gate command that clears these variables itself (an isolated-mode interpreter,
for example), a cache directory set explicitly by the tool's own configuration (mypy
``cache_dir``, ruff ``cache-dir``) or by ``--cache-dir`` on the command line, both of which the
tools' documentation ranks above the variable, or any process outside the gate. Standard
library only.
"""
from __future__ import annotations

import contextlib
import subprocess
import tempfile
from os import environ as process_environment
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from detect_stack import BYTECODE_ISOLATION_ENV, BYTECODE_PREFIX_ENV, GATE_CACHE_DIR_ENVS


@contextlib.contextmanager
def isolated(base: Mapping[str, str] | None = None) -> Iterator[dict[str, str]]:
    """A copy of ``base`` (default: this process's environment) for one gate run; every cache is removed on exit."""
    env = dict(process_environment if base is None else base)
    with tempfile.TemporaryDirectory(prefix="gate-caches-") as root:
        prefix = Path(root) / "pycache"
        prefix.mkdir()
        env.update(BYTECODE_ISOLATION_ENV)
        env[BYTECODE_PREFIX_ENV] = str(prefix)
        for name in GATE_CACHE_DIR_ENVS:
            cache = Path(root) / name.lower()
            cache.mkdir()
            env[name] = str(cache)
        yield env


def run(argv: Sequence[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    """``sdd.py gate``'s command run: inherited environment, isolated caches, captured text output."""
    with isolated() as env:
        return subprocess.run(list(argv), cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False, env=env)
