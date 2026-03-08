/**
 * sdk/src/trace.ts — CoT trace capture for ElizaOS AgentRuntime.
 * Wraps any async function with Pramana trace emission.
 */

import axios from "axios";
import type { PramanaConfig, TraceDAGJSON, TraceNode } from "./types";

export class CoTCapture {
  private nodes: TraceNode[] = [];
  private edges: Array<{ from: string; to: string }> = [];
  private step = 0;
  private config: PramanaConfig;
  private flowId: string;

  constructor(config: PramanaConfig, flowId?: string) {
    this.config = config;
    this.flowId = flowId ?? `flow_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  }

  /** Record a step in the trace. */
  record(name: string, action: string, score: number, flagged: boolean, metadata?: Record<string, unknown>): void {
    const node: TraceNode = { name, action, score, flagged, metadata, step: this.step++ };
    if (this.nodes.length > 0) {
      this.edges.push({ from: this.nodes[this.nodes.length - 1].name, to: name });
    }
    this.nodes.push(node);
  }

  /** Serialize current trace. */
  toJSON(): TraceDAGJSON {
    return {
      flowId: this.flowId,
      agentId: this.config.agentId,
      orgId: this.config.orgId,
      nodes: this.nodes,
      edges: this.edges,
    };
  }

  /** Emit trace to Pramana API. Fire-and-forget. */
  async emit(): Promise<void> {
    if (!(this.config.traceEnabled ?? true)) return;
    try {
      await axios.post(
        `${this.config.apiUrl}/ingest`,
        {
          agent_id: this.config.agentId,
          org_id: this.config.orgId,
          action: this.nodes[0]?.action ?? "unknown",
          calldata: JSON.stringify(this.toJSON()),
          network: "elizaos",
        },
        {
          headers: { "X-Org-Id": this.config.orgId },
          timeout: 5000,
        }
      );
    } catch {
      // Non-blocking: trace emission failure should never crash agent
    }
  }
}

/**
 * @trace decorator factory.
 * Wraps an async method to auto-capture start/end trace nodes.
 *
 * @example
 * class MyAgent {
 *   @trace(config)
 *   async sendTransaction(params) { ... }
 * }
 */
export function trace(config: PramanaConfig) {
  return function (_target: unknown, propertyKey: string, descriptor: PropertyDescriptor) {
    const originalMethod = descriptor.value as (...args: unknown[]) => Promise<unknown>;

    descriptor.value = async function (...args: unknown[]) {
      const capture = new CoTCapture(config);
      capture.record(`${propertyKey}:start`, propertyKey, 0.0, false, { argsCount: args.length });

      let result: unknown;
      try {
        result = await originalMethod.apply(this, args);
        capture.record(`${propertyKey}:end`, `${propertyKey}:success`, 0.0, false);
      } catch (err) {
        capture.record(`${propertyKey}:error`, `${propertyKey}:failure`, 1.0, true, {
          error: String(err).slice(0, 100),
        });
        await capture.emit();
        throw err;
      }

      await capture.emit();
      return result;
    };

    return descriptor;
  };
}
