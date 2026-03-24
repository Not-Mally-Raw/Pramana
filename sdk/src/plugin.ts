/**
 * sdk/src/plugin.ts — Pramana ElizaOS plugin.
 * Hooks into AgentRuntime lifecycle: pre-action guard + post-action trace.
 * Register via character.plugins in your ElizaOS character file.
 */

import type { IAgentRuntime, Plugin } from "@elizaos/core";
import { PramanaGuard } from "./guard";
import { CoTCapture } from "./trace";
import type { PramanaConfig } from "./types";

export interface PramanaPluginOptions {
  apiUrl?: string;       // default: http://localhost:8000
  orgId?: string;        // required
  enabled?: boolean;     // default: true
  guardEnabled?: boolean;
  guardFailMode?: "fail_open" | "fail_closed";
  traceEnabled?: boolean;
}

function resolveConfig(runtime: IAgentRuntime, opts: PramanaPluginOptions): PramanaConfig {
  const agentId = (runtime as unknown as { agentId?: string }).agentId ?? "unknown_agent";
  return {
    apiUrl:        opts.apiUrl       ?? (runtime.getSetting?.("PRAMANA_API_URL") as string | undefined) ?? "http://localhost:8000",
    orgId:         opts.orgId        ?? (runtime.getSetting?.("PRAMANA_ORG_ID")  as string | undefined) ?? "default",
    agentId,
    enabled:       opts.enabled       ?? true,
    guardEnabled:  opts.guardEnabled  ?? true,
    guardFailMode: opts.guardFailMode ?? "fail_open",
    traceEnabled:  opts.traceEnabled  ?? true,
  };
}

export function createPramanaPlugin(opts: PramanaPluginOptions = {}): Plugin {
  let config: PramanaConfig | null = null;
  let guard: PramanaGuard | null = null;
  const activeCaptures = new Map<string, CoTCapture>();

  return {
    name: "@pramana/eliza-plugin",
    description: "Pramana on-chain AgentOps: pre-action guard + trace emission",

    async initialize(runtime: IAgentRuntime): Promise<void> {
      config = resolveConfig(runtime, opts);
      if (!config.enabled) return;

      guard = new PramanaGuard(config);
      console.info(`[Pramana] Plugin initialized for agent ${config.agentId} (org: ${config.orgId})`);
    },

    // Pre-action hook — called before each agent action
    handlers: {
      "action:pre": async (params: Record<string, unknown>) => {
        if (!config?.enabled) return;

        const actionName = String(params["actionName"] ?? "unknown");
        const payload = params["payload"];

        // Guard check
        if (guard && config.guardEnabled) {
          await guard.check({
            action: actionName,
            calldata: payload ? JSON.stringify(payload).slice(0, 512) : "",
          });
        }

        // Start trace capture
        if (config.traceEnabled) {
          const flowId = `flow_${Date.now()}_${actionName.slice(0, 8)}`;
          const capture = new CoTCapture(config, flowId);
          capture.record(`${actionName}:start`, actionName, 0.0, false, {
            payload: typeof payload === "object" ? payload : {},
          });
          activeCaptures.set(actionName, capture);
        }
      },

      // Post-action hook — called after each agent action completes
      "action:post": async (params: Record<string, unknown>) => {
        if (!config?.traceEnabled) return;

        const actionName = String(params["actionName"] ?? "unknown");
        const capture = activeCaptures.get(actionName);
        if (!capture) return;

        const error = params["error"];
        capture.record(
          `${actionName}:end`,
          error ? `${actionName}:failure` : `${actionName}:success`,
          error ? 0.8 : 0.0,
          !!error,
          { error: error ? String(error).slice(0, 100) : undefined }
        );

        await capture.emit();
        activeCaptures.delete(actionName);
      },
    },
  };
}

/** Default plugin export for direct use in character files. */
export const pramanaPlugin = createPramanaPlugin();
