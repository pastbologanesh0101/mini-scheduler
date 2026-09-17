"""The Scheduler: assigns pods to nodes using a chosen bin-packing
strategy, honoring node selectors and resource limits, with optional
priority preemption and node-failure rescheduling.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .models import Node, Pod

FIRST_FIT = "first-fit"
BEST_FIT = "best-fit"
STRATEGIES = (FIRST_FIT, BEST_FIT)


class InsufficientResourcesError(Exception):
    """Raised by strict helpers when a pod truly cannot be placed."""


class Scheduler:
    """A simplified cluster scheduler.

    Args:
        nodes: initial list of Node objects.
        strategy: "first-fit" (place on the first node, in insertion
            order, that has room) or "best-fit" (place on the node that
            will be left with the least free capacity after placement,
            i.e. the tightest fit / most-requested node).
        enable_preemption: if True, a pod that can't be placed may evict
            lower-priority pods from a node it would otherwise fit on.
    """

    def __init__(
        self,
        nodes: Iterable[Node],
        strategy: str = FIRST_FIT,
        enable_preemption: bool = True,
    ) -> None:
        if strategy not in STRATEGIES:
            raise ValueError(f"unknown strategy {strategy!r}, expected one of {STRATEGIES}")
        self.nodes: Dict[str, Node] = {n.name: n for n in nodes}
        self.strategy = strategy
        self.enable_preemption = enable_preemption
        self.pending: List[Pod] = []

    # -- node bookkeeping -------------------------------------------------

    def add_node(self, node: Node) -> None:
        self.nodes[node.name] = node

    def healthy_nodes(self) -> List[Node]:
        return [n for n in self.nodes.values() if n.healthy]

    # -- placement selection ------------------------------------------------

    def _select_node(self, pod: Pod) -> Optional[Node]:
        candidates = [n for n in self.healthy_nodes() if n.can_fit(pod)]
        if not candidates:
            return None
        if self.strategy == FIRST_FIT:
            # `self.nodes` preserves insertion order (dict semantics),
            # so this is the first node added that has room.
            return candidates[0]
        # BEST_FIT: minimize leftover capacity after placement (tightest
        # fit), i.e. prefer the most-utilized node that still fits.
        return min(
            candidates,
            key=lambda n: (n.cpu_free - pod.cpu_request) + (n.memory_free - pod.memory_request),
        )

    # -- scheduling ---------------------------------------------------------

    def schedule_pod(self, pod: Pod) -> Optional[Node]:
        """Try to place a single pod. Returns the Node it landed on, or
        None if it had to be left/added to the pending queue."""
        node = self._select_node(pod)
        if node is not None:
            node.add_pod(pod)
            self._unqueue(pod)
            return node

        if self.enable_preemption:
            node = self._try_preempt(pod)
            if node is not None:
                self._unqueue(pod)
                return node

        if pod not in self.pending:
            self.pending.append(pod)
        pod.node_name = None
        return None

    def schedule_all(self, pods: Iterable[Pod]) -> Dict[str, Optional[str]]:
        """Schedule a batch of pods, highest priority first. Returns a
        dict of pod name -> node name (or None if left pending)."""
        queue = sorted(pods, key=lambda p: -p.priority)
        results: Dict[str, Optional[str]] = {}
        for pod in queue:
            node = self.schedule_pod(pod)
            results[pod.name] = node.name if node else None
        return results

    def schedule_pending(self) -> Dict[str, Optional[str]]:
        """Re-attempt scheduling of everything currently pending (e.g.
        after a node recovers or new capacity was added)."""
        queue = self.pending
        self.pending = []
        return self.schedule_all(queue)

    def _unqueue(self, pod: Pod) -> None:
        if pod in self.pending:
            self.pending.remove(pod)

    # -- preemption -----------------------------------------------------

    def _try_preempt(self, pod: Pod) -> Optional[Node]:
        """Look for a healthy, selector-matching node where evicting some
        set of strictly-lower-priority pods would free enough room for
        `pod`. Among feasible nodes, prefer the one requiring the fewest
        evictions. Returns the node the pod was placed on, or None."""
        best_node: Optional[Node] = None
        best_victims: Optional[List[Pod]] = None

        for node in self.healthy_nodes():
            if not node.matches_selector(pod.node_selector):
                continue
            # If even evicting *every* lower-priority pod wouldn't be
            # enough, this node can never work -- skip early.
            lower_priority = sorted(
                (p for p in node.pods.values() if p.priority < pod.priority),
                key=lambda p: p.priority,
            )

            freed_cpu = 0
            freed_mem = 0
            victims: List[Pod] = []
            for victim in lower_priority:
                if (
                    node.cpu_free + freed_cpu >= pod.cpu_request
                    and node.memory_free + freed_mem >= pod.memory_request
                ):
                    break
                freed_cpu += victim.cpu_request
                freed_mem += victim.memory_request
                victims.append(victim)

            fits = (
                node.cpu_free + freed_cpu >= pod.cpu_request
                and node.memory_free + freed_mem >= pod.memory_request
            )
            if not fits:
                continue

            if best_victims is None or len(victims) < len(best_victims):
                best_node = node
                best_victims = victims

        if best_node is None or best_victims is None:
            return None

        for victim in best_victims:
            best_node.remove_pod(victim.name)
            if victim not in self.pending:
                self.pending.append(victim)

        best_node.add_pod(pod)
        return best_node

    # -- failure simulation -----------------------------------------------

    def fail_node(self, node_name: str) -> Dict[str, Optional[str]]:
        """Mark a node as down and attempt to reschedule everything that
        was running on it onto the remaining healthy nodes. Pods that
        don't fit anywhere are left in `self.pending`."""
        node = self.nodes.get(node_name)
        if node is None:
            raise KeyError(f"no such node: {node_name!r}")

        node.healthy = False
        evicted = list(node.pods.values())
        node.pods = {}
        for pod in evicted:
            pod.node_name = None

        self.pending.extend(evicted)
        return self.schedule_pending()

    def recover_node(self, node_name: str) -> Dict[str, Optional[str]]:
        """Mark a previously-failed node healthy again and try to drain
        the pending queue onto it (and any other healthy node)."""
        node = self.nodes.get(node_name)
        if node is None:
            raise KeyError(f"no such node: {node_name!r}")
        node.healthy = True
        return self.schedule_pending()

    # -- introspection --------------------------------------------------

    def placement(self) -> Dict[str, Optional[str]]:
        """Snapshot of pod name -> node name for every pod currently
        placed on a node (pending pods are omitted; see `self.pending`)."""
        result: Dict[str, Optional[str]] = {}
        for node in self.nodes.values():
            for pod_name in node.pods:
                result[pod_name] = node.name
        return result
