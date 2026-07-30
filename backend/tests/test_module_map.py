"""Structural validation for the agent navigation module map."""

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODULE_MAP_PATH = REPOSITORY_ROOT / "backend" / "module-map.json"


class DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object declares the same key more than once."""


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while preserving duplicate-key validation."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_module_map() -> dict[str, Any]:
    """Load module-map.json while rejecting duplicate object and feature keys."""
    return json.loads(
        MODULE_MAP_PATH.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
    )


def iter_referenced_paths(module_map: dict[str, Any]) -> list[str]:
    """Return values from every module-map field that represents a file path."""
    application = module_map["application"]
    paths = [
        application["entrypoint"],
        application["factory"],
        application["api_router"],
    ]

    for feature in module_map["features"].values():
        paths.append(feature["entrypoint"])
        for field in ("contracts", "depends_on", "tests"):
            paths.extend(feature[field])
    return paths


def validate_repository_path(value: str) -> Path:
    """Validate and resolve a repo-relative POSIX file path."""
    assert isinstance(value, str) and value
    assert "\\" not in value

    posix_path = PurePosixPath(value)
    assert not posix_path.is_absolute()
    assert not PureWindowsPath(value).is_absolute()
    assert ".." not in posix_path.parts
    assert posix_path.as_posix() == value

    resolved = (REPOSITORY_ROOT / Path(*posix_path.parts)).resolve()
    resolved.relative_to(REPOSITORY_ROOT.resolve())
    assert resolved.is_file(), f"Referenced module-map file does not exist: {value}"
    return resolved


def test_module_map_is_valid_and_references_existing_repo_files() -> None:
    module_map = load_module_map()

    assert module_map["version"] == 1
    assert module_map["features"]
    for value in iter_referenced_paths(module_map):
        validate_repository_path(value)


def test_duplicate_json_keys_are_rejected() -> None:
    with pytest.raises(DuplicateJsonKeyError, match="Duplicate JSON key: health"):
        json.loads(
            '{"features":{"health":{},"health":{}}}',
            object_pairs_hook=reject_duplicate_keys,
        )


@pytest.mark.parametrize(
    "invalid_path",
    [
        "/absolute/path.py",
        "C:/absolute/path.py",
        "../outside.py",
        "backend\\app\\main.py",
        "./backend/app/main.py",
    ],
)
def test_non_repo_relative_posix_paths_are_rejected(invalid_path: str) -> None:
    with pytest.raises((AssertionError, ValueError)):
        validate_repository_path(invalid_path)


def test_missing_referenced_file_is_rejected() -> None:
    with pytest.raises(AssertionError, match="Referenced module-map file does not exist"):
        validate_repository_path("backend/app/does-not-exist.py")
