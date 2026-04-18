"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchTraceReplay } from "@/lib/api";
import { subscribeToTrace } from "@/lib/websocket";
import type { DagNodeEvent, DecisionEvent, StreamEvent } from "@/lib/types";

type NodeStatus = "waiting" | "processing" | "pass" | "escalate" | "pause" | "skipped";

interface NodeState {
  status: NodeStatus;
  score: number | null;
  reasoning: string;
  plain_english: string;
  latency_ms: number | null;
  flagged: boolean;
}

interface DAGNode {
  id: string;
  label: string;
  fullName: string;
  description: string;
  layer: number;
  position: number;
  icon: string;
}

const PIPELINE_NODES: DAGNode[] = [
  {
    id: "ingest",
    label: "Transaction In",
    fullName: "Ingestion",
    description: "Transaction received and validated for processing",
    layer: 0,
    position: 0,
    icon: "IN",
  },
  {
    id: "sanctions",
    label: "Sanctions",
    fullName: "SanctionsAgent",
    description: "Checks sender address against OFAC sanctions list",
    layer: 1,
    position: 0,
    icon: "SC",
  },
  {
    id: "aa_validator",
    label: "AA Structure",
    fullName: "AAValidatorAgent",
    description: "Validates ERC-4337 UserOperation format and paymaster",
    layer: 2,
    position: 0,
    icon: "AA",
  },
  {
    id: "network",
    label: "Network",
    fullName: "NetworkAgent",
    description: "Checks RPC health and network conditions",
    layer: 3,
    position: 0,
    icon: "NW",
  },
  {
    id: "operations",
    label: "Operations",
    fullName: "OperationsAgent",
    description: "Detects action loops and abnormal transaction frequency",
    layer: 3,
    position: 1,
    icon: "OP",
  },
  {
    id: "hallucination",
    label: "Behavior Match",
    fullName: "HallucinationAgent",
    description: "Compares current behavior to historical agent baseline",
    layer: 3,
    position: 2,
    icon: "HM",
  },
  {
    id: "injection",
    label: "Injection",
    fullName: "InjectionAgent",
    description: "Scans calldata for adversarial prompt injection patterns",
    layer: 4,
    position: 0,
    icon: "IJ",
  },
  {
    id: "validator",
    label: "Validation",
    fullName: "ValidatorAgent",
    description: "Structural validation of transaction parameters",
    layer: 4,
    position: 1,
    icon: "VL",
  },
  {
    id: "economic_sanity",
    label: "Economics",
    fullName: "EconomicSanityAgent",
    description: "Checks gas, value transfer, and MEV exposure",
    layer: 5,
    position: 0,
    icon: "EC",
  },
  {
    id: "drift",
    label: "Drift",
    fullName: "DriftMonitorAgent",
    description: "Detects gradual behavioral strategy changes over time",
    layer: 6,
    position: 0,
    icon: "DR",
  },
  {
    id: "fusion",
    label: "Risk Fusion",
    fullName: "RiskFusion",
    description: "Combines all agent scores into a single risk assessment",
    layer: 7,
    position: 0,
    icon: "RF",
  },
  {
    id: "decision",
    label: "Decision",
    fullName: "FinalDecision",
    description: "Final verdict: allow, escalate for human review, or block",
    layer: 8,
    position: 0,
    icon: "FN",
  },
];

const PIPELINE_EDGES: Array<[string, string]> = [
  ["ingest", "sanctions"],
  ["sanctions", "aa_validator"],
  ["aa_validator", "network"],
  ["aa_validator", "operations"],
  ["aa_validator", "hallucination"],
  ["network", "injection"],
  ["operations", "injection"],
  ["hallucination", "injection"],
  ["network", "validator"],
  ["operations", "validator"],
  ["hallucination", "validator"],
  ["injection", "economic_sanity"],
  ["validator", "economic_sanity"],
  ["economic_sanity", "drift"],
  ["drift", "fusion"],
  ["fusion", "decision"],
];

