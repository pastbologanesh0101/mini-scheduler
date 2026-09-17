# Tiny Kubernetes-like Scheduler

A small, fully in-process simulation of a cluster scheduler, written in
pure Python (standard library only, no dependencies). It models nodes
and pods, and schedules pods onto nodes using pluggable bin-packing
strategies while respecting resource limits and label-based node
selectors — with optional priority preemption and node-failure
rescheduling.

This is a simulation for learning/portfolio purposes: there are no real
containers, no Docker, no Kubernetes API. Everything runs as plain
Python objects in a single process.

## Concepts

### Nodes and Pods

- **`Node`** (`scheduler/models.py`): has a name, a fixed CPU capacity,
  a fixed memory capacity, and a dict of labels (e.g.
  `{"zone": "us-east", "tier": "gpu"}`). Its `cpu_used`/`memory_used`
  are always derived from the pods actually placed on it, so usage can
  never silently drift out of sync with capacity.
- **`Pod`** (`scheduler/models.py`): has a name, a CPU request, a
  memory request, an optional `node_selector` (labels the target node
  must satisfy), and a `priority` (higher = more important).

### Bin-packing strategies

The `Scheduler` supports two placement strategies, selected via the
`strategy=` argument:

- **`first-fit`** — walk the nodes in the order they were added and
  place the pod on the first one with enough free CPU and memory. Fast
  and simple; can leave capacity fragmented across many nodes.
- **`best-fit`** (a.k.a. "most-requested") — among all nodes that could
  fit the pod, choose the one that would be left with the *least*
  total free capacity afterwards (the tightest fit). This packs pods
  more densely onto fewer nodes, which is useful for bin-packing
  efficiency and for keeping some nodes completely free (e.g. for
  scale-down).

Both strategies always respect capacity: a pod is never placed on a
node unless `node.cpu_free >= pod.cpu_request` and
`node.memory_free >= pod.memory_request` hold *after* accounting for
every pod already on that node.

### Node selectors

A pod's `node_selector` is a dict of label requirements. A node is
eligible for a pod only if, for every key/value pair in the selector,
the node has that exact label. This mirrors Kubernetes'
`nodeSelector` field — e.g. a pod with `node_selector={"tier": "gpu"}`
will only ever land on nodes labeled `tier=gpu`, regardless of how much
free capacity other nodes have.

### Priority preemption

If a pod can't be placed on any node as-is, and preemption is enabled
(`enable_preemption=True`, the default), the scheduler looks for a
node where evicting one or more **strictly lower-priority** pods would
free up enough room. It prefers the node that requires evicting the
fewest pods, evicts the lowest-priority ones first, places the new pod,
and pushes the evicted pods back onto the pending queue (from which
they may be rescheduled elsewhere on a later pass). A pod can never
evict a pod of equal or higher priority.

### Node failure and rescheduling

`Scheduler.fail_node(name)` simulates a node going down: it's marked
unhealthy (excluded from all future placement decisions), every pod
that was running on it is pulled off and pushed onto the pending queue,
and the scheduler immediately retries placing those pods onto the
remaining healthy nodes (highest priority first). Anything that still
doesn't fit anywhere stays in `scheduler.pending`.
`Scheduler.recover_node(name)` marks a node healthy again and retries
the pending queue.

## Project layout

```
scheduler/
  models.py      Node and Pod data classes
  scheduler.py    Scheduler: placement, preemption, failure handling
demo.py           Runnable demo: 5-node cluster, mixed pods, a node failure
tests/
  test_scheduler.py  Unit tests (unittest, stdlib only)
.github/workflows/tests.yml  CI: runs the test suite on Python 3.11 & 3.12
```

## Running the demo

```bash
python3 demo.py
```

This builds a 5-node cluster (mixed CPU/memory capacities, some labeled
`tier=gpu`, some in different `zone`s), schedules 8 pods with a mix of
plain requests, node-selector requirements, and priorities, prints the
resulting placement, then simulates `node-c` failing and shows how its
pods get rescheduled onto the surviving nodes (or left pending if
nothing fits — e.g. because only that node satisfied a pod's selector
with enough capacity).

## Running the tests

Pure standard library — no install step needed:

```bash
python3 -m unittest discover -s tests -v
```

or, if you have pytest available:

```bash
python3 -m pytest tests -v
```

The suite covers: scheduling onto a node with sufficient capacity,
pods staying pending when no node has room, node-selector-restricted
placement, first-fit vs. best-fit producing different (both correct)
placements on the same cluster, resource limits never being exceeded
across many pods, node failure triggering rescheduling (including the
case where nothing else fits), and priority preemption evicting a
lower-priority pod to make room (with and without preemption enabled).

## License

MIT — see [LICENSE](LICENSE).
