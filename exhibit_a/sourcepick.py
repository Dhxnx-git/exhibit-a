"""Deterministically pick which source files to show the synthesizer.

No LLM here — we don't want to pay a model to guess filenames, and we don't
want attacker-controlled plan text to make us read `/etc/passwd`. This module
walks the workspace, ranks files by how well they match the plan's named
symbols and keywords, and returns excerpts. Everything is clamped inside the
workspace root.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .schemas import ReproPlan

MAX_FILES = 6
MAX_FILE_CHARS = 6_000
SKIP_DIRS = {".git", ".venv", "venv", ".exhibit-venv", "__pycache__", "tests",
             "test", "node_modules", "dist", "build", ".tox"}


def _iter_py(root: Path):
    for p in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def _defined_symbols(text: str) -> set[str]:
    """Top-level function/class names, via AST (never exec)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def pick_sources(workspace: Path, plan: ReproPlan) -> dict[str, str]:
    """Return {relative_path: excerpt} for the most plan-relevant files."""
    workspace = Path(workspace).resolve()
    wanted_symbols = {s.split(".")[-1] for s in plan.affected_symbols}
    keywords = {k.lower() for k in plan.symptom_keywords} | {
        w.lower() for w in plan.title.split() if len(w) > 4}

    scored: list[tuple[int, Path, str]] = []
    for path in _iter_py(workspace):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Defensive: never escape the workspace even via a symlink we missed.
        if workspace not in path.resolve().parents and path.resolve() != workspace:
            continue
        score = 0
        symbols = _defined_symbols(text)
        score += 10 * len(symbols & wanted_symbols)
        low = text.lower()
        score += sum(1 for k in keywords if k and k in low)
        # Files literally named after an affected symbol are strong hints.
        if any(sym.lower() in path.stem.lower() for sym in wanted_symbols):
            score += 5
        if score > 0:
            scored.append((score, path, text))

    scored.sort(key=lambda t: t[0], reverse=True)
    if not scored:
        # Nothing matched — fall back to the package's __init__ and largest
        # modules so the synthesizer at least sees the public surface.
        for path in list(_iter_py(workspace))[:MAX_FILES]:
            try:
                scored.append((0, path, path.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue

    out: dict[str, str] = {}
    for _, path, text in scored[:MAX_FILES]:
        rel = path.relative_to(workspace).as_posix()
        out[rel] = text[:MAX_FILE_CHARS]
    return out
