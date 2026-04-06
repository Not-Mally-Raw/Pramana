import { create } from "zustand";
import type { RealtimeMetrics, RecoveryAlertEvent } from "@/lib/types";

interface AlertsState {
  alerts: RecoveryAlertEvent[];
  unresolvedCriticalCount: number;
  warningCount: number;
  totalUsdAtRisk: number;
  upsertAlert: (alert: RecoveryAlertEvent) => void;
  setFromMetrics: (metrics: RealtimeMetrics) => void;
}

function summarize(alerts: RecoveryAlertEvent[]) {
  return {
    unresolvedCriticalCount: alerts.reduce((sum, item) => sum + item.critical_count, 0),
    warningCount: alerts.reduce((sum, item) => sum + item.warning_count, 0),
    totalUsdAtRisk: alerts.reduce((sum, item) => sum + item.total_usd_at_risk, 0),
  };
}

export const useAlertsStore = create<AlertsState>((set) => ({
  alerts: [],
  unresolvedCriticalCount: 0,
  warningCount: 0,
  totalUsdAtRisk: 0,
  upsertAlert: (alert) =>
    set((state) => {
      const others = state.alerts.filter((item) => item.alert_id !== alert.alert_id);
      const alerts = [alert, ...others].sort((left, right) => right.timestamp - left.timestamp);

      return {
        alerts,
        ...summarize(alerts),
      };
    }),
  setFromMetrics: (metrics) =>
    set(() => ({
      alerts: metrics.active_recovery_alerts,
      ...summarize(metrics.active_recovery_alerts),
    })),
}));
