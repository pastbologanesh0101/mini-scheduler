#!/usr/bin/env python3
"""Demo: a small cluster scheduling a mix of pods, a node going down,
and pods being rescheduled onto the surviving nodes.

Run with:  python3 demo.py
"""

from scheduler import Node, Pod, Scheduler


def print_cluster(scheduler: Scheduler, title: str) -> None:
    print(f"\n=== {title} ===")
    for node in scheduler.nodes.values():
        status = "UP" if node.healthy else "DOWN"
        pods = ", ".join(sorted(node.pods)) or "(empty)"
        print(
            f"  {node.name:8s} [{status:4s}] "
            f"cpu {node.cpu_used:2d}/{node.cpu_capacity:2d}  "
            f"mem {node.memory_used:3d}/{node.memory_capacity:3d}  "
            f"labels={node.labels}  pods: {pods}"
        )
    pending = ", ".join(sorted(p.name for p in scheduler.pending)) or "(none)"
    print(f"  pending: {pending}")


def build_nodes():
    return [
        Node("node-a", cpu_capacity=8, memory_capacity=16, labels={"zone": "us-east", "tier": "general"}),
        Node("node-b", cpu_capacity=4, memory_capacity=8, labels={"zone": "us-east", "tier": "general"}),
        Node("node-c", cpu_capacity=16, memory_capacity=32, labels={"zone": "us-west", "tier": "gpu"}),
        Node("node-d", cpu_capacity=4, memory_capacity=8, labels={"zone": "us-west", "tier": "general"}),
        Node("node-e", cpu_capacity=8, memory_capacity=8, labels={"zone": "us-east", "tier": "gpu"}),
    ]


def build_pods():
    return [
        Pod("web-1", cpu_request=2, memory_request=4, priority=5),
        Pod("web-2", cpu_request=2, memory_request=4, priority=5),
        Pod("cache-1", cpu_request=1, memory_request=2, priority=3),
        Pod("batch-1", cpu_request=3, memory_request=6, priority=1),
        Pod("gpu-train", cpu_request=6, memory_request=12, node_selector={"tier": "gpu"}, priority=8),
        Pod("gpu-infer", cpu_request=4, memory_request=4, node_selector={"tier": "gpu"}, priority=6),
        Pod("west-only", cpu_request=2, memory_request=4, node_selector={"zone": "us-west"}, priority=4),
        Pod("critical-db", cpu_request=3, memory_request=6, priority=10),
    ]


def main() -> None:
    print("Tiny Kubernetes-like Scheduler -- demo\n")
    print("Strategy: best-fit, preemption: enabled")

    nodes = build_nodes()
    scheduler = Scheduler(nodes, strategy="best-fit", enable_preemption=True)
    pods = build_pods()

    results = scheduler.schedule_all(pods)
    print("\nInitial scheduling decisions:")
    for pod_name, node_name in results.items():
        print(f"  {pod_name:12s} -> {node_name if node_name else 'PENDING'}")

    print_cluster(scheduler, "Cluster after initial scheduling")

    # Simulate a node failure: node-c (the big GPU node) goes down.
    print("\n>>> node-c goes DOWN <<<")
    reschedule_results = scheduler.fail_node("node-c")
    print("\nRescheduling decisions after failure:")
    for pod_name, node_name in reschedule_results.items():
        print(f"  {pod_name:12s} -> {node_name if node_name else 'PENDING'}")

    print_cluster(scheduler, "Cluster after node-c failure and rescheduling")

    print("\nDone.")


if __name__ == "__main__":
    main()
