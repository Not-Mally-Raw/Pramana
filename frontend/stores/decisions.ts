import { create } from "zustand";
import type { DecisionEvent } from "@/lib/types";

const MAX_DECISIONS = 200;

interface DecisionsState {
  decisions: DecisionEvent[];
  lastTimestamp: number;
  livePaused: boolean;
  addDecision: (event: DecisionEvent) => void;
  addBatch: (events: DecisionEvent[]) => void;
  setLivePaused: (paused: boolean) => void;
}

function dedupeAndSort(input: DecisionEvent[]): DecisionEvent[] {
  const unique = new Map<string, DecisionEvent>();

  for (const event of input) {
    unique.set(`${event.trace_id}:${event.timestamp}`, event);
  }

  return Array.from(unique.values())
    .sort((a, b) => b.timestamp - a.timestamp)
    .slice(0, MAX_DECISIONS);
}

export const useDecisionsStore = create<DecisionsState>((set) => ({
  decisions: [],
  lastTimestamp: 0,
  livePaused: false,
  addDecision: (event) =>
    set((state) => {
      const decisions = dedupeAndSort([event, ...state.decisions]);
      return {
        decisions,
        lastTimestamp: Math.max(state.lastTimestamp, event.timestamp),
      };
    }),
  addBatch: (events) =>
    set((state) => {
      if (events.length === 0) {
        return state;
      }

      const decisions = dedupeAndSort([...events, ...state.decisions]);
      const lastTimestamp = Math.max(
        state.lastTimestamp,
        ...events.map((event) => event.timestamp),
      );

      return {
        decisions,
        lastTimestamp,
      };
    }),
  setLivePaused: (paused) => set(() => ({ livePaused: paused })),
}));
