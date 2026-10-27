#!/usr/bin/env python3
"""The environment every gate command runs with, shared by ``sdd.py gate`` and ``gate_validator``.

A gate executes repository code. A writing worker can plant interpreter bytecode in the
repository's cache directories that the workspace audit leaves out as a test-run artifact
(``runtime/audit_scope.py``) when its header copies its source's mtime and size; an
interpreter that reads that cache would run the planted code instead of the source. So
each gate run gets the caller's environment plus a bytecode-cache prefix that is a fresh
temporary directory outside the repository, deleted when the run ends, and the switch that
stops the interpreter writing bytecode at all (``detect_stack.BYTECODE_PREFIX_ENV``,
``detect_stack.BYTECODE_ISOLATION_ENV``: ecosystem names live only there). The child
then neither loads a cached file from the repository nor writes one into it.

Not covered: a gate command that clears these variables itself (an isolated-mode
interpreter, for example) or any process outside the gate. Standard library only.
"""
from __future__ import annotations

import contextlib
import subprocess
import tempfile
from os import environ as process_environment
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from detect_stack import BYTECODE_ISOLATION_ENV, BYTECODE_PREFIX_ENV


@contextlib.contextmanager
def isolated(base: Mapping[str, str] | None = None) -> Iterator[dict[str, str]]:
    """A copy of ``base`` (default: this process's environment) for one gate run; the prefix is removed on exit."""
    env = dict(process_environment if base is None else base)
    with tempfile.TemporaryDirectory(prefix="gate-pycache-") as prefix:
        env.update(BYTECODE_ISOLATION_ENV)
        env[BYTECODE_PREFIX_ENV] = prefix
        yield env


def run(argv: Sequence[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    """``sdd.py gate``'s command run: inherited environment, isolated bytecode cache, captured text output."""
    with isolated() as env:
        return subprocess.run(list(argv), cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False, env=env)
