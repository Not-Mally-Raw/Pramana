"""
utils/graph.py — TraceDAG: interaction graph as the canonical trace object.

Design:
  - NetworkX DiGraph, O(V+E) construction.
  - Node = (agent_name, step_index) tuple, attributes: timestamp, action, score.
  - Edge = directed hand-off, attributes: data_hash (SHA-256 of payload).
  - finalize() → deterministic adjacency-list JSON → SHA-256 → 32-byte hex.
  - Serialization format is stable (sorted nodes/edges) for reproducible hashes.
  - Max expected V=8, E=10 per flow → ~500 bytes serialized, negligible memory.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import networkx as nx


class TraceDAG:
    """
    Wraps a NetworkX DiGraph to represent one complete Pramãna flow execution.
    Thread-safety: instances are created per-flow, never shared across threads.
    """

    def __init__(self, flow_id: str, agent_id: str, org_id: str) -> None:
        self.flow_id = flow_id
        self.agent_id = agent_id
        self.org_id = org_id
        self._g: nx.DiGraph = nx.DiGraph()
        self._step = 0
        self._finalized: str | None = None  # cached hash after finalize()

    # ─── Node / Edge API ──────────────────────────────────────────────────────

    def add_node(
        self,
        name: str,
        action: str = "",
        score: float = 0.0,
        flagged: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Add an agent/step node. Returns node_id = f"{name}:{step}"."""
        node_id = f"{name}:{self._step}"
        self._step += 1
        self._g.add_node(
            node_id,
            name=name,
            action=action,
            score=score,
            flagged=flagged,
            ts=time.time(),
            metadata=metadata or {},
        )
        return node_id

    def add_edge(
        self,
        from_node: str,
        to_node: str,
        payload: Any = "",
    ) -> None:
        """Add a directed edge representing an agent hand-off.
        payload is hashed (SHA-256) and stored as edge attribute — not raw data.
        """
        src = self._resolve_node_ref(from_node)
        dst = self._resolve_node_ref(to_node)

        if isinstance(payload, str):
            payload_str = payload
        elif payload is None:
            payload_str = ""
        else:
            payload_str = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), default=str
            )

        payload_hash = (
            hashlib.sha256(payload_str.encode()).hexdigest() if payload_str else ""
        )
        self._g.add_edge(src, dst, data_hash=payload_hash)

    def _resolve_node_ref(self, node_ref: str) -> str:
        """Resolve a node reference by ID first, then by agent name.

        If multiple nodes share the same name, select the latest step.
        """
        if node_ref in self._g:
            return node_ref

        matches: list[tuple[int, str]] = []
        for nid, attrs in self._g.nodes(data=True):
            if attrs.get("name") != node_ref:
                continue
            step = -1
            if ":" in nid:
                try:
                    step = int(nid.rsplit(":", 1)[1])
                except ValueError:
                    step = -1
            matches.append((step, nid))

        if matches:
            matches.sort(key=lambda x: x[0])
            return matches[-1][1]

        return node_ref

    # ─── Serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """
        Stable, deterministic adjacency-list dict.
        Sorted by node_id so the same execution always produces the same JSON.
        O(V + E).
        """
        nodes = {
            nid: {k: v for k, v in attrs.items() if k != "ts"}
            for nid, attrs in sorted(self._g.nodes(data=True))
        }
        edges = [
            {"from": u, "to": v, "data_hash": d.get("data_hash", "")}
            for u, v, d in sorted(self._g.edges(data=True), key=lambda x: (x[0], x[1]))
        ]
        return {
            "flow_id": self.flow_id,
            "agent_id": self.agent_id,
            "org_id": self.org_id,
            "nodes": nodes,
            "edges": edges,
        }

    def to_json(self) -> str:
        """Canonical JSON string — compact, sorted keys, no whitespace."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    # ─── Finalization & Hash ──────────────────────────────────────────────────

    def finalize(self) -> str:
        """
        Compute SHA-256 of the canonical JSON.
        Result is cached — calling twice returns same value.
        Returns: 64-char hex string (32 bytes).
        Complexity: O(V+E) for serialization + O(n) for hash where n = JSON bytes.
        Typical n ≈ 500 bytes → ~0.01ms on M2.
        """
        if self._finalized is not None:
            return self._finalized
        raw = self.to_json().encode("utf-8")
        self._finalized = hashlib.sha256(raw).hexdigest()
        return self._finalized

    def finalize_bytes32(self) -> bytes:
        """Return 32-byte representation suitable for Solidity bytes32."""
        return bytes.fromhex(self.finalize())

    # ─── Graph Analytics ──────────────────────────────────────────────────────

    def flagged_nodes(self) -> list[str]:
        """O(V) — return node_ids where flagged=True."""
        return [nid for nid, attrs in self._g.nodes(data=True) if attrs.get("flagged")]

    def flag_count(self) -> int:
        return len(self.flagged_nodes())

    def average_score(self) -> float:
        """Average agent score across all nodes. O(V)."""
        scores = [attrs.get("score", 0.0) for _, attrs in self._g.nodes(data=True)]
        return sum(scores) / len(scores) if scores else 0.0

    def ancestors_of(self, node_id: str) -> list[str]:
        """Which agents contributed to this node's decision? O(V+E)."""
        return list(nx.ancestors(self._g, node_id))

    def critical_path(self) -> list[str]:
        """Longest path in the DAG — represents the bottleneck chain. O(V+E)."""
        try:
            return nx.dag_longest_path(self._g)
        except nx.NetworkXUnfeasible:
            return []

    # ─── Reconstruction from SQLite ───────────────────────────────────────────

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TraceDAG":
        """Reconstruct a TraceDAG from stored adjacency dict (e.g. for replay)."""
        dag = cls(
            flow_id=d["flow_id"],
            agent_id=d["agent_id"],
            org_id=d["org_id"],
        )
        for nid, attrs in d.get("nodes", {}).items():
            dag._g.add_node(nid, **attrs)
        for edge in d.get("edges", []):
            dag._g.add_edge(
                edge["from"], edge["to"], data_hash=edge.get("data_hash", "")
            )
        dag._step = len(dag._g.nodes)
        return dag

    @classmethod
    def from_json(cls, s: str) -> "TraceDAG":
        return cls.from_dict(json.loads(s))

    def __repr__(self) -> str:
        return (
            f"TraceDAG(flow_id={self.flow_id!r}, "
            f"nodes={self._g.number_of_nodes()}, "
            f"edges={self._g.number_of_edges()}, "
            f"flags={self.flag_count()})"
        )
