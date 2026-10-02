"""Keep the plugin separate and execution owned by native Inspect."""

import ast
import sys
from pathlib import Path


def test_plugin_inventory_and_dependency_direction():
    plugin = Path(__file__).resolve().parents[1]
    source = plugin / "src" / "inspect_labs_commec"
    allowed = {
        "__init__": {"inspect_labs_commec.report"},
        "report": {"pydantic", "yaml"},
        "environment": {
            "inspect_ai",
            "inspect_labs.bindings",
            "inspect_labs_commec.report",
            "pydantic",
            "anyio",
        },
        "tasks": {
            "inspect_ai",
            "inspect_labs.bindings",
            "inspect_labs.tasks",
            "inspect_labs_commec.environment",
            "inspect_labs_commec.report",
        },
        "provision": {"inspect_labs.bindings", "inspect_labs_commec.environment"},
        "rescore": {"inspect_labs.bindings", "inspect_labs_commec.tasks"},
    }
    assert {p.stem for p in source.glob("*.py")} == set(allowed)
    assert (source / "py.typed").is_file()
    registry = (plugin.parents[1] / "docs" / "design.md").read_text()
    for path in source.glob("*.py"):
        assert f"inspect_labs_commec/{path.name}" in registry
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
                assert prefix not in {"subprocess", "commec", "requests", "httpx"}
                if prefix in sys.stdlib_module_names:
                    continue
                dependency = module if prefix in {"inspect_labs", "inspect_labs_commec"} else prefix
                assert dependency in allowed[path.stem], (path.name, module)
                if prefix == "inspect_ai":
                    assert all(not p.startswith("_") for p in module.split("."))