const STATUS_COLORS: Record<NodeStatus, { bg: string; border: string; text: string; stroke: string }> = {
  waiting: {
    bg: "#131313",
    border: "#333333",
    text: "#707070",
    stroke: "#27272a",
  },
  processing: {
    bg: "#11192f",
    border: "#4f46e5",
    text: "#a5b4fc",
    stroke: "#6366f1",
  },
  pass: {
    bg: "#0b1e14",
    border: "#00d27a",
    text: "#7cf2bb",
    stroke: "#00d27a",
  },
  escalate: {
    bg: "#221a09",
    border: "#ffb800",
    text: "#ffd46b",
    stroke: "#ffb800",
  },
  pause: {
    bg: "#260b12",
    border: "#ff3366",
    text: "#ff7c9b",
    stroke: "#ff3366",
  },
  skipped: {
    bg: "#0e0e0e",
    border: "#2f2f2f",
    text: "#4f4f4f",
    stroke: "#2f2f2f",
  },
};

const INITIAL_NODE_STATE: NodeState = {
  status: "waiting",
  score: null,
  reasoning: "",
  plain_english: "",
  latency_ms: null,
  flagged: false,
};

const SCORE_TO_COLOR = (score: number): string => {
  if (score < 0.3) {
    return "#00d27a";
  }
  if (score < 0.5) {
    return "#95d600";
  }
  if (score < 0.65) {
    return "#ffb800";
  }
  if (score < 0.8) {
    return "#ff7a00";
  }
  return "#ff3366";
};

const NODE_ID_ALIASES: Record<string, string> = {
  ingest: "ingest",
  userop_received: "ingest",
  sanctions: "sanctions",
  aa_validator: "aa_validator",
  aavalidator: "aa_validator",
  network: "network",
  operations: "operations",
  hallucination: "hallucination",
  injection: "injection",
  validator: "validator",
  economicsanity: "economic_sanity",
  economic_sanity: "economic_sanity",
  drift: "drift",
  driftmonitor: "drift",
  drift_monitor: "drift",
  council: "fusion",
  fusion: "fusion",
  decision: "decision",
  emergency_pause: "decision",
  finalize: "decision",
};

function normalizeNodeId(rawNode: string): string {
  const normalized = (rawNode || "").toLowerCase().replace(/[^a-z0-9_]/g, "");
  return NODE_ID_ALIASES[normalized] || normalized;
}

function makeInitialNodeStates(): Record<string, NodeState> {
  const initial: Record<string, NodeState> = {};
  PIPELINE_NODES.forEach((node) => {
    initial[node.id] = { ...INITIAL_NODE_STATE };
  });
  return initial;
}

function toNodeStatus(nodeId: string, score: number, flagged: boolean): NodeStatus {
  if (!flagged) {
    return "pass";
  }
  if ((nodeId === "sanctions" || nodeId === "aa_validator") && score >= 0.9) {
    return "pause";
  }
  if (score >= 0.85) {
    return "pause";
  }
  return "escalate";
}

function toPlainEnglish(nodeId: string, score: number, flagged: boolean, plainEnglish?: string): string {
  if (plainEnglish && plainEnglish.trim().length > 0) {
    return plainEnglish;
  }
  if (!flagged) {
    return `${nodeId} found nothing concerning for this transaction.`;
  }
  return `${nodeId} flagged potential risk (score ${(score * 100).toFixed(0)}%).`;
}

interface ExplainableDAGRendererProps {
  traceId: string | null;
  mode: "live" | "replay" | "idle";
  onNodeSelect?: (node: DAGNode, state: NodeState) => void;
}

