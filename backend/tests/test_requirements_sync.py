"""Keep the simple runtime requirements list aligned with package metadata."""

from __future__ import annotations

import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPOSITORY_ROOT / "backend" / "pyproject.toml"
REQUIREMENTS_PATH = REPOSITORY_ROOT / "requirements.txt"


def test_runtime_requirements_match_project_dependencies_exactly() -> None:
    with PYPROJECT_PATH.open("rb") as stream:
        project_dependencies = tomllib.load(stream)["project"]["dependencies"]

    runtime_dependencies = [
        line.strip()
        for line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert runtime_dependencies == project_dependencies, (
        "requirements.txt must exactly match backend/pyproject.toml [project].dependencies"
    )
