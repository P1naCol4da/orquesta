# Copyright 2026
# SPDX-License-Identifier: Apache-2.0
"""G19: source-level behavioral parity checks for transient rerun indexing.

This test imports installed Orquesta and exercises native WorkflowState and
WorkflowConductor methods. It deliberately does NOT assert any speed threshold.
"""
import queue
import random
import unittest

from orquesta import conducting, constants


def historical_descendants(state, task_id, route):
    """Reference implementation copied in behavior from PR #283 head."""
    index = state.tasks[constants.TASK_STATE_ROUTE_FORMAT % (task_id, str(route))]
    result = [(index, state.sequence[index])]
    visited = {index}
    pending = queue.Queue()
    pending.put((task_id, route))
    while not pending.empty():
        current_id, current_route = pending.get()
        for i, entry in enumerate(state.sequence):
            for parent_index in entry["prev"].values():
                parent = state.sequence[parent_index]
                if (
                    parent["id"] == current_id
                    and parent["route"] == current_route
                    and i not in visited
                ):
                    visited.add(i)
                    result.append((i, entry))
                    pending.put((entry["id"], entry["route"]))
    return result


def historical_collapse(state, requests):
    result = {
        key: [idx for idx, _ in historical_descendants(state, request[0], request[1])]
        for key, request in requests.items()
    }
    if len(requests) > 1:
        result = {
            key: indices
            for key, indices in result.items()
            if not any(
                other_key != key and set(indices) < set(other_indices)
                for other_key, other_indices in result.items()
            )
        }
    return result


def make_state(seed, count):
    rng = random.Random(seed)
    state = conducting.WorkflowState()
    for i in range(count):
        name = "task_%s" % rng.randrange(9)
        route = rng.randrange(3)
        parent_ids = rng.sample(range(i), min(i, rng.randrange(3)))
        entry = {
            "id": name,
            "route": route,
            "prev": {"p%s" % j: p for j, p in enumerate(parent_ids)},
            "next": {},
            "ctxs": {"in": [0]},
            "status": "succeeded",
        }
        state.sequence.append(entry)
        state.tasks[constants.TASK_STATE_ROUTE_FORMAT % (name, str(route))] = i
    return state


class HistoryIndexParityTest(unittest.TestCase):
    def test_reference_reachability_and_request_collapse(self):
        for seed in range(32):
            state = make_state(seed, 5 + seed * 2)
            self.assertTrue(state.sequence)
            names = sorted((entry["id"], entry["route"]) for entry in state.sequence)
            names = list(dict.fromkeys(names))
            for name, route in names[:8]:
                actual = [i for i, _ in state.get_task_sequence(name, route)]
                reference = [
                    i for i, _ in historical_descendants(state, name, route)
                ]
                self.assertEqual(reference, actual)

            requests = {
                "%s__r%s" % (name, route): (name, route)
                for name, route in names[:7]
            }
            for count in (1, min(3, len(requests)), len(requests)):
                subset = dict(list(requests.items())[:count])
                objects = {
                    key: type("TaskRequest", (), {"task_id": name, "route": route})()
                    for key, (name, route) in subset.items()
                }
                conductor = object.__new__(conducting.WorkflowConductor)
                conductor._workflow_state = state
                self.assertEqual(
                    historical_collapse(state, subset),
                    conductor._collapse_task_rerun_requests(objects),
                )

    def test_index_not_stale_after_history_mutation(self):
        state = make_state(10, 8)
        name = state.sequence[2]["id"]
        route = state.sequence[2]["route"]
        _ = state.get_task_sequence(name, route)
        new_index = len(state.sequence)
        state.sequence.append(
            {"id": "extra", "route": 0, "prev": {"from": 2},
             "next": {}, "ctxs": {"in": [0]}, "status": "succeeded"}
        )
        state.tasks["extra__r0"] = new_index
        actual = [idx for idx, _ in state.get_task_sequence(name, route)]
        expected = [idx for idx, _ in historical_descendants(state, name, route)]
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
