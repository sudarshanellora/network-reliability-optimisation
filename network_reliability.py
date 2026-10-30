"""
Network reliability optimisation for an 11-node telecommunications network.

1. Base case:   maximum number of node-disjoint paths from node 10 to node 11,
                using a node-splitting max-flow model
2. Extension:   every intermediate node fails independently with p = 0.10;
                all 2^9 = 512 failure scenarios are evaluated exactly
3. Hardening:   which nodes (budget B = 2) to make failure-proof to maximise
                the probability that node 10 stays connected to node 11

Usage:
    python network_reliability.py              # results summary
    python network_reliability.py --scenarios  # also save all 512 scenarios to results/
"""
import argparse
import csv
import itertools
from pathlib import Path

import networkx as nx

# ---------------------------
# Network
# ---------------------------
EDGES = [
    (1, 2), (1, 3), (1, 11),
    (2, 3), (2, 8), (2, 9),
    (3, 4), (3, 5), (3, 9), (3, 10), (3, 11),
    (4, 5), (4, 6), (4, 11),
    (5, 9), (5, 11),
    (6, 7), (6, 9), (6, 10),
    (7, 8), (7, 10),
    (8, 10),
    (9, 10),
]
N = 11
SOURCE = 10
SINK = 11
FAILURE_PROB = 0.10
BUDGET = 2
CANDIDATES = [i for i in range(1, N + 1) if i not in (SOURCE, SINK)]
RESULTS = Path(__file__).resolve().parent / "results"


# ---------------------------
# Node splitting + max flow
# ---------------------------
def build_node_split_digraph(edges, n, failed_nodes=frozenset()):
    """Split each node i into i_in -> i_out with capacity 1, so max flow
    equals the number of node-disjoint paths (Menger's theorem)."""
    G = nx.DiGraph()
    for i in range(1, n + 1):
        if i in failed_nodes:
            continue
        cap = 1000 if i in (SOURCE, SINK) else 1
        G.add_edge(f"{i}_in", f"{i}_out", capacity=cap)

    for u, v in edges:
        if u in failed_nodes or v in failed_nodes:
            continue
        G.add_edge(f"{u}_out", f"{v}_in", capacity=1)
        G.add_edge(f"{v}_out", f"{u}_in", capacity=1)
    return G


def max_flow(edges, n, failed_nodes=frozenset()):
    """Return (number of node-disjoint paths, graph of edges carrying flow)."""
    G = build_node_split_digraph(edges, n, failed_nodes)
    s, t = f"{SOURCE}_out", f"{SINK}_in"
    if not (G.has_node(s) and G.has_node(t)):
        return 0, nx.DiGraph()

    value, flow = nx.maximum_flow(G, s, t, capacity="capacity")
    H = nx.DiGraph()
    for u, targets in flow.items():
        for v, f in targets.items():
            if f > 0:
                H.add_edge(u, v)
    return int(value), H


def to_original_path(split_path):
    path = [SOURCE]
    for node in split_path:
        if node.endswith("_in"):
            num = int(node.split("_")[0])
            if num != SOURCE and path[-1] != num:
                path.append(num)
    if path[-1] != SINK:
        path.append(SINK)
    return path


def extract_paths(H, max_paths):
    """Read node-disjoint paths off the flow graph."""
    H = H.copy()
    s, t = f"{SOURCE}_out", f"{SINK}_in"
    paths = []
    for _ in range(max_paths):
        try:
            p = nx.shortest_path(H, s, t)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            break
        paths.append(to_original_path(p))
        H.remove_edges_from(zip(p[:-1], p[1:]))
    return paths


# ---------------------------
# Probabilistic reliability
# ---------------------------
def failure_probs(hardened=()):
    p = {i: FAILURE_PROB for i in CANDIDATES}
    for i in hardened:
        p[i] = 0.0
    return p


def all_failure_subsets(nodes):
    for r in range(len(nodes) + 1):
        for comb in itertools.combinations(nodes, r):
            yield frozenset(comb)


