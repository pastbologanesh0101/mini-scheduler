"""Tiny Kubernetes-like Scheduler.

A small, fully in-process simulation of a cluster scheduler:
nodes, pods, bin-packing placement strategies, node-selector
constraints, priority preemption, and node-failure rescheduling.
"""

from .models import Node, Pod
from .scheduler import Scheduler, InsufficientResourcesError

__all__ = ["Node", "Pod", "Scheduler", "InsufficientResourcesError"]
