# SPDX-License-Identifier: Apache-2.0
"""G19 genuine installed-Orquesta behavior parity and microbenchmark.

This file is intentionally isolated to the research branch. The reference
methods are compiled from the exact PR #283 head commit via git show.
"""
import argparse
import ast
import json
import statistics
import subprocess
import time
from pathlib import Path

from orquesta import conducting
from orquesta import requests as rerun_requests

BASE_SHA = "234233e1ca3a55a068cfac31adf95c4acdba7959"


def reference_methods():
    raw = subprocess.check_output(
        ["git", "show", BASE_SHA + ":orquesta/conducting.py"], text=True
    )
    tree = ast.parse(raw)
    wanted = {
        "WorkflowState": {"get_task_sequence"},
        "WorkflowConductor": {"_collapse_task_rerun_requests"},
    }
    namespace = dict(vars(conducting))
    result = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in wanted:
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if method.name in wanted[node.name]:
                        single = ast.fix_missing_locations(
                            ast.Module(body=[method], type_ignores=[])
                        )
                        exec(compile(single, "PR283_REFERENCE", "exec"), namespace)
                        result[(node.name, method.name)] = namespace[method.name]
    assert len(result) == 2, "Unable to extract pinned PR methods"
    return result


def make_state(kind, length):
    state = conducting.WorkflowState()
    for i in range(length):
        if kind == "chain":
            task_id, route = "t_%s" % i, 0
            parents = [] if i == 0 else [i - 1]
        elif kind == "repeat":
            task_id, route = "t_%s" % (i % 7), 0
            parents = [] if i == 0 else [i - 1]
        else:
            task_id, route = "t_%s" % (i % 9), i % 2
            parents = [] if i < 2 else [i - 2]
        entry = {
            "id": task_id,
            "route": route,
            "prev": {"parent_%s" % x: x for x in parents},
            "next": {},
            "ctxs": {"in": [0]},
            "status": "succeeded",
        }
        state.sequence.append(entry)
        state.tasks["%s__r%s" % (task_id, route)] = i
    return state


def requests_for(state, count):
    keys = sorted(state.tasks)
    if count >= len(keys):
        chosen = keys
    elif count == 1:
        chosen = keys[:1]
    else:
        chosen = [
            keys[(j * (len(keys) - 1)) // (count - 1)]
            for j in range(count)
        ]
    result = {}
    for key in chosen:
        task_id, route = key.rsplit("__r", 1)
        request = rerun_requests.TaskRerunRequest.new(task_id, route=int(route))
        result[request.task_state_entry_id] = request
    return result


def compare_one(kind, length, count, reference):
    state = make_state(kind, length)
    tasks = requests_for(state, count)
    conductor = object.__new__(conducting.WorkflowConductor)
    conductor._workflow_state = state
    patched_get = conducting.WorkflowState.get_task_sequence
    patched_collapse = conducting.WorkflowConductor._collapse_task_rerun_requests
    ref_get = reference[("WorkflowState", "get_task_sequence")]
    ref_collapse = reference[("WorkflowConductor", "_collapse_task_rerun_requests")]

    def run_with(get_method, collapse_method):
        conducting.WorkflowState.get_task_sequence = get_method
        conducting.WorkflowConductor._collapse_task_rerun_requests = collapse_method
        try:
            closure = {
                k: [i for i, _ in state.get_task_sequence(r.task_id, r.route)]
                for k, r in tasks.items()
            }
            collapsed = conductor._collapse_task_rerun_requests(tasks)
            return {"closure": closure, "collapsed": collapsed}
        finally:
            conducting.WorkflowState.get_task_sequence = patched_get
            conducting.WorkflowConductor._collapse_task_rerun_requests = patched_collapse

    baseline = run_with(ref_get, ref_collapse)
    patched = run_with(patched_get, patched_collapse)
    assert baseline == patched, "Output mismatch for %s %s %s" % (
        kind, length, count
    )

    def benchmark(get_method, collapse_method):
        samples = []
        for _ in range(7):
            start = time.perf_counter_ns()
            run_with(get_method, collapse_method)
            samples.append((time.perf_counter_ns() - start) / 1000000.0)
        return round(statistics.median(samples), 6)

    old_ms = benchmark(ref_get, ref_collapse)
    new_ms = benchmark(patched_get, patched_collapse)
    return {
        "kind": kind,
        "history_length": length,
        "requests": len(tasks),
        "output_equal": True,
        "reference_ms": old_ms,
        "patched_ms": new_ms,
        "speedup": round(old_ms / max(new_ms, 0.000001), 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="results/g19_native_ci.json")
    args = parser.parse_args()
    original = reference_methods()
    results = [
        compare_one(kind, length, count, original)
        for kind in ("chain", "repeat", "branch")
        for length in (6, 12, 24, 48, 96, 192)
        for count in (1, 3, 6)
    ]
    doc = {
        "status": "PASS",
        "installed_module": str(Path(conducting.__file__).resolve()),
        "reference_commit": BASE_SHA,
        "cases": len(results),
        "mismatches": 0,
        "faster_cases": sum(x["speedup"] > 1 for x in results),
        "median_speedup": statistics.median(x["speedup"] for x in results),
        "note": "Native installed classes and methods, microbenchmark only",
        "rows": results,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(doc, indent=2))
    print(json.dumps({k: v for k, v in doc.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
