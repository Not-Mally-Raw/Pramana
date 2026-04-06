import type { RealtimeMetrics, TraceRecord } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_PRAMANA_API_URL || "http://127.0.0.1:8000";
const API_JWT = process.env.NEXT_PUBLIC_PRAMANA_JWT || "";

const EMPTY_METRICS: RealtimeMetrics = {
  api_status: "down",
  active_agents: 0,
  paused_agents: 0,
  decisions_per_min: 0,
  escalation_rate_pct: 0,
  pause_rate_pct: 0,
  recovery_backlog: 0,
  pending_guardian_votes: 0,
  connected_stream_clients: 0,
  now_ts: Date.now(),
  sparkline: {
    active_agents: [],
    decisions_per_min: [],
    escalation_rate_pct: [],
    pause_rate_pct: [],
    recovery_backlog: [],
  },
  delta: {
    active_agents: 0,
    decisions_per_min: 0,
    escalation_rate_pct: 0,
    pause_rate_pct: 0,
    recovery_backlog: 0,
  },
  recent_decisions: [],
  fleet: [],
  active_recovery_alerts: [],
  lifecycle_transitions: [],
};

function buildHeaders(): HeadersInit {
  if (!API_JWT) {
    return {
      "Content-Type": "application/json",
    };
  }

  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${API_JWT}`,
  };
}

async function requestApi<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...buildHeaders(),
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });

  const payload = await response.json();
  if (!response.ok) {
    const reason = payload?.detail || payload?.error || response.statusText;
    throw new Error(typeof reason === "string" ? reason : "Request failed");
  }

  if (payload && typeof payload === "object" && "data" in payload) {
    return payload.data as T;
  }

  return payload as T;
}

export function getApiBase(): string {
  return API_BASE;
}

export interface AgentRow {
  agent_addr: string;
  did?: string;
  risk_tier?: number;
  paused?: number;
  reputation?: number;
}

export async function fetchRealtimeMetrics(): Promise<RealtimeMetrics> {
  try {
    return await requestApi<RealtimeMetrics>("/metrics/realtime");
  } catch {
    return {
      ...EMPTY_METRICS,
      now_ts: Date.now(),
    };
  }
}

export async function fetchTraces(limit = 250): Promise<TraceRecord[]> {
  try {
    return await requestApi<TraceRecord[]>(`/traces?limit=${limit}`);
  } catch {
    return [];
  }
}

export async function fetchAgents(): Promise<AgentRow[]> {
  try {
    return await requestApi<AgentRow[]>("/agents");
  } catch {
    return [];
  }
}

export async function fetchTraceReplay(flowId: string): Promise<Record<string, unknown> | null> {
  try {
    return await requestApi<Record<string, unknown>>(`/replay/${flowId}`);
  } catch {
    return null;
  }
}

export async function fetchDecisionExport(traceHash: string): Promise<Record<string, unknown> | null> {
  try {
    return await requestApi<Record<string, unknown>>(`/decisions/${traceHash}/export`);
  } catch {
    return null;
  }
}
