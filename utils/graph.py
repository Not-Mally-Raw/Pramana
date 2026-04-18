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
from datetime import datetime, timezone
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

    @staticmethod
    def _node_sort_key(node_id: str) -> tuple[int, str]:
        if ":" in node_id:
            try:
                return int(node_id.rsplit(":", 1)[1]), node_id
            except ValueError:
                pass
        return -1, node_id

    @staticmethod
    def _span_id(trace_id: str, node_id: str) -> str:
        return hashlib.sha256(f"{trace_id}:{node_id}".encode("utf-8")).hexdigest()[:16]

    def to_otel_trace(self) -> dict[str, Any]:
        """Render the DAG as a trace payload compatible with OpenTelemetry concepts."""
        trace_id = self.finalize()[:32]
        ordered_nodes = sorted(
            self._g.nodes(data=True), key=lambda item: self._node_sort_key(item[0])
        )
        span_ids = {nid: self._span_id(trace_id, nid) for nid, _ in ordered_nodes}

        spans: list[dict[str, Any]] = []
        previous_node_id = ""

        for index, (node_id, attrs) in enumerate(ordered_nodes):
            predecessors = sorted(self._g.predecessors(node_id), key=self._node_sort_key)
            explicit_parent = predecessors[0] if predecessors else ""
            parent_node_id = explicit_parent or previous_node_id
            inferred_parent = not explicit_parent and bool(previous_node_id)

            metadata = attrs.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {"value": metadata}

            ts_value = attrs.get("ts", time.time())
            ts_iso = datetime.fromtimestamp(float(ts_value), tz=timezone.utc).isoformat()

            attributes: dict[str, Any] = {
                "pramana.flow_id": self.flow_id,
                "pramana.agent_id": self.agent_id,
                "pramana.org_id": self.org_id,
                "pramana.node_name": attrs.get("name", node_id),
                "pramana.action": attrs.get("action", ""),
                "pramana.score": float(attrs.get("score", 0.0) or 0.0),
                "pramana.flagged": bool(attrs.get("flagged", False)),
                "pramana.step": index,
                "pramana.metadata_json": json.dumps(
                    metadata, sort_keys=True, separators=(",", ":"), default=str
                ),
            }
            if inferred_parent:
                attributes["pramana.inferred_parent"] = True

            links = [
                {
                    "trace_id": trace_id,
                    "span_id": span_ids[pred],
                    "type": "link",
                }
                for pred in predecessors[1:]
            ]

            events: list[dict[str, Any]] = []
            if attrs.get("flagged"):
                events.append(
                    {
                        "name": "flagged",
                        "timestamp": ts_iso,
                        "attributes": {
                            "score": float(attrs.get("score", 0.0) or 0.0)
                        },
                    }
                )

            spans.append(
                {
                    "name": attrs.get("name", node_id),
                    "context": {
                        "trace_id": trace_id,
                        "span_id": span_ids[node_id],
                    },
                    "parent_id": span_ids[parent_node_id] if parent_node_id else "",
                    "start_time": ts_iso,
                    "end_time": ts_iso,
                    "kind": "SERVER" if index == 0 else "INTERNAL",
                    "status_code": "STATUS_CODE_UNSET",
                    "status_message": "",
                    "attributes": attributes,
                    "events": events,
                    "links": links,
                }
            )
            previous_node_id = node_id

        return {
            "schema": "otel-span-v1",
            "trace_id": trace_id,
            "resource": {
                "service.name": "pramana",
                "service.version": "1.0.0",
            },
            "spans": spans,
        }

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
