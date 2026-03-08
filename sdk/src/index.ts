/**
 * sdk/src/index.ts — Public exports for @pramana/eliza-plugin
 */

export { createPramanaPlugin, pramanaPlugin } from "./plugin";
export { CoTCapture, trace } from "./trace";
export { guard, PramanaGuard, PramanaBlockedError } from "./guard";
export type {
  ChainEvent,
  CouncilResult,
  CouncilVote,
  FlowAction,
  GuardCheckRequest,
  GuardCheckResponse,
  L1Result,
  PramanaConfig,
  TraceDAGJSON,
  TraceEdge,
  TraceNode,
} from "./types";
