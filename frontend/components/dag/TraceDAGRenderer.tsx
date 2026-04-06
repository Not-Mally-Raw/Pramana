"use client";

import { useMemo, useState, useEffect } from "react";
import { buildDagLayout, DAG_EDGES } from "@/components/dag/dag-layout";
import { fetchTraceReplay } from "@/lib/api";
import { riskToHex } from "@/lib/design-tokens";
import { subscribeToTrace } from "@/lib/websocket";
import type { DagNodeEvent } from "@/lib/types";

type Mode = "live" | "replay";

type NodeStatus =
  | "waiting"
  | "processing"
  | "complete-pass"
  | "complete-escalate"
  | "complete-pause"
  | "skipped";

interface NodeState {
  status: NodeStatus;
  score: number;
  flagged?: boolean;
  latencyMs?: number;
}

interface TraceDAGRendererProps {
  traceId: string;
  mode: Mode;
}

interface EdgePulse {
  id: string;
  pathId: string;
  color: string;
}

const NODE_NAME_TO_ID: Record<string, string> = {
  sanctions: "sanctions",
  aavalidator: "aa_validator",
  aa_validator: "aa_validator",
  network: "network",
  operations: "operations",
  hallucination: "hallucination",
  injection: "injection",
  validator: "validator",
  economicsanity: "economic_sanity",
  economic_sanity: "economic_sanity",
  drift: "drift",
  drift_monitor: "drift",
  fusion: "fusion",
  decision: "decision",
  emergency_pause: "decision",
  council: "fusion",
  ingest: "userop_received",
};

function normalizeNodeId(nodeName: string): string {
  const key = nodeName.replace(/[^a-zA-Z0-9_]/g, "").toLowerCase();
  return NODE_NAME_TO_ID[key] || key;
}

function initialNodeState(nodeIds: string[]): Record<string, NodeState> {
  return Object.fromEntries(nodeIds.map((nodeId) => [nodeId, { status: "waiting", score: 0 }])) as Record<
    string,
    NodeState
  >;
}

function toCompletedStatus(nodeId: string, score: number, flagged: boolean): NodeStatus {
  if (!flagged) {
    return "complete-pass";
  }

  // Conservative hard-stop thresholds for critical gatekeepers.
  const pauseThreshold = nodeId === "sanctions" || nodeId === "aa_validator" ? 0.95 : 0.85;
  if (score >= pauseThreshold) {
    return "complete-pause";
  }

  return "complete-escalate";
}

function shouldHardStop(nodeId: string, score: number, flagged: boolean): boolean {
  if (!flagged) {
    return false;
  }

  const threshold = nodeId === "sanctions" || nodeId === "aa_validator" ? 0.95 : 0.85;
  return score >= threshold;
}

