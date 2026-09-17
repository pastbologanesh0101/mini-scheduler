"""Core data models: Node and Pod."""

from __future__ import annotations

from typing import Dict, Optional


class Pod:
    """A workload that requests some CPU/memory and (optionally) requires
    that the node it lands on carries certain labels.

    Attributes:
        name: unique pod name.
        cpu_request: CPU units requested (arbitrary unit, e.g. millicores).
        memory_request: memory units requested (arbitrary unit, e.g. MB).
        node_selector: dict of label key/value pairs the target node must
            have (all must match). Empty dict means "any node".
        priority: higher runs first / can preempt lower-priority pods.
        node_name: name of the node this pod is currently placed on, or
            None if the pod is pending (unscheduled).
    """

    def __init__(
        self,
        name: str,
        cpu_request: int,
        memory_request: int,
        node_selector: Optional[Dict[str, str]] = None,
        priority: int = 0,
    ) -> None:
        if cpu_request < 0 or memory_request < 0:
            raise ValueError("resource requests must be non-negative")
        self.name = name
        self.cpu_request = cpu_request
        self.memory_request = memory_request
        self.node_selector: Dict[str, str] = dict(node_selector or {})
        self.priority = priority
        self.node_name: Optional[str] = None

    @property
    def is_pending(self) -> bool:
        return self.node_name is None

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"Pod(name={self.name!r}, cpu={self.cpu_request}, mem={self.memory_request}, "
            f"priority={self.priority}, node={self.node_name!r})"
        )


class Node:
    """A cluster node with fixed CPU/memory capacity and a set of labels.

    Resource usage is derived from the pods currently scheduled on the
    node (`self.pods`), so capacity can never silently drift out of sync
    with what's actually placed.
    """

    def __init__(
        self,
        name: str,
        cpu_capacity: int,
        memory_capacity: int,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        if cpu_capacity < 0 or memory_capacity < 0:
            raise ValueError("capacities must be non-negative")
        self.name = name
        self.cpu_capacity = cpu_capacity
        self.memory_capacity = memory_capacity
        self.labels: Dict[str, str] = dict(labels or {})
        self.healthy = True
        self.pods: Dict[str, Pod] = {}

    @property
    def cpu_used(self) -> int:
        return sum(p.cpu_request for p in self.pods.values())

    @property
    def memory_used(self) -> int:
        return sum(p.memory_request for p in self.pods.values())

    @property
    def cpu_free(self) -> int:
        return self.cpu_capacity - self.cpu_used

    @property
    def memory_free(self) -> int:
        return self.memory_capacity - self.memory_used

    def matches_selector(self, selector: Dict[str, str]) -> bool:
        """True if this node's labels satisfy every key/value in selector."""
        return all(self.labels.get(k) == v for k, v in selector.items())

    def can_fit(self, pod: Pod) -> bool:
        """True if the node is healthy, matches the pod's selector, and has
        enough free CPU/memory to accommodate the pod right now."""
        return (
            self.healthy
            and self.matches_selector(pod.node_selector)
            and self.cpu_free >= pod.cpu_request
            and self.memory_free >= pod.memory_request
        )

    def add_pod(self, pod: Pod) -> None:
        """Place a pod on this node without checking capacity. Callers are
        expected to have already verified `can_fit`, but we still guard
        against accidental over-commit as a safety net."""
        if pod.cpu_request > self.cpu_free or pod.memory_request > self.memory_free:
            raise ValueError(
                f"cannot place pod {pod.name!r} on node {self.name!r}: "
                f"would exceed capacity"
            )
        self.pods[pod.name] = pod
        pod.node_name = self.name

    def remove_pod(self, pod_name: str) -> Optional[Pod]:
        pod = self.pods.pop(pod_name, None)
        if pod is not None:
            pod.node_name = None
        return pod

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"Node(name={self.name!r}, cpu={self.cpu_used}/{self.cpu_capacity}, "
            f"mem={self.memory_used}/{self.memory_capacity}, healthy={self.healthy}, "
            f"labels={self.labels})"
        )
