from __future__ import annotations

import ast
import inspect


def _function_source(module, function_name: str) -> str:
    source = inspect.getsource(module)
    lines = source.splitlines()
    tree = ast.parse(source)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"top-level function not found: {function_name}")


def _nested_function_source(parent_source: str, function_name: str) -> str:
    lines = parent_source.splitlines()
    tree = ast.parse(parent_source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"nested function not found: {function_name}")


def test_ingest_telemetry_loop_references_partition_top_up():
    import pmfi.cli as cli

    cmd_ingest_src = _function_source(cli, "cmd_ingest")
    telemetry_src = _nested_function_source(cmd_ingest_src, "_telemetry_loop")
    assert "ensure_current_partitions as _ensure_partitions" in cmd_ingest_src
    assert "ensure_partitions=_ensure_partitions" in telemetry_src


def test_live_smoke_persist_path_references_partition_top_up():
    import pmfi.commands.ingest as ingest

    cmd_live_smoke_src = _function_source(ingest, "cmd_live_smoke")
    assert "ensure_current_partitions" in cmd_live_smoke_src


def test_cmd_live_references_partition_top_up_at_startup():
    import pmfi.commands.ingest as ingest

    cmd_live_src = _function_source(ingest, "cmd_live")
    assert "ensure_current_partitions" in cmd_live_src
