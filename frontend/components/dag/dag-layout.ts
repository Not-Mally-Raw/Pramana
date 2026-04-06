export interface DagNodeLayout {
  id: string;
  label: string;
  layer: number;
  x: number;
  y: number;
}

export interface DagEdge {
  source: string;
  target: string;
}

const LAYERS: string[][] = [
  ["userop_received"],
  ["sanctions"],
  ["aa_validator"],
  ["network", "operations", "hallucination"],
  ["injection", "validator"],
  ["economic_sanity"],
  ["drift"],
  ["fusion"],
  ["decision"],
];

const LABELS: Record<string, string> = {
  userop_received: "UserOp Received",
  sanctions: "Sanctions",
  aa_validator: "AA Validator",
  network: "Network",
  operations: "Operations",
  hallucination: "Hallucination",
  injection: "Injection",
  validator: "Validator",
  economic_sanity: "Economic Sanity",
  drift: "Drift",
  fusion: "Fusion",
  decision: "Decision",
};

export const DAG_EDGES: DagEdge[] = [
  { source: "userop_received", target: "sanctions" },
  { source: "sanctions", target: "aa_validator" },
  { source: "aa_validator", target: "network" },
  { source: "aa_validator", target: "operations" },
  { source: "aa_validator", target: "hallucination" },
  { source: "network", target: "injection" },
  { source: "operations", target: "injection" },
  { source: "hallucination", target: "validator" },
  { source: "network", target: "validator" },
  { source: "injection", target: "economic_sanity" },
  { source: "validator", target: "economic_sanity" },
  { source: "economic_sanity", target: "drift" },
  { source: "drift", target: "fusion" },
  { source: "fusion", target: "decision" },
];

export function buildDagLayout(): DagNodeLayout[] {
  const xGap = 170;
  const yGap = 92;

  return LAYERS.flatMap((layerNodes, layerIdx) => {
    const totalHeight = (layerNodes.length - 1) * yGap;

    return layerNodes.map((nodeId, nodeIdx) => ({
      id: nodeId,
      label: LABELS[nodeId] || nodeId,
      layer: layerIdx,
      x: 90 + layerIdx * xGap,
      y: 70 + nodeIdx * yGap - totalHeight / 2 + 150,
    }));
  });
}
