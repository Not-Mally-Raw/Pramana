"use client";

import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useEffect } from "react";
import { fetchRealtimeMetrics } from "@/lib/api";
import { openRealtimeStream } from "@/lib/websocket";
import { useAlertsStore } from "@/stores/alerts";
import { useAgentsStore } from "@/stores/agents";
import { useDecisionsStore } from "@/stores/decisions";
import { useSystemStore } from "@/stores/system";

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const addDecision = useDecisionsStore((state) => state.addDecision);
  const addDecisionBatch = useDecisionsStore((state) => state.addBatch);
  const setLivePaused = useDecisionsStore((state) => state.setLivePaused);
  const applyLifecycle = useAgentsStore((state) => state.applyLifecycle);
  const setFleet = useAgentsStore((state) => state.setFleet);
  const upsertAlert = useAlertsStore((state) => state.upsertAlert);
  const setAlertsFromMetrics = useAlertsStore((state) => state.setFromMetrics);
  const setApiStatus = useSystemStore((state) => state.setApiStatus);
  const setWsState = useSystemStore((state) => state.setWsState);
  const setRealtimeHeader = useSystemStore((state) => state.setRealtimeHeader);
  const wsState = useSystemStore((state) => state.wsState);

  useEffect(() => {
    const dispose = openRealtimeStream({
      getSince: () => useDecisionsStore.getState().lastTimestamp,
      onConnectionChange: (state) => {
        setWsState(state);
        setLivePaused(state !== "connected");
      },
      onEvent: (event) => {
        if (event.type === "decision") {
          addDecision(event);
          return;
        }

        if (event.type === "recovery_alert") {
          upsertAlert(event);
          return;
        }

        if (event.type === "lifecycle") {
          applyLifecycle(event);
        }
      },
    });

    return () => {
      dispose();
    };
  }, [addDecision, applyLifecycle, setLivePaused, setWsState, upsertAlert]);

  const metricsQuery = useQuery({
    queryKey: ["realtime-provider"],
    queryFn: fetchRealtimeMetrics,
    refetchInterval: wsState === "connected" ? 15_000 : 5_000,
  });

  useEffect(() => {
    if (!metricsQuery.data) {
      return;
    }

    setApiStatus(metricsQuery.data.api_status);
    setRealtimeHeader({
      decisionsPerMin: metricsQuery.data.decisions_per_min,
      activeAgentCount: metricsQuery.data.active_agents,
      pendingGuardianVotes: metricsQuery.data.pending_guardian_votes,
      connectedStreamClients: metricsQuery.data.connected_stream_clients,
    });

    setFleet(metricsQuery.data.fleet);
    setAlertsFromMetrics(metricsQuery.data);

    if (useDecisionsStore.getState().decisions.length === 0 && metricsQuery.data.recent_decisions.length > 0) {
      addDecisionBatch(metricsQuery.data.recent_decisions);
    }
  }, [addDecisionBatch, metricsQuery.data, setAlertsFromMetrics, setApiStatus, setFleet, setRealtimeHeader]);

  return <>{children}</>;
}
