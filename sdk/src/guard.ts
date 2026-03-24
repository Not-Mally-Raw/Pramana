/**
 * sdk/src/guard.ts — Pre-action guard hook.
 * Calls Pramana /guard/check before each agent action.
 * Blocks if injection detected (score >= 0.70).
 */

import axios from "axios";
import type { GuardCheckRequest, GuardCheckResponse, PramanaConfig } from "./types";

export class PramanaGuard {
  constructor(private readonly config: PramanaConfig) {}

  /**
   * Check an action before execution.
   * @throws Error if action is blocked AND guardEnabled is true.
   * @returns GuardCheckResponse with allowed/score/reason.
   */
  async check(params: Omit<GuardCheckRequest, "agentId" | "orgId">): Promise<GuardCheckResponse> {
    if (!(this.config.guardEnabled ?? true)) {
      return { allowed: true, score: 0.0, reason: "guard disabled", patterns: [] };
    }

    try {
      const resp = await axios.post<{ status: string; data: GuardCheckResponse }>(
        `${this.config.apiUrl}/guard/check`,
        {
          agent_id: this.config.agentId,
          org_id: this.config.orgId,
          action: params.action,
          calldata: params.calldata ?? "",
          value_wei: params.valueWei ?? 0,
          gas_price: params.gasPrice ?? 0,
        } satisfies {
          agent_id: string;
          org_id: string;
          action: string;
          calldata: string;
          value_wei: number;
          gas_price: number;
        },
        {
          headers: { "X-Org-Id": this.config.orgId },
          timeout: 3000,
        }
      );

      const result = resp.data.data;

      if (!result.allowed) {
        throw new PramanaBlockedError(
          `Action blocked by Pramana guard: ${result.reason} (score=${result.score.toFixed(2)})`,
          result
        );
      }

      return result;
    } catch (err) {
      if (err instanceof PramanaBlockedError) throw err;

      const mode = this.config.guardFailMode ?? "fail_open";
      const msg = err instanceof Error ? err.message : String(err);
      console.warn("[Pramana] Guard check unavailable:", msg);

      if (mode === "fail_closed") {
        const result: GuardCheckResponse = {
          allowed: false,
          score: 1.0,
          reason: "guard unavailable (fail closed)",
          patterns: [],
          fail_mode: "fail_closed",
        };
        throw new PramanaBlockedError(
          `Action blocked by Pramana guard: ${result.reason}`,
          result
        );
      }

      return {
        allowed: true,
        score: 0.0,
        reason: "guard unavailable (fail open)",
        patterns: [],
        fail_mode: "fail_open",
      };
    }
  }
}

export class PramanaBlockedError extends Error {
  constructor(
    message: string,
    public readonly guardResult: GuardCheckResponse
  ) {
    super(message);
    this.name = "PramanaBlockedError";
  }
}

/**
 * @guard decorator factory.
 * Automatically calls guard check before the decorated method.
 *
 * @example
 * class MyAgent {
 *   @guard(config)
 *   async executeSwap(params) { ... }
 * }
 */
export function guard(config: PramanaConfig) {
  const guardInstance = new PramanaGuard(config);

  return function (_target: unknown, propertyKey: string, descriptor: PropertyDescriptor) {
    const originalMethod = descriptor.value as (...args: unknown[]) => Promise<unknown>;

    descriptor.value = async function (...args: unknown[]) {
      // Extract action from first arg if available
      const firstArg = args[0];
      const action = propertyKey;
      const calldata =
        firstArg && typeof firstArg === "object"
          ? JSON.stringify(firstArg).slice(0, 512)
          : String(firstArg ?? "").slice(0, 512);

      await guardInstance.check({ action, calldata });
      return originalMethod.apply(this, args);
    };

    return descriptor;
  };
}
