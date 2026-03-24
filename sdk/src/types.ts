/**
 * sdk/src/types.ts — TypeScript interfaces mirroring Pydantic models.
 */

export interface ChainEvent {
  agentId: string;
  orgId: string;
  txHash?: string;
  action: string;
  calldata?: string;
  valueWei?: number;
  gasPrice?: number;
  network?: string;
  timestamp?: string;
}

export interface L1Result {
  agentName: string;
  flagged: boolean;
  score: number;
  reason: string;
  latencyMs: number;
  passCount: number;
  metadata: Record<string, unknown>;
}

export interface CouncilVote {
  judgeId: string;
  approved: boolean;
  riskScore: number;
  confidence: number;
  action: string;
  reason: string;
  weight: number;
  model: string;
  temperature: number;
}

export interface CouncilResult {
  approved: boolean;
  riskScore: number;
  confidence: number;
  action: FlowAction;
  reason: string;
  votes: CouncilVote[];
  quorumMet: boolean;
}

export type FlowAction = "PASS" | "ESCALATE" | "PAUSE" | "EMERGENCY";

export interface TraceNode {
  name: string;
  action: string;
  score: number;
  flagged: boolean;
  metadata?: Record<string, unknown>;
  step?: number;
}

export interface TraceEdge {
  from: string;
  to: string;
  payloadHash?: string;
}

export interface TraceDAGJSON {
  flowId: string;
  agentId: string;
  orgId: string;
  nodes: TraceNode[];
  edges: TraceEdge[];
  hash?: string;
}

export interface PramanaConfig {
  apiUrl: string;       // http://localhost:8000
  orgId: string;
  agentId: string;
  enabled?: boolean;    // default: true
  guardEnabled?: boolean; // default: true — block on injection detection
  guardFailMode?: "fail_open" | "fail_closed"; // default: fail_open
  traceEnabled?: boolean; // default: true — emit traces to API
}

export interface GuardCheckRequest {
  agentId: string;
  orgId: string;
  action: string;
  calldata?: string;
  valueWei?: number;
  gasPrice?: number;
}

export interface GuardCheckResponse {
  allowed: boolean;
  score: number;
  reason: string;
  patterns: string[];
  threshold?: number;
  fail_mode?: "fail_open" | "fail_closed";
}

export interface ApiResponse<T = unknown> {
  status: string;
  data: T;
  error?: string;
}
