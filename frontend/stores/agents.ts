import { create } from "zustand";
import type { AgentFleetItem, LifecycleEvent } from "@/lib/types";

const URGENCY_ORDER: Record<string, number> = {
  PAUSED: 0,
  DEGRADED: 1,
  RECOVERING: 2,
  ACTIVE: 3,
  UNKNOWN: 4,
};

interface AgentsState {
  fleet: AgentFleetItem[];
  setFleet: (fleet: AgentFleetItem[]) => void;
  applyLifecycle: (event: LifecycleEvent) => void;
}

const sortByUrgency = (fleet: AgentFleetItem[]) =>
  [...fleet].sort((left, right) => {
    const leftOrder = URGENCY_ORDER[left.lifecycle_state] ?? URGENCY_ORDER.UNKNOWN;
    const rightOrder = URGENCY_ORDER[right.lifecycle_state] ?? URGENCY_ORDER.UNKNOWN;

    if (leftOrder === rightOrder) {
      return right.latest_risk - left.latest_risk;
    }

    return leftOrder - rightOrder;
  });

export const useAgentsStore = create<AgentsState>((set) => ({
  fleet: [],
  setFleet: (fleet) => set(() => ({ fleet: sortByUrgency(fleet) })),
  applyLifecycle: (event) =>
    set((state) => {
      const next = [...state.fleet];
      const idx = next.findIndex((item) => item.agent_addr.toLowerCase() === event.agent_addr.toLowerCase());

      if (idx === -1) {
        next.push({
          agent_addr: event.agent_addr,
          lifecycle_state: event.to_state.toUpperCase() as AgentFleetItem["lifecycle_state"],
          profile_type: "unknown",
          latest_risk: 0,
          risk_trend: [],
        });
      } else {
        next[idx] = {
          ...next[idx],
          lifecycle_state: event.to_state.toUpperCase() as AgentFleetItem["lifecycle_state"],
        };
      }

      return { fleet: sortByUrgency(next) };
    }),
}));
