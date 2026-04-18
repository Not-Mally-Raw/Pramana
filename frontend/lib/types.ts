export type DecisionType = "PASS" | "ESCALATE" | "PAUSE";

export interface DecisionEvent {
  type: "decision";
  trace_id: string;
  agent_addr: string;
  org_id: string;
  decision: DecisionType;
  risk_score: number;
  top_agent?: string;
  timestamp: number;
}

export interface DagNodeEvent {
  type: "dag_node";
  trace_id: string;
  node_name: string;
  node_id?: string;
  score: number;
  flagged: boolean;
  latency_ms: number;
  reason: string;
  reasoning?: string;
  plain_english?: string;
  timestamp: number;
  org_id?: string;
}

export interface RecoveryAlertEvent {
  type: "recovery_alert";
  alert_id: string;
  agent_addr: string;
  org_id: string;
  critical_count: number;
  warning_count: number;
  safe_count: number;
  total_usd_at_risk: number;
  timestamp: number;
}

export interface LifecycleEvent {
  type: "lifecycle";
  agent_addr: string;
  org_id: string;
  from_state: string;
  to_state: string;
  reason: string;
  timestamp: number;
}

export type StreamEvent = DecisionEvent | DagNodeEvent | RecoveryAlertEvent | LifecycleEvent;

export interface AgentFleetItem {
  agent_addr: string;
  did?: string;
  profile_type: string;
  lifecycle_state: "ACTIVE" | "DEGRADED" | "RECOVERING" | "PAUSED" | "UNKNOWN";
  latest_risk: number;
  risk_trend: number[];
}

export interface MetricsSparkline {
  active_agents: number[];
  decisions_per_min: number[];
  escalation_rate_pct: number[];
  pause_rate_pct: number[];
  recovery_backlog: number[];
}

export interface MetricsDelta {
  active_agents: number;
  decisions_per_min: number;
  escalation_rate_pct: number;
  pause_rate_pct: number;
  recovery_backlog: number;
}

export interface RealtimeMetrics {
  api_status: "healthy" | "degraded" | "down";
  active_agents: number;
  paused_agents: number;
  decisions_per_min: number;
  escalation_rate_pct: number;
  pause_rate_pct: number;
  recovery_backlog: number;
  pending_guardian_votes: number;
  connected_stream_clients: number;
  now_ts: number;
  sparkline: MetricsSparkline;
  delta: MetricsDelta;
  recent_decisions: DecisionEvent[];
  fleet: AgentFleetItem[];
  active_recovery_alerts: RecoveryAlertEvent[];
  lifecycle_transitions: LifecycleEvent[];
}

export interface TraceRecord {
  flow_id: string;
  agent_addr: string;
  trace_hash: string;
  dag_json?: string;
  flag_count: number;
  risk_score: number;
  council_action: string;
  created_at: string;
}

export interface PlatformReadiness {
  org_id: string;
  api_status: "healthy" | "degraded" | "down";
  active_agents: number;
  paused_agents: number;
  decisions_per_min: number;
  connected_stream_clients: number;
  fleet_size: number;
  dag_stream_enabled: boolean;
  ready_for_live_demo: boolean;
}

export interface ProviderSlotStatus {
  slot: string;
  provider: "groq" | "cerebras" | string;
  configured: boolean;
  model: string;
}

export interface ProviderStatus {
  providers: string[];
  candidate_models: Record<string, string[]>;
  slots: ProviderSlotStatus[];
}

export interface ProviderValidationEntry {
  slot: string;
  provider: string;
  api_key: string;
  model: string;
  api_base?: string;
}

export interface ProviderValidationCheck {
  slot: string;
  provider: string;
  provider_used?: string;
  model: string;
  ok: boolean;
  latency_ms: number;
  error?: string;
  response_preview?: string;
}

export interface ProviderValidationResult {
  total: number;
  ok: number;
  failed: number;
  checks: ProviderValidationCheck[];
}