export function TraceDAGRenderer({ traceId, mode }: TraceDAGRendererProps) {
  const nodes = useMemo(() => buildDagLayout(), []);
  const lookup = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const descendants = useMemo(() => {
    const adjacency = new Map<string, string[]>();
    for (const edge of DAG_EDGES) {
      const current = adjacency.get(edge.source) || [];
      current.push(edge.target);
      adjacency.set(edge.source, current);
    }

    const output: Record<string, string[]> = {};
    const walk = (source: string) => {
      const seen = new Set<string>();
      const queue = [...(adjacency.get(source) || [])];
      while (queue.length > 0) {
        const next = queue.shift() as string;
        if (seen.has(next)) {
          continue;
        }
        seen.add(next);
        queue.push(...(adjacency.get(next) || []));
      }
      return Array.from(seen);
    };

    for (const node of nodes) {
      output[node.id] = walk(node.id);
    }

    return output;
  }, [nodes]);

  const [nodeState, setNodeState] = useState<Record<string, NodeState>>(() => initialNodeState(nodes.map((node) => node.id)));
  const [edgePulses, setEdgePulses] = useState<EdgePulse[]>([]);

  useEffect(() => {
    setNodeState(initialNodeState(nodes.map((node) => node.id)));
    setEdgePulses([]);
  }, [nodes, traceId]);

  useEffect(() => {
    if (mode === "replay") {
      let cancelled = false;

      const load = async () => {
        const replay = await fetchTraceReplay(traceId);
        const dag = replay?.dag as { nodes?: Record<string, { score?: number; metadata?: Record<string, unknown> }> } | undefined;
        if (!dag || !dag.nodes || cancelled) {
          return;
        }

        setNodeState((current) => {
          const next = { ...current };
          for (const [nodeName, payload] of Object.entries(dag.nodes || {})) {
            const nodeId = normalizeNodeId(nodeName);
            if (!next[nodeId]) {
              continue;
            }

            const score = Number(payload?.score || 0);
            const metadata = (payload?.metadata || {}) as Record<string, unknown>;
            const flagged = Boolean(metadata.flagged) || score >= 0.85;
            next[nodeId] = {
              status: toCompletedStatus(nodeId, score, flagged),
              score,
              flagged,
            };
          }
          return next;
        });
      };

      void load();

      return () => {
        cancelled = true;
      };
    }

    const timerIds: number[] = [];

    const pulseOutgoingEdges = (nodeId: string, event: DagNodeEvent) => {
      const outgoing = DAG_EDGES.filter((edge) => edge.source === nodeId);
      if (outgoing.length === 0) {
        return;
      }

      const createdAt = Date.now();
      const created = outgoing.map((edge, idx) => ({
        id: `${edge.source}-${edge.target}-${createdAt}-${idx}`,
        pathId: `edge-${edge.source}-${edge.target}`,
        color: riskToHex(event.score),
      }));

      setEdgePulses((current) => [...current, ...created].slice(-120));

      const removeTimer = window.setTimeout(() => {
        setEdgePulses((current) => current.filter((pulse) => !created.some((item) => item.id === pulse.id)));
      }, 450);
      timerIds.push(removeTimer);
    };

    const completeNode = (nodeId: string, event: DagNodeEvent) => {
      setNodeState((current) => {
        const next = { ...current };
        next[nodeId] = {
          status: toCompletedStatus(nodeId, event.score, event.flagged),
          score: event.score,
          flagged: event.flagged,
          latencyMs: event.latency_ms,
        };

        if (shouldHardStop(nodeId, event.score, event.flagged)) {
          for (const downstream of descendants[nodeId] || []) {
            const state = next[downstream];
            if (!state) {
              continue;
            }
            if (state.status === "waiting" || state.status === "processing") {
              next[downstream] = { ...state, status: "skipped" };
            }
          }
        }

        return next;
      });

      pulseOutgoingEdges(nodeId, event);
    };

    const dispose = subscribeToTrace(traceId, (event) => {
      if (event.type !== "dag_node" || event.trace_id !== traceId) {
        return;
      }

      const nodeId = normalizeNodeId(event.node_name);
      if (!lookup.has(nodeId)) {
        return;
      }

      setNodeState((current) => ({
        ...current,
        [nodeId]: {
          status: "processing",
          score: event.score,
          flagged: event.flagged,
          latencyMs: event.latency_ms,
        },
      }));

      const timerId = window.setTimeout(() => completeNode(nodeId, event), 140);
      timerIds.push(timerId);
    });

    return () => {
      for (const id of timerIds) {
        window.clearTimeout(id);
      }
      dispose();
    };
  }, [descendants, lookup, mode, traceId]);

  return (
    <div className="panel overflow-auto p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="card-label">Trace DAG ({mode})</h3>
        <span className="rounded-full border border-border-subtle px-2 py-0.5 text-[11px] uppercase tracking-[0.08em] text-text-secondary">
          {traceId.slice(0, 12)}...
        </span>
      </div>

      <svg width={1600} height={360} viewBox="0 0 1600 360" className="min-w-full overflow-visible">
        {DAG_EDGES.map((edge) => {
          const source = lookup.get(edge.source);
          const target = lookup.get(edge.target);
          if (!source || !target) {
            return null;
          }

          const sourceState = nodeState[source.id];
          const sourceComplete = sourceState?.status.startsWith("complete");
          const sourceSkipped = sourceState?.status === "skipped";
          const stroke = sourceComplete
            ? riskToHex(sourceState?.score || 0.3)
            : sourceSkipped
              ? "rgba(255,255,255,0.18)"
              : "rgba(255,255,255,0.12)";
          const pathId = `edge-${edge.source}-${edge.target}`;

          return (
            <path
              key={`${edge.source}-${edge.target}`}
              id={pathId}
              d={`M ${source.x} ${source.y} L ${target.x} ${target.y}`}
              stroke={stroke}
              strokeWidth={sourceComplete ? 2 : 1}
              opacity={sourceComplete ? 0.9 : 0.5}
              fill="none"
            />
          );
        })}

        {edgePulses.map((pulse) => (
          <circle key={pulse.id} r={3.5} fill={pulse.color} opacity={0.95}>
            <animateMotion dur="0.4s" repeatCount={1} rotate="auto">
              <mpath href={`#${pulse.pathId}`} />
            </animateMotion>
          </circle>
        ))}

        {nodes.map((node) => {
          const state = nodeState[node.id] || { status: "waiting", score: 0 };
          const isProcessing = state.status === "processing";
          const isWaiting = state.status === "waiting";
          const isSkipped = state.status === "skipped";
          const isComplete = state.status.startsWith("complete");
          const isPause = state.status === "complete-pause";
          const isEscalate = state.status === "complete-escalate";

          const baseColor = isPause ? "#ff375f" : isEscalate ? "#ffb800" : riskToHex(state.score || 0.1);
          const strokeColor = isWaiting ? "#3f3f46" : isProcessing ? "#ffffff" : baseColor;
          const fillColor = isWaiting || isSkipped ? "transparent" : baseColor;
          const glow = isPause || isEscalate ? `drop-shadow(0 0 8px ${baseColor})` : "none";

          return (
            <g key={node.id} transform={`translate(${node.x}, ${node.y})`} opacity={isSkipped ? 0.3 : 1}>
              {isProcessing ? (
                <circle
                  r={30}
                  fill="transparent"
                  stroke="#ffffff"
                  strokeWidth={1.5}
                  strokeDasharray="7 4"
                  className="origin-center animate-spin"
                  opacity={0.8}
                />
              ) : null}

              <circle r={26} fill="transparent" stroke={strokeColor} strokeWidth={2} />
              <circle r={20} fill={fillColor} fillOpacity={isComplete ? 0.2 : 0.08} style={{ filter: glow }} />
              <text y={42} textAnchor="middle" fontSize="11" fill="#a1a1aa">
                {node.label}
              </text>

              {isComplete ? (
                <text y={4} textAnchor="middle" fontSize="11" fill="#ffffff" fontWeight={700}>
                  {state.score.toFixed(2)}
                </text>
              ) : null}

              {isSkipped ? (
                <text y={58} textAnchor="middle" fontSize="10" fill="#a1a1aa" className="uppercase tracking-[0.08em]">
                  skipped
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
