"""Structural validation for the agent navigation module map."""

import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODULE_MAP_PATH = REPOSITORY_ROOT / "backend" / "module-map.json"
FRONTEND_MODULE_MAP_PATH = REPOSITORY_ROOT / "frontend" / "module-map.json"
STATIC_JS_IMPORT = re.compile(
    r"^\s*import(?:[\s\S]*?\sfrom\s*)?[\"']([^\"']+)[\"']\s*;?",
    re.MULTILINE,
)


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


def load_module_map(path: Path = MODULE_MAP_PATH) -> dict[str, Any]:
    """Load module-map.json while rejecting duplicate object and component keys."""
    return json.loads(
        path.read_text(encoding="utf-8"),
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

    for group in ("infrastructure", "features"):
        for component in module_map.get(group, {}).values():
            paths.append(component["entrypoint"])
            for field in ("contracts", "implementation", "depends_on", "tests"):
                paths.extend(component.get(field, []))
    return paths


def iter_frontend_referenced_paths(module_map: dict[str, Any]) -> list[str]:
    """Return every file path declared by the frontend navigation map."""
    paths = list(module_map["application"].values())
    for component in module_map.get("modules", {}).values():
        paths.append(component["entrypoint"])
        for field in (
            "contracts",
            "implementation",
            "composition",
            "depends_on",
            "tests",
        ):
            paths.extend(component.get(field, []))
    return paths


def local_static_js_imports(source: Path) -> set[Path]:
    """Resolve direct relative static imports from one JavaScript module."""
    if source.suffix not in {".js", ".mjs"}:
        return set()
    imports: set[Path] = set()
    for specifier in STATIC_JS_IMPORT.findall(source.read_text(encoding="utf-8")):
        if not specifier.startswith("."):
            continue
        target = (source.parent / specifier).resolve()
        target.relative_to(REPOSITORY_ROOT.resolve())
        assert target.is_file(), f"Imported frontend module does not exist: {specifier}"
        imports.add(target)
    return imports


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
    assert module_map["infrastructure"]
    assert module_map["features"]
    for value in iter_referenced_paths(module_map):
        validate_repository_path(value)


def test_frontend_module_map_is_valid_and_references_existing_repo_files() -> None:
    module_map = load_module_map(FRONTEND_MODULE_MAP_PATH)

    assert module_map["version"] == 1
    assert module_map["modules"]
    assert set(module_map["field_semantics"]) == {
        "entrypoint",
        "contracts",
        "implementation",
        "composition",
        "depends_on",
        "tests",
    }
    for value in iter_frontend_referenced_paths(module_map):
        validate_repository_path(value)


def test_frontend_dependencies_match_direct_static_imports() -> None:
    module_map = load_module_map(FRONTEND_MODULE_MAP_PATH)

    for component_id, component in module_map["modules"].items():
        source_paths = [
            validate_repository_path(component["entrypoint"]),
            *(validate_repository_path(value) for value in component.get("implementation", [])),
        ]
        owned_paths = set(source_paths)
        imported_paths = {
            imported
            for source in source_paths
            for imported in local_static_js_imports(source)
            if imported not in owned_paths
        }
        declared_paths = {
            validate_repository_path(value) for value in component.get("depends_on", [])
        }
        assert declared_paths == imported_paths, (
            f"Frontend module dependency mismatch for {component_id}: "
            f"declared={sorted(str(path) for path in declared_paths)}, "
            f"imported={sorted(str(path) for path in imported_paths)}"
        )


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


def test_dependency_graph_is_current_and_dependencies_are_declared() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "backend/scripts/generate_dependency_graph.py",
            "--check",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
