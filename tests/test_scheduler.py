"""Unit tests for the tiny Kubernetes-like scheduler.

Run with:  python3 -m unittest discover -s tests -v
       or: python3 -m pytest tests
"""

import os
import sys
import unittest

# Make sure `scheduler` (the package at the repo root) is importable
# regardless of the current working directory or test runner used.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduler import Node, Pod, Scheduler  # noqa: E402


class SchedulingBasicsTests(unittest.TestCase):
    def test_pod_schedules_onto_node_with_sufficient_capacity(self):
        node = Node("n1", cpu_capacity=4, memory_capacity=8)
        scheduler = Scheduler([node], strategy="first-fit")
        pod = Pod("p1", cpu_request=2, memory_request=4)

        placed = scheduler.schedule_pod(pod)

        self.assertIs(placed, node)
        self.assertEqual(pod.node_name, "n1")
        self.assertIn("p1", node.pods)
        self.assertEqual(node.cpu_used, 2)
        self.assertEqual(node.memory_used, 4)
        self.assertEqual(scheduler.pending, [])

    def test_pod_stays_pending_when_no_node_has_capacity(self):
        node = Node("n1", cpu_capacity=2, memory_capacity=2)
        scheduler = Scheduler([node], strategy="first-fit")
        pod = Pod("too-big", cpu_request=10, memory_request=10)

        placed = scheduler.schedule_pod(pod)

        self.assertIsNone(placed)
        self.assertTrue(pod.is_pending)
        self.assertIn(pod, scheduler.pending)
        self.assertEqual(node.cpu_used, 0)
        self.assertEqual(node.memory_used, 0)

    def test_exact_capacity_fit_is_allowed(self):
        # Boundary check: a pod that exactly consumes remaining capacity
        # should still be schedulable (no off-by-one over-strictness).
        node = Node("n1", cpu_capacity=4, memory_capacity=8)
        scheduler = Scheduler([node], strategy="first-fit")
        pod = Pod("exact", cpu_request=4, memory_request=8)

        placed = scheduler.schedule_pod(pod)

        self.assertIs(placed, node)
        self.assertEqual(node.cpu_free, 0)
        self.assertEqual(node.memory_free, 0)


class NodeSelectorTests(unittest.TestCase):
    def test_node_selector_restricts_placement_to_matching_labels(self):
        gpu_node = Node("gpu-1", cpu_capacity=8, memory_capacity=16, labels={"tier": "gpu"})
        general_node = Node("gen-1", cpu_capacity=8, memory_capacity=16, labels={"tier": "general"})
        # general_node listed first so a selector-blind scheduler would
        # wrongly pick it under first-fit.
        scheduler = Scheduler([general_node, gpu_node], strategy="first-fit")
        pod = Pod("needs-gpu", cpu_request=2, memory_request=4, node_selector={"tier": "gpu"})

        placed = scheduler.schedule_pod(pod)

        self.assertIs(placed, gpu_node)
        self.assertNotIn("needs-gpu", general_node.pods)

    def test_pod_stays_pending_if_no_node_matches_selector(self):
        general_node = Node("gen-1", cpu_capacity=8, memory_capacity=16, labels={"tier": "general"})
        scheduler = Scheduler([general_node], strategy="first-fit", enable_preemption=False)
        pod = Pod("needs-gpu", cpu_request=1, memory_request=1, node_selector={"tier": "gpu"})

        placed = scheduler.schedule_pod(pod)

        self.assertIsNone(placed)
        self.assertIn(pod, scheduler.pending)


class StrategyComparisonTests(unittest.TestCase):
    def test_first_fit_and_best_fit_choose_different_nodes(self):
        # node-A has lots of slack; node-B is a tight fit for this pod.
        # first-fit should take the first node in insertion order that
        # fits (node-A). best-fit should choose the node that leaves the
        # least slack behind (node-B), packing tightly.
        def build_nodes():
            return [
                Node("node-A", cpu_capacity=10, memory_capacity=10),
                Node("node-B", cpu_capacity=4, memory_capacity=4),
            ]

        pod_for_ff = Pod("p", cpu_request=3, memory_request=3)
        ff_scheduler = Scheduler(build_nodes(), strategy="first-fit")
        ff_node = ff_scheduler.schedule_pod(pod_for_ff)

        pod_for_bf = Pod("p", cpu_request=3, memory_request=3)
        bf_scheduler = Scheduler(build_nodes(), strategy="best-fit")
        bf_node = bf_scheduler.schedule_pod(pod_for_bf)

        self.assertEqual(ff_node.name, "node-A")
        self.assertEqual(bf_node.name, "node-B")
        self.assertNotEqual(ff_node.name, bf_node.name)

        # Both placements must still respect capacity.
        self.assertLessEqual(ff_node.cpu_used, ff_node.cpu_capacity)
        self.assertLessEqual(bf_node.cpu_used, bf_node.cpu_capacity)


