"""Enforce the small source registry and native execution ownership."""

import ast
from pathlib import Path


def test_declared_module_inventory_and_dependencies() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "src" / "inspect_labs"
    assert (source / "py.typed").is_file()
    allowed = {
        "__init__": {
            "inspect_labs.actions",
            "inspect_labs.bindings",
            "inspect_labs.conformance",
            "inspect_labs.monitors",
        },
        "conformance": {"inspect_ai", "pydantic", "anyio", "inspect_labs.bindings", "inspect_labs"},
        "plugins": set(),
        "devices": set(),
        "errors": set(),
        "spec": {"pydantic"},
        "actions": {"pydantic", "inspect_labs.spec"},
        "monitors": {"pydantic", "inspect_labs.actions"},
        "gateway": {"pydantic", "inspect_labs.actions", "inspect_labs.spec"},
        "serve": {
            "inspect_ai",
            "pydantic",
            "mcp",
            "inspect_labs.actions",
            "inspect_labs.bindings",
            "inspect_labs.gateway",
            "inspect_labs.monitors",
        },
        "liquid": {"pydantic"},
        "liquid_handling": {
            "inspect_ai",
            "pydantic",
            "anyio",
            "pylabrobot",
            "inspect_labs.bindings",
            "inspect_labs.errors",
            "inspect_labs.liquid",
            "inspect_labs.spec",
            "inspect_labs.devices",
            "inspect_labs.plugins",
        },
        "liquid_tasks": {
            "inspect_ai",
            "pydantic",
            "inspect_labs.bindings",
            "inspect_labs.liquid",
            "inspect_labs.liquid_handling",
            "inspect_labs.spec",
            "inspect_labs.tasks",
            "inspect_labs.plugins",
        },
        "registry": {"inspect_labs"},
        "secure_lab": {
            "inspect_ai",
            "inspect_labs.actions",
            "inspect_labs.bindings",
            "inspect_labs.gateway",
            "inspect_labs.liquid_tasks",
            "inspect_labs.monitors",
        },
        "robot_bridge": {
            "inspect_ai",
            "inspect_robots",
            "anyio",
            "pydantic",
            "inspect_labs.bindings",
            "inspect_labs.devices",
            "inspect_labs.errors",
        },
        "litmus_labs": {"pydantic"},
        "native": {"inspect_ai", "pydantic", "inspect_labs.litmus_labs"},
        "evidence": {"inspect_ai", "pydantic", "inspect_labs.litmus_labs", "inspect_labs.native"},
        "cli": {
            "inspect_ai",
            "inspect_labs.evidence",
            "inspect_labs.litmus_labs",
            "inspect_labs.native",
            "inspect_labs.robot_mock",
            "inspect_labs.bindings",
            "inspect_labs.tasks",
            "inspect_labs.liquid_tasks",
            "inspect_labs.conformance",
            "inspect_labs.plugins",
            "inspect_labs.actions",
            "inspect_labs.monitors",
            "inspect_labs.gateway",
            "inspect_labs.serve",
            "pydantic",
            "anyio",
        },
        "robot_mock": {"inspect_robots"},
        "bindings": {
            "inspect_ai",
            "pydantic",
            "anyio",
            "inspect_labs.actions",
            "inspect_labs.errors",
            "inspect_labs.gateway",
            "inspect_labs.monitors",
            "inspect_labs.spec",
        },
        "environments": {
            "inspect_ai",
            "pydantic",
            "inspect_labs.bindings",
            "inspect_labs.litmus_labs",
            "inspect_labs.native",
        },
        "tasks": {
            "inspect_ai",
            "inspect_labs.bindings",
            "inspect_labs.environments",
            "inspect_labs.litmus_labs",
            "inspect_labs.robot_workflow",
            "inspect_labs.robot_bridge",
        },
        "robot_workflow": {
            "inspect_ai",
            "inspect_robots",
            "anyio",
            "pydantic",
            "inspect_labs.bindings",
            "inspect_labs.environments",
        },
    }
    assert {path.stem for path in source.glob("*.py")} == set(allowed)
    registry = (root / "docs" / "design.md").read_text()
    import sys

    for path in source.glob("*.py"):
        assert f"src/inspect_labs/{path.name}" in registry
        for node in ast.walk(ast.parse(path.read_text())):
            modules = (
                [node.module]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for module in modules:
                assert module is not None
                prefix = module.split(".")[0]
                if prefix in sys.stdlib_module_names or prefix == "__future__":
                    continue
                dependency = module if prefix == "inspect_labs" else prefix
                assert dependency in allowed[path.stem], (path.name, module)
                if prefix in {"inspect_ai", "inspect_robots"}:
                    assert all(not part.startswith("_") for part in module.split("."))


def test_optional_backends_are_imported_lazily() -> None:
    """The task registry must import without optional extras installed."""
    import subprocess
    import sys

    source = Path(__file__).resolve().parents[1] / "src" / "inspect_labs"
    optional = {
        "pylabrobot",
        "inspect_robots",
        "inspect_labs.liquid_handling",
        "inspect_labs.robot_workflow",
        "inspect_labs.robot_bridge",
    }
    for name in ("liquid_tasks", "tasks", "registry", "cli", "__init__"):
        tree = ast.parse((source / f"{name}.py").read_text())
        for node in tree.body:  # Module level only; function-level imports are lazy.
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for module in modules:
                assert not any(module == o or module.startswith(o + ".") for o in optional), (
                    name,
                    module,
                )
    blocker = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('pylabrobot', 'inspect_robots'):\n"
        "            raise ImportError('blocked ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        "import inspect_labs.registry, inspect_labs.cli\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", blocker], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip().endswith("ok"), result.stderr