export function ExplainableDAGRenderer({ traceId, mode, onNodeSelect }: ExplainableDAGRendererProps) {
  const [nodeStates, setNodeStates] = useState<Record<string, NodeState>>(makeInitialNodeStates);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [activeEdges, setActiveEdges] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState("");
  const [activeStage, setActiveStage] = useState("ingest");
  const [stageLog, setStageLog] = useState<string[]>([]);
  const dimensions = useMemo(() => ({ width: 1180, height: 620 }), []);

  const pushStageLog = useCallback((line: string) => {
    setStageLog((current) => [...current.slice(-8), line]);
  }, []);

  const getNodePosition = useCallback(
    (node: DAGNode) => {
      const layerSpacing = dimensions.width / 10;
      const centerY = dimensions.height / 2;
      const startX = 72;

      const layerX: Record<number, number> = {
        0: startX,
        1: startX + layerSpacing,
        2: startX + layerSpacing * 2,
        3: startX + layerSpacing * 3.2,
        4: startX + layerSpacing * 4.7,
        5: startX + layerSpacing * 6,
        6: startX + layerSpacing * 7,
        7: startX + layerSpacing * 8.1,
        8: startX + layerSpacing * 9,
      };

      const layerNodes = PIPELINE_NODES.filter((candidate) => candidate.layer === node.layer);
      const layerHeight = Math.max(1, layerNodes.length) * 88;
      const startY = centerY - layerHeight / 2 + node.position * 88 + 20;

      return {
        x: layerX[node.layer] || startX,
        y: startY,
      };
    },
    [dimensions.height, dimensions.width],
  );

  const stateFor = useCallback(
    (nodeId: string): NodeState => nodeStates[nodeId] || { ...INITIAL_NODE_STATE },
    [nodeStates],
  );

  useEffect(() => {
    setNodeStates(makeInitialNodeStates());
    setActiveEdges(new Set());
    setSelectedNode(null);
    setActiveStage("ingest");
    setStageLog([]);
  }, [traceId]);

  useEffect(() => {
    if (!traceId || mode === "idle") {
      return;
    }

    let cancelled = false;

    const loadReplay = async () => {
      const replay = await fetchTraceReplay(traceId);
      if (!replay || cancelled) {
        return;
      }

      const dag = replay.dag as { nodes?: Record<string, { score?: number; metadata?: Record<string, unknown> }> } | undefined;
      const dagNodes = dag?.nodes || {};

      setNodeStates((current) => {
        const next = { ...current };
        next.ingest = {
          ...next.ingest,
          status: "pass",
          score: 0,
          plain_english: "Transaction entered the Pramana pipeline.",
        };

        for (const [rawNode, payload] of Object.entries(dagNodes)) {
          const nodeId = normalizeNodeId(rawNode);
          if (!next[nodeId]) {
            continue;
          }

          const score = Number(payload?.score || 0);
          const metadata = (payload?.metadata || {}) as Record<string, unknown>;
          const flagged = Boolean(metadata.flagged) || score >= 0.85;
          const reason = typeof metadata.reason === "string" ? metadata.reason : "";
          const latencyMs = Number(metadata.latency_ms || 0) || null;

          next[nodeId] = {
            status: toNodeStatus(nodeId, score, flagged),
            score,
            reasoning: reason,
            plain_english: toPlainEnglish(nodeId, score, flagged),
            latency_ms: latencyMs,
            flagged,
          };
        }

        const replayNodes = Object.keys(dagNodes).map((key) => normalizeNodeId(key));
        if (replayNodes.length > 0) {
          setActiveStage(replayNodes[replayNodes.length - 1]);
          setStageLog(
            replayNodes.slice(-8).map((nodeId) => `stage.${nodeId} -> completed`),
          );
        }

        return next;
      });
    };

    void loadReplay();

    return () => {
      cancelled = true;
    };
  }, [mode, pushStageLog, traceId]);

  useEffect(() => {
    if (mode !== "live" || !traceId) {
      return;
    }

    const timers: number[] = [];

    const activateOutgoingEdges = (nodeId: string) => {
      const outgoing = PIPELINE_EDGES.filter(([src]) => src === nodeId).map(([src, dst]) => `${src}-${dst}`);
      outgoing.forEach((edgeId) => {
        setActiveEdges((current) => {
          const next = new Set(current);
          next.add(edgeId);
          return next;
        });
        const timer = window.setTimeout(() => {
          setActiveEdges((current) => {
            const next = new Set(current);
            next.delete(edgeId);
            return next;
          });
        }, 600);
        timers.push(timer);
      });
    };

    const onDagNode = (event: DagNodeEvent) => {
      if (event.trace_id !== traceId) {
        return;
      }

      const nodeId = normalizeNodeId(event.node_name || event.node_id || "");
      if (!PIPELINE_NODES.some((node) => node.id === nodeId)) {
        return;
      }

      setNodeStates((current) => {
        const next = { ...current };
        next.ingest = {
          ...next.ingest,
          status: next.ingest.status === "waiting" ? "pass" : next.ingest.status,
          score: next.ingest.score ?? 0,
          plain_english: next.ingest.plain_english || "Transaction entered the Pramana pipeline.",
        };
        next[nodeId] = {
          ...next[nodeId],
          status: "processing",
          score: event.score,
          flagged: event.flagged,
        };
        return next;
      });
      setActiveStage(nodeId);
      pushStageLog(`stage.${nodeId} -> running(score=${event.score.toFixed(2)})`);

      const completeTimer = window.setTimeout(() => {
        const status = toNodeStatus(nodeId, event.score, event.flagged);

        setNodeStates((current) => {
          const next = { ...current };
          next[nodeId] = {
            status,
            score: event.score,
            reasoning: event.reasoning || event.reason || "",
            plain_english: toPlainEnglish(nodeId, event.score, event.flagged, event.plain_english),
            latency_ms: event.latency_ms,
            flagged: event.flagged,
          };

          if (event.flagged && event.score >= 0.9 && (nodeId === "sanctions" || nodeId === "aa_validator")) {
            for (const downstreamId of _getDownstreamNodes(nodeId)) {
              const downstream = next[downstreamId];
              if (downstream && (downstream.status === "waiting" || downstream.status === "processing")) {
                next[downstreamId] = {
                  ...downstream,
                  status: "skipped",
                  reasoning: "hard_stop_skip",
                  plain_english: "Skipped because an upstream hard-stop blocked this flow.",
                };
              }
            }
          }

          return next;
        });

        activateOutgoingEdges(nodeId);
      }, 140);

      timers.push(completeTimer);
    };

    const onDecision = (event: DecisionEvent) => {
      if (event.trace_id !== traceId) {
        return;
      }

      setNodeStates((current) => ({
        ...current,
        decision: {
          status: event.decision === "PASS" ? "pass" : event.decision === "ESCALATE" ? "escalate" : "pause",
          score: event.risk_score,
          reasoning: event.top_agent ? `Top signal: ${event.top_agent}` : "final_decision",
          plain_english:
            event.decision === "PASS"
              ? "Transaction approved. All checks passed."
              : event.decision === "ESCALATE"
                ? "Transaction sent for human review because some checks raised concern."
                : "Transaction blocked due to high-risk indicators.",
          latency_ms: null,
          flagged: event.decision !== "PASS",
        },
      }));
      setActiveStage("decision");
      pushStageLog(`decision.final -> ${event.decision} (risk=${event.risk_score.toFixed(2)})`);
    };

    const dispose = subscribeToTrace(
      traceId,
      (event: StreamEvent) => {
        if (event.type === "dag_node") {
          onDagNode(event);
          return;
        }
        if (event.type === "decision") {
          onDecision(event);
        }
      },
      { eventTypes: ["dag_node", "decision"] },
    );

    return () => {
      for (const timer of timers) {
        window.clearTimeout(timer);
      }
      dispose();
    };
  }, [mode, pushStageLog, traceId]);

  const filteredNodes = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) {
      return PIPELINE_NODES;
    }
    return PIPELINE_NODES.filter((node) => {
      return (
        node.id.toLowerCase().includes(q) ||
        node.label.toLowerCase().includes(q) ||
        node.fullName.toLowerCase().includes(q)
      );
    });
  }, [searchQuery]);

  return (
    <div className="w-full overflow-hidden rounded-xl border border-zinc-800 bg-[#050505]">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 bg-zinc-950/70 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          {[
            { status: "pass", label: "Safe" },
            { status: "escalate", label: "Review" },
            { status: "pause", label: "Blocked" },
            { status: "processing", label: "Analyzing" },
            { status: "skipped", label: "Skipped" },
          ].map(({ status, label }) => (
            <div
              key={status}
              className="flex items-center gap-1.5 rounded-full border border-zinc-800 bg-black/70 px-2.5 py-1 text-[11px] text-zinc-400"
            >
              <span
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: STATUS_COLORS[status as NodeStatus].border }}
              />
              {label}
            </div>
          ))}
        </div>

        <div className="flex min-w-[260px] flex-1 items-center justify-end gap-2">
          <span className="text-[11px] uppercase tracking-[0.08em] text-zinc-500">Search Nodes</span>
          <input
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Find stage, agent, validator..."
            className="w-full max-w-[320px] rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-xs text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-indigo-500"
          />
        </div>
      </div>

      <div className="chain-grid relative">
        <svg width="100%" viewBox={`0 0 ${dimensions.width} ${dimensions.height}`} className="w-full">
        <defs>
          <pattern id="explainable-grid" width="36" height="36" patternUnits="userSpaceOnUse">
            <path d="M 36 0 L 0 0 0 36" fill="none" stroke="#161616" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width={dimensions.width} height={dimensions.height} fill="url(#explainable-grid)" />

        {PIPELINE_EDGES.map(([src, dst]) => {
          const srcNode = PIPELINE_NODES.find((node) => node.id === src);
          const dstNode = PIPELINE_NODES.find((node) => node.id === dst);
          if (!srcNode || !dstNode) {
            return null;
          }

          const srcPos = getNodePosition(srcNode);
          const dstPos = getNodePosition(dstNode);
          const edgeId = `${src}-${dst}`;
          const edgePathId = `edge-path-${edgeId}`;
          const sourceState = stateFor(src);
          const isActive = activeEdges.has(edgeId);

          const edgeColor =
            sourceState.status === "pass"
              ? "#00d27a"
              : sourceState.status === "pause"
                ? "#ff3366"
                : sourceState.status === "escalate"
                  ? "#ffb800"
                  : "#353535";

          const startX = srcPos.x + 74;
          const startY = srcPos.y + 22;
          const endX = dstPos.x - 74;
          const endY = dstPos.y + 22;
          const midX = (startX + endX) / 2;
          const pathD = `M ${startX} ${startY} L ${startX + 28} ${startY} L ${midX} ${endY} L ${endX - 28} ${endY} L ${endX} ${endY}`;

          return (
            <g key={edgeId}>
              <path
                id={edgePathId}
                d={pathD}
                fill="none"
                stroke={edgeColor}
                strokeWidth={sourceState.status === "waiting" ? 1 : 1.6}
                strokeDasharray={sourceState.status === "waiting" ? "5,5" : "none"}
                opacity={sourceState.status === "skipped" ? 0.25 : 0.7}
              />
              {isActive ? (
                <circle r="3.8" fill={edgeColor}>
                  <animateMotion dur="0.55s" repeatCount="1" rotate="auto">
                    <mpath href={`#${edgePathId}`} />
                  </animateMotion>
                </circle>
              ) : null}
            </g>
          );
        })}

        {PIPELINE_NODES.map((node) => {
          const pos = getNodePosition(node);
          const nodeWidth = 148;
          const nodeHeight = 56;
          const nodeX = pos.x - nodeWidth / 2;
          const nodeY = pos.y;
          const nodeState = stateFor(node.id);
          const colors = STATUS_COLORS[nodeState.status];
          const isSelected = selectedNode === node.id;

          return (
            <g
              key={node.id}
              transform={`translate(${nodeX}, ${nodeY})`}
              onClick={() => {
                setSelectedNode(node.id);
                onNodeSelect?.(node, nodeState);
              }}
              style={{ cursor: "pointer" }}
            >
              {nodeState.status !== "waiting" && nodeState.status !== "skipped" ? (
                <rect
                  x={-4}
                  y={-4}
                  width={nodeWidth + 8}
                  height={nodeHeight + 8}
                  rx={12}
                  ry={12}
                  fill={colors.border}
                  opacity={0.18}
                />
              ) : null}

              <rect
                x={0}
                y={0}
                width={nodeWidth}
                height={nodeHeight}
                rx={10}
                ry={10}
                fill={colors.bg}
                stroke={isSelected ? "#ffffff" : colors.border}
                strokeWidth={isSelected ? 2 : 1}
              />

              {nodeState.status === "processing" ? (
                <rect
                  x={0}
                  y={0}
                  width={nodeWidth}
                  height={nodeHeight}
                  rx={10}
                  ry={10}
                  fill="none"
                  stroke="#6366f1"
                  strokeWidth={2}
                  strokeDasharray="18,8"
                >
                  <animateTransform
                    attributeName="transform"
                    type="rotate"
                    from={`0 ${nodeWidth / 2} ${nodeHeight / 2}`}
                    to={`360 ${nodeWidth / 2} ${nodeHeight / 2}`}
                    dur="1.4s"
                    repeatCount="indefinite"
                  />
                </rect>
              ) : null}

              {nodeState.score !== null && nodeState.status !== "waiting" ? (
                <rect
                  x={8}
                  y={nodeHeight - 6}
                  width={(nodeWidth - 16) * nodeState.score}
                  height={3}
                  rx={2}
                  fill={SCORE_TO_COLOR(nodeState.score)}
                  opacity={0.85}
                />
              ) : null}

              <text x={11} y={20} fontSize={10} fontWeight={700} fill={colors.text} fontFamily="'Space Grotesk', sans-serif">
                {node.icon}
              </text>

              <text x={34} y={20} fontSize={11} fontWeight={700} fill={colors.text} fontFamily="'Space Grotesk', sans-serif">
                {node.label}
              </text>

              <text x={34} y={38} fontSize={9} fill={nodeState.latency_ms ? colors.text : "#5f5f5f"} fontFamily="'IBM Plex Mono', monospace" opacity={0.75}>
                {nodeState.status === "waiting"
                  ? "waiting"
                  : nodeState.status === "processing"
                    ? "analyzing"
                    : nodeState.status === "skipped"
                      ? "skipped"
                      : nodeState.latency_ms
                        ? `${nodeState.latency_ms.toFixed(0)}ms`
                        : nodeState.status}
              </text>

              {nodeState.score !== null && nodeState.status !== "waiting" ? (
                <g transform={`translate(${nodeWidth - 36}, 12)`}>
                  <rect
                    x={0}
                    y={0}
                    width={30}
                    height={17}
                    rx={4}
                    fill={SCORE_TO_COLOR(nodeState.score)}
                    opacity={0.2}
                  />
                  <text
                    x={15}
                    y={11}
                    fontSize={9}
                    fontWeight={700}
                    fill={SCORE_TO_COLOR(nodeState.score)}
                    textAnchor="middle"
                    fontFamily="'IBM Plex Mono', monospace"
                  >
                    {(nodeState.score * 100).toFixed(0)}
                  </text>
                </g>
              ) : null}

              <circle cx={0} cy={nodeHeight / 2} r={5} fill={colors.stroke} stroke={colors.bg} strokeWidth={1} />
              <circle cx={nodeWidth} cy={nodeHeight / 2} r={5} fill={colors.stroke} stroke={colors.bg} strokeWidth={1} />
            </g>
          );
        })}
        </svg>
      </div>

      <div className="grid gap-3 border-t border-zinc-800 bg-[#070707] p-3 md:grid-cols-[1.35fr_1fr]">
        <div className="rounded-lg border border-zinc-800 bg-black/40 p-3">
          {selectedNode
            ? (() => {
                const node = PIPELINE_NODES.find((candidate) => candidate.id === selectedNode);
                if (!node) {
                  return null;
                }
                const selectedState = stateFor(selectedNode);
                const colors = STATUS_COLORS[selectedState.status];
                return (
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1">
                      <div className="mb-1 flex flex-wrap items-center gap-2">
                        <span className="rounded bg-zinc-900 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-zinc-400">
                          {node.icon}
                        </span>
                        <span className="text-sm font-semibold text-white">{node.fullName}</span>
                        <span
                          className="rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase"
                          style={{ color: colors.text, backgroundColor: colors.bg, borderColor: colors.border }}
                        >
                          {selectedState.status}
                        </span>
                        {selectedState.score !== null ? (
                          <span className="text-xs font-mono" style={{ color: SCORE_TO_COLOR(selectedState.score) }}>
                            Risk: {(selectedState.score * 100).toFixed(1)}%
                          </span>
                        ) : null}
                        {selectedState.latency_ms ? (
                          <span className="text-xs font-mono text-zinc-500">{selectedState.latency_ms.toFixed(1)}ms</span>
                        ) : null}
                      </div>

                      <p className="mb-2 text-xs text-zinc-400">
                        <span className="text-zinc-600">Purpose: </span>
                        {node.description}
                      </p>

                      {selectedState.plain_english ? (
                        <div className="rounded-lg p-3 text-sm" style={{ backgroundColor: colors.bg, borderLeft: `3px solid ${colors.border}` }}>
                          <p style={{ color: colors.text }}>{selectedState.plain_english}</p>
                        </div>
                      ) : null}

                      {selectedState.reasoning ? (
                        <details className="mt-2">
                          <summary className="cursor-pointer text-xs text-zinc-600 hover:text-zinc-400">Technical details</summary>
                          <p className="mt-1 border-l border-zinc-800 pl-2 font-mono text-xs text-zinc-500">{selectedState.reasoning}</p>
                        </details>
                      ) : null}
                    </div>

                    <button
                      type="button"
                      onClick={() => setSelectedNode(null)}
                      className="text-xs text-zinc-600 transition hover:text-zinc-400"
                    >
                      close
                    </button>
                  </div>
                );
              })()
            : (
              <div className="flex h-full min-h-[140px] items-center justify-center rounded-lg border border-dashed border-zinc-800 bg-zinc-950/40 text-center text-sm text-zinc-500">
                Click any node to inspect stage reasoning, risk, and latency.
              </div>
            )}
        </div>

        <div className="rounded-lg border border-zinc-800 bg-[#090b12] p-3">
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.1em] text-zinc-500">Live Stage Monitor</p>
          <p className="mb-2 font-mono text-xs text-indigo-300">active_stage = {activeStage}</p>
          <div className="dag-code-window space-y-1 font-mono text-[11px]">
            {stageLog.length === 0 ? <p className="text-zinc-600">awaiting live events...</p> : null}
            {stageLog.map((line, idx) => (
              <p key={`${line}-${idx}`} className="dag-code-line text-zinc-300">
                <span className="mr-2 text-zinc-600">{String(idx + 1).padStart(2, "0")}</span>
                {line}
              </p>
            ))}
          </div>

          <div className="mt-3 max-h-32 overflow-auto rounded border border-zinc-900 bg-zinc-950/60 p-1">
            {filteredNodes.map((node) => {
              const state = stateFor(node.id);
              return (
                <button
                  key={node.id}
                  type="button"
                  onClick={() => setSelectedNode(node.id)}
                  className="flex w-full items-center justify-between rounded px-2 py-1 text-left text-xs text-zinc-400 transition hover:bg-zinc-900 hover:text-zinc-200"
                >
                  <span>{node.label}</span>
                  <span style={{ color: STATUS_COLORS[state.status].text }}>{state.status}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function _getDownstreamNodes(nodeId: string): string[] {
  const visited = new Set<string>();
  const queue = [nodeId];

  while (queue.length > 0) {
    const current = queue.shift();
    if (!current) {
      continue;
    }
    PIPELINE_EDGES.forEach(([src, dst]) => {
      if (src === current && !visited.has(dst)) {
        visited.add(dst);
        queue.push(dst);
      }
    });
  }

  return Array.from(visited);
}