def exact_reliability(edges, n, p, collect=False):
    """Enumerate all failure scenarios.

    Returns (P[source connected to sink], E[node-disjoint paths], scenarios).
    """
    p_connect, expected_k, scenarios = 0.0, 0.0, []
    for failed in all_failure_subsets(CANDIDATES):
        prob = 1.0
        for i in CANDIDATES:
            prob *= p[i] if i in failed else (1 - p[i])

        k, H = max_flow(edges, n, failed)
        if k >= 1:
            p_connect += prob
        expected_k += prob * k

        if collect:
            example = extract_paths(H, 1)
            scenarios.append({
                "failed_nodes": sorted(failed),
                "surviving_nodes": sorted(set(range(1, n + 1)) - failed),
                "disjoint_paths": k,
                "connected": k >= 1,
                "example_path": example[0] if example else None,
                "probability": prob,
            })
    return p_connect, expected_k, scenarios


# ---------------------------
# Hardening
# ---------------------------
def rank_hardening(edges, n, size):
    """Exact connectivity and expected paths for every hardening set of a given size."""
    rows = []
    for combo in itertools.combinations(CANDIDATES, size):
        pc, ek, _ = exact_reliability(edges, n, failure_probs(combo))
        rows.append({"hardened": list(combo), "p_connect": pc, "expected_paths": ek})
    return sorted(rows, key=lambda r: (-round(r["p_connect"], 12), -r["expected_paths"]))


def best_hardening(edges, n, budget):
    """Smallest set within budget with the highest connectivity;
    ties broken by expected number of disjoint paths."""
    options = [{"hardened": [], **dict(zip(("p_connect", "expected_paths"),
                                           exact_reliability(edges, n, failure_probs())[:2]))}]
    for size in range(1, budget + 1):
        options += rank_hardening(edges, n, size)
    best_p = max(round(o["p_connect"], 12) for o in options)
    return [o for o in options if round(o["p_connect"], 12) == best_p]


def write_csv(path, rows):
    path.parent.mkdir(exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: (" ".join(map(str, v)) if isinstance(v, list)
                            else round(v, 8) if isinstance(v, float) else v) for k, v in r.items()})


# ---------------------------
# Main
# ---------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", action="store_true", help="save all 512 scenarios to results/")
    args = parser.parse_args()

    print("BASE CASE (deterministic)")
    k, H = max_flow(EDGES, N)
    print(f"Max node-disjoint paths from {SOURCE} to {SINK}: {k}")
    for i, path in enumerate(extract_paths(H, k), 1):
        print(f"  Path {i}: " + " -> ".join(map(str, path)))
    print("Paths left after removing one node: " +
          ", ".join(f"{i}:{max_flow(EDGES, N, {i})[0]}" for i in CANDIDATES))

    print(f"\nEXTENSION (each intermediate node fails with p = {FAILURE_PROB})")
    p_connect, exp_k, scenarios = exact_reliability(EDGES, N, failure_probs(), collect=True)
    disconnected = [s for s in scenarios if not s["connected"]]
    print(f"Scenarios evaluated: {len(scenarios)}")
    print(f"Connectivity probability: {p_connect:.6f}")
    print(f"Expected node-disjoint paths: {exp_k:.6f}")
    print(f"Disconnected scenarios: {len(disconnected)} "
          f"(smallest needs {min(len(s['failed_nodes']) for s in disconnected)} failures, "
          f"all include node 3: {all(3 in s['failed_nodes'] for s in disconnected)})")

    print(f"\nHARDENING (budget B = {BUDGET})")
    singles = rank_hardening(EDGES, N, 1)
    pairs = rank_hardening(EDGES, N, 2)
    print("Best single nodes:")
    for r in singles[:5]:
        print(f"  {r['hardened']}  P(connect) = {r['p_connect']:.6f}  E[paths] = {r['expected_paths']:.4f}")
    print("Best pairs:")
    for r in pairs[:5]:
        print(f"  {r['hardened']}  P(connect) = {r['p_connect']:.6f}  E[paths] = {r['expected_paths']:.4f}")
    best = best_hardening(EDGES, N, BUDGET)
    smallest = min(len(o["hardened"]) for o in best)
    print("Smallest set reaching the best connectivity: " +
          ", ".join(str(o["hardened"]) for o in best if len(o["hardened"]) == smallest))

    write_csv(RESULTS / "hardening_single.csv", singles)
    write_csv(RESULTS / "hardening_pairs.csv", pairs)
    if args.scenarios:
        write_csv(RESULTS / "all_scenarios.csv", scenarios)
        print(f"\nSaved {len(scenarios)} scenarios to results/all_scenarios.csv")


if __name__ == "__main__":
    main()