class ResourceLimitTests(unittest.TestCase):
    def test_resource_limits_never_exceeded_across_many_pods(self):
        nodes = [
            Node("n1", cpu_capacity=6, memory_capacity=12),
            Node("n2", cpu_capacity=4, memory_capacity=8),
            Node("n3", cpu_capacity=8, memory_capacity=8),
        ]
        scheduler = Scheduler(nodes, strategy="best-fit", enable_preemption=False)
        pods = [Pod(f"pod-{i}", cpu_request=(i % 3) + 1, memory_request=((i % 4) + 1) * 2) for i in range(30)]

        scheduler.schedule_all(pods)

        for node in scheduler.nodes.values():
            self.assertLessEqual(node.cpu_used, node.cpu_capacity)
            self.assertLessEqual(node.memory_used, node.memory_capacity)
            # cross-check against the underlying pods too
            self.assertEqual(node.cpu_used, sum(p.cpu_request for p in node.pods.values()))
            self.assertEqual(node.memory_used, sum(p.memory_request for p in node.pods.values()))


class NodeFailureTests(unittest.TestCase):
    def test_node_failure_reschedules_its_pods_onto_healthy_nodes(self):
        n1 = Node("n1", cpu_capacity=4, memory_capacity=8)
        n2 = Node("n2", cpu_capacity=4, memory_capacity=8)
        scheduler = Scheduler([n1, n2], strategy="first-fit")
        pod = Pod("victim", cpu_request=2, memory_request=4)

        scheduler.schedule_pod(pod)
        self.assertEqual(pod.node_name, "n1")

        results = scheduler.fail_node("n1")

        self.assertFalse(n1.healthy)
        self.assertNotIn("victim", n1.pods)
        self.assertEqual(results["victim"], "n2")
        self.assertEqual(pod.node_name, "n2")
        self.assertIn("victim", n2.pods)

    def test_node_failure_leaves_pod_pending_if_no_capacity_elsewhere(self):
        n1 = Node("n1", cpu_capacity=4, memory_capacity=8)
        n2 = Node("n2", cpu_capacity=1, memory_capacity=1)  # too small
        scheduler = Scheduler([n1, n2], strategy="first-fit", enable_preemption=False)
        pod = Pod("stuck", cpu_request=3, memory_request=6)
        scheduler.schedule_pod(pod)

        results = scheduler.fail_node("n1")

        self.assertIsNone(results["stuck"])
        self.assertTrue(pod.is_pending)
        self.assertIn(pod, scheduler.pending)


class PreemptionTests(unittest.TestCase):
    def test_preemption_evicts_lower_priority_pod_to_make_room(self):
        node = Node("solo", cpu_capacity=10, memory_capacity=10)
        scheduler = Scheduler([node], strategy="first-fit", enable_preemption=True)

        low_pod = Pod("low", cpu_request=8, memory_request=8, priority=1)
        scheduler.schedule_pod(low_pod)
        self.assertEqual(low_pod.node_name, "solo")

        high_pod = Pod("high", cpu_request=5, memory_request=5, priority=10)
        placed = scheduler.schedule_pod(high_pod)

        self.assertIs(placed, node)
        self.assertEqual(high_pod.node_name, "solo")
        self.assertTrue(low_pod.is_pending)
        self.assertIn(low_pod, scheduler.pending)
        self.assertNotIn("low", node.pods)
        self.assertIn("high", node.pods)
        # capacity must still hold after eviction + placement
        self.assertLessEqual(node.cpu_used, node.cpu_capacity)
        self.assertLessEqual(node.memory_used, node.memory_capacity)

    def test_preemption_disabled_leaves_higher_priority_pod_pending(self):
        node = Node("solo", cpu_capacity=10, memory_capacity=10)
        scheduler = Scheduler([node], strategy="first-fit", enable_preemption=False)

        low_pod = Pod("low", cpu_request=8, memory_request=8, priority=1)
        scheduler.schedule_pod(low_pod)

        high_pod = Pod("high", cpu_request=5, memory_request=5, priority=10)
        placed = scheduler.schedule_pod(high_pod)

        self.assertIsNone(placed)
        self.assertTrue(high_pod.is_pending)
        self.assertIn("low", node.pods)  # untouched


if __name__ == "__main__":
    unittest.main()
