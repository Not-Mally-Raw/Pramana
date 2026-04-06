import { create } from "zustand";

type ApiStatus = "healthy" | "degraded" | "down";
type WsState = "connected" | "reconnecting" | "disconnected";

interface SystemState {
  apiStatus: ApiStatus;
  wsState: WsState;
  decisionsPerMin: number;
  activeAgentCount: number;
  pendingGuardianVotes: number;
  connectedStreamClients: number;
  setApiStatus: (status: ApiStatus) => void;
  setWsState: (status: WsState) => void;
  setRealtimeHeader: (payload: {
    decisionsPerMin: number;
    activeAgentCount: number;
    pendingGuardianVotes: number;
    connectedStreamClients: number;
  }) => void;
}

export const useSystemStore = create<SystemState>((set) => ({
  apiStatus: "down",
  wsState: "disconnected",
  decisionsPerMin: 0,
  activeAgentCount: 0,
  pendingGuardianVotes: 0,
  connectedStreamClients: 0,
  setApiStatus: (status) => set(() => ({ apiStatus: status })),
  setWsState: (status) => set(() => ({ wsState: status })),
  setRealtimeHeader: (payload) =>
    set(() => ({
      decisionsPerMin: payload.decisionsPerMin,
      activeAgentCount: payload.activeAgentCount,
      pendingGuardianVotes: payload.pendingGuardianVotes,
      connectedStreamClients: payload.connectedStreamClients,
    })),
}));
