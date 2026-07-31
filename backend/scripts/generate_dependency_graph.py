"""Validate module-map ownership and generate its deterministic Mermaid graph."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODULE_MAP_PATH = REPOSITORY_ROOT / "backend" / "module-map.json"
OUTPUT_PATH = REPOSITORY_ROOT / "backend" / "DEPENDENCY_GRAPH.md"


class ModuleMapError(ValueError):
    """Raised when the navigation map is ambiguous, stale, or incomplete."""


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModuleMapError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_module_map() -> dict[str, Any]:
    return json.loads(
        MODULE_MAP_PATH.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicates,
    )


def components(module_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {
        f"infrastructure:{name}": value
        for name, value in module_map.get("infrastructure", {}).items()
    }
    result.update({f"feature:{name}": value for name, value in module_map["features"].items()})
    return result


def referenced_paths(module_map: dict[str, Any]) -> list[str]:
    application = module_map["application"]
    result = [application["entrypoint"], application["factory"], application["api_router"]]
    for component in components(module_map).values():
        result.append(component["entrypoint"])
        for field in ("contracts", "implementation", "depends_on", "tests"):
            result.extend(component.get(field, []))
    return result


def resolve_path(value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ModuleMapError(f"Invalid module-map path: {value!r}")
    posix = PurePosixPath(value)
    if (
        posix.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or ".." in posix.parts
        or posix.as_posix() != value
    ):
        raise ModuleMapError(f"Path is not repo-relative POSIX: {value}")
    resolved = (REPOSITORY_ROOT / Path(*posix.parts)).resolve()
    try:
        resolved.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError as error:
        raise ModuleMapError(f"Path escapes repository root: {value}") from error
    if not resolved.is_file():
        raise ModuleMapError(f"Referenced file does not exist: {value}")
    return resolved


def _component_for_module(module: str) -> str | None:
    if module == "app.storage" or module.startswith("app.storage."):
        return "infrastructure:storage"
    if module.startswith("app.features."):
        parts = module.split(".")
        if len(parts) >= 3:
            return f"feature:{parts[2]}"
    return None


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(name.name for name in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def validate_dependencies(module_map: dict[str, Any]) -> None:
    owner_by_path: dict[Path, str] = {}
    all_components = components(module_map)
    for component_id, component in all_components.items():
        for field in ("entrypoint", "contracts", "implementation"):
            values = [component[field]] if field == "entrypoint" else component.get(field, [])
            for value in values:
                owner_by_path[resolve_path(value)] = component_id

    for component_id, component in all_components.items():
        declared = {
            target
            for dependency in component.get("depends_on", [])
            if (target := owner_by_path.get(resolve_path(dependency))) is not None
        }
        source_paths = [component["entrypoint"], *component.get("contracts", [])]
        source_paths.extend(component.get("implementation", []))
        for source in source_paths:
            for imported in _imports(resolve_path(source)):
                target = _component_for_module(imported)
                if target is None or target == component_id:
                    continue
                if target not in all_components:
                    raise ModuleMapError(f"{source} imports unknown component {target}")
                if target not in declared:
                    raise ModuleMapError(f"{source} imports undeclared dependency {target}")


def render(module_map: dict[str, Any]) -> str:
    all_components = components(module_map)
    path_owner: dict[str, str] = {}
    for component_id, component in all_components.items():
        for field in ("entrypoint", "contracts", "implementation"):
            values = [component[field]] if field == "entrypoint" else component.get(field, [])
            for value in values:
                path_owner[value] = component_id

    lines = [
        "# Backend dependency graph",
        "",
        "Generated from `backend/module-map.json`. Do not edit by hand.",
        "",
        "```mermaid",
        "flowchart LR",
        '  application["application"]',
    ]
    node_names: dict[str, str] = {}
    for component_id in sorted(all_components):
        node = component_id.replace(":", "_").replace("-", "_")
        node_names[component_id] = node
        lines.append(f'  {node}["{component_id}"]')
    for component_id in sorted(all_components):
        if component_id.startswith("feature:"):
            lines.append(f"  application --> {node_names[component_id]}")
        targets = {
            path_owner[path]
            for path in all_components[component_id].get("depends_on", [])
            if path in path_owner and path_owner[path] != component_id
        }
        for target in sorted(targets):
            lines.append(f"  {node_names[component_id]} --> {node_names[target]}")
    lines.extend(["```", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    module_map = load_module_map()
    for path in referenced_paths(module_map):
        resolve_path(path)
    validate_dependencies(module_map)
    generated = render(module_map)
    if args.check:
        if not OUTPUT_PATH.is_file() or OUTPUT_PATH.read_text(encoding="utf-8") != generated:
            raise SystemExit("backend/DEPENDENCY_GRAPH.md is stale; regenerate it.")
        return 0
    OUTPUT_PATH.write_text(generated, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
