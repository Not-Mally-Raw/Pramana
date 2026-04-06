"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { PositionScatter } from "@/components/charts/PositionScatter";
import { fetchRealtimeMetrics } from "@/lib/api";

export default function RecoveryPage() {
  const metricsQuery = useQuery({
    queryKey: ["recovery-metrics"],
    queryFn: fetchRealtimeMetrics,
    refetchInterval: 8_000,
  });

  const alerts = useMemo(
    () => metricsQuery.data?.active_recovery_alerts || [],
    [metricsQuery.data],
  );

  const totals = useMemo(() => {
    return alerts.reduce(
      (acc, alert) => {
        acc.critical += alert.critical_count;
        acc.warning += alert.warning_count;
        acc.totalUsd += alert.total_usd_at_risk;
        return acc;
      },
      { critical: 0, warning: 0, totalUsd: 0 },
    );
  }, [alerts]);

  const points = useMemo(() => {
    return alerts.flatMap((alert, idx) => {
      const critical = Array.from({ length: Math.max(1, alert.critical_count) }).map((_, subIdx) => ({
        id: `${alert.alert_id}-critical-${subIdx}`,
        health_factor: Math.max(0.7, 1.0 - subIdx * 0.06),
        usd_value: Math.max(5_000, alert.total_usd_at_risk / Math.max(1, alert.critical_count + alert.warning_count)),
        risk_level: "CRITICAL" as const,
      }));

      const warning = Array.from({ length: alert.warning_count }).map((_, subIdx) => ({
        id: `${alert.alert_id}-warning-${subIdx}`,
        health_factor: 1.05 + subIdx * 0.08,
        usd_value: Math.max(2_500, alert.total_usd_at_risk * 0.6 / Math.max(1, alert.warning_count)),
        risk_level: "WARNING" as const,
      }));

      const safe = {
        id: `${alert.alert_id}-safe-${idx}`,
        health_factor: 1.45,
        usd_value: Math.max(1_200, alert.total_usd_at_risk * 0.1),
        risk_level: "SAFE" as const,
      };

      return [...critical, ...warning, safe];
    });
  }, [alerts]);

  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Recovery Operations</h1>
        <p className="text-sm text-text-secondary">Urgency view for paused agents, liquidation exposure, and webhook delivery confidence.</p>
      </header>

      <div className="grid gap-4 md:grid-cols-3">
        <div className="panel border-critical-1/30 p-4">
          <p className="card-label">Critical Positions</p>
          <p className="number-display mt-2 text-3xl text-critical-1">{totals.critical}</p>
        </div>
        <div className="panel border-warn-0/30 p-4">
          <p className="card-label">Warning Positions</p>
          <p className="number-display mt-2 text-3xl text-warn-0">{totals.warning}</p>
        </div>
        <div className="panel p-4">
          <p className="card-label">Total USD At Risk</p>
          <p className="number-display mt-2 text-3xl text-white">${totals.totalUsd.toLocaleString()}</p>
        </div>
      </div>

      <article className="panel p-4">
        <h2 className="card-label mb-3">Recovery Alert Cards</h2>
        {alerts.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border-subtle p-6 text-sm text-text-secondary">No active recovery alerts.</div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {alerts.map((alert) => (
              <div key={alert.alert_id} className="rounded-xl border border-border-subtle bg-surface-card p-4">
                <p className="font-mono text-xs text-text-secondary">{alert.agent_addr}</p>
                <p className="mt-1 text-sm text-white">paused {Math.max(1, Math.floor((Date.now() - alert.timestamp) / 60000))} minutes ago</p>

                <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/10">
                  <div
                    className="h-full bg-critical-1"
                    style={{ width: `${Math.min(100, (alert.critical_count / Math.max(1, alert.critical_count + alert.warning_count + alert.safe_count)) * 100)}%` }}
                  />
                </div>

                <div className="mt-2 flex items-center justify-between text-xs text-text-secondary">
                  <span>Critical {alert.critical_count}</span>
                  <span>Warning {alert.warning_count}</span>
                  <span>Safe {alert.safe_count}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </article>

      <article className="panel p-4">
        <h2 className="card-label mb-3">Position Risk Scatter</h2>
        <PositionScatter points={points} />
      </article>
    </section>
  );
}
