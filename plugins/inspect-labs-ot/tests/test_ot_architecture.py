"""Keep the OT plugin small, native and explicitly registered."""

import ast
import sys
from pathlib import Path


def test_source_inventory_and_dependency_direction():
    plugin = Path(__file__).resolve().parents[1]
    source = plugin / "src" / "inspect_labs_ot"
    allowed = {
        "__init__": {"inspect_labs_ot.lab", "inspect_labs_ot.tasks"},
        "plant": {"pydantic"},
        "lab": {
            "anyio",
            "inspect_ai",
            "pydantic",
            "inspect_labs.actions",
            "inspect_labs.bindings",
            "inspect_labs.spec",
            "inspect_labs_ot.plant",
        },
        "tasks": {
            "inspect_ai",
            "pydantic",
            "inspect_labs.actions",
            "inspect_labs.bindings",
            "inspect_labs.gateway",
            "inspect_labs.monitors",
            "inspect_labs.tasks",
            "inspect_labs_ot.lab",
        },
    }
    assert {p.stem for p in source.glob("*.py")} == set(allowed)
    assert (source / "py.typed").is_file()
    registry = (plugin.parents[1] / "docs" / "design.md").read_text()
    for path in source.glob("*.py"):
        assert f"inspect_labs_ot/{path.name}" in registry
        for node in ast.walk(ast.parse(path.read_text())):
            modules = (
                [node.module]
                if isinstance(node, ast.ImportFrom)
                else [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for module in modules:
                assert module is not None
                prefix = module.split(".")[0]
                if prefix in sys.stdlib_module_names:
                    continue
                dependency = module if prefix in {"inspect_labs", "inspect_labs_ot"} else prefix
                assert dependency in allowed[path.stem], (path.name, module)
                if prefix == "inspect_ai":
                    assert all(not part.startswith("_") for part in module.split("."))
