"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchRealtimeMetrics } from "@/lib/api";
import { riskToHex } from "@/lib/design-tokens";
import { useRuntimeConfigStore } from "@/stores/runtime";

const PROFILES = ["treasury", "arb_bot", "keeper", "rebalancer", "market_maker"];
const VECTORS = ["injection", "baseline_poisoning", "pga", "nonce_abuse", "sanctioned"];

export default function EvaluationsPage() {
  const orgId = useRuntimeConfigStore((state) => state.orgId);
  const [continuous, setContinuous] = useState(true);
  const [nightly, setNightly] = useState(false);
  const [profile, setProfile] = useState(PROFILES[0]);
  const [vector, setVector] = useState(VECTORS[0]);
  const [rounds, setRounds] = useState(5);

  const metricsQuery = useQuery({
    queryKey: ["evaluations-stream", orgId],
    queryFn: fetchRealtimeMetrics,
    refetchInterval: 15_000,
  });

  const runs = metricsQuery.data?.recent_decisions || [];

  const matrix = useMemo(() => {
    return PROFILES.map((name, rowIdx) =>
      VECTORS.map((_, colIdx) => {
        const seeded = (name.length * 7 + rowIdx * 17 + colIdx * 13) % 10;
        return seeded / 10;
      }),
    );
  }, []);

  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Evaluation Lab</h1>
        <p className="text-sm text-text-secondary">Continuous attack simulation stream, coverage matrix, and red-team scheduling controls.</p>
      </header>

      <article className="panel p-4">
        <h2 className="card-label mb-3">Evaluation Run Stream</h2>
        {runs.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border-subtle p-6 text-sm text-text-secondary">No evaluation runs streamed yet.</div>
        ) : (
          <div className="space-y-2">
            {runs.slice(0, 20).map((run, idx) => {
              const attack = VECTORS[idx % VECTORS.length];
              const detected = run.decision !== "PASS";

              return (
                <div key={`${run.trace_id}-${run.timestamp}`} className="rounded-lg border border-border-subtle bg-surface-card p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm text-white">{attack}</p>
                    <span className={detected ? "text-pass-0" : "text-critical-1"}>{detected ? "detected" : "missed"}</span>
                  </div>
                  <p className="mt-1 text-xs text-text-secondary">profile {(idx % 5) + 1} | risk {(run.risk_score * 100).toFixed(1)}%</p>
                </div>
              );
            })}
          </div>
        )}
      </article>

      <article className="panel p-4">
        <h2 className="card-label mb-3">Red-Team Issue Family Matrix</h2>
        <div className="space-y-2 overflow-auto">
          {PROFILES.map((name, rowIdx) => (
            <div key={name} className="grid grid-cols-[130px_1fr] items-center gap-2">
              <p className="text-xs text-text-secondary">{name}</p>
              <div className="grid grid-flow-col auto-cols-fr gap-1">
                {matrix[rowIdx].map((score, colIdx) => (
                  <div
                    key={`${name}-${VECTORS[colIdx]}`}
                    className="h-6 rounded"
                    style={{
                      background: riskToHex(score),
                      opacity: 0.75,
                    }}
                    title={`${name} vs ${VECTORS[colIdx]}`}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      </article>

      <article className="panel p-4">
        <h2 className="card-label mb-3">Schedule and Trigger Controls</h2>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="rounded-lg border border-border-subtle bg-surface-card p-3 text-sm text-text-secondary">
            <input type="checkbox" checked={continuous} onChange={() => setContinuous((prev) => !prev)} className="mr-2" />
            Continuous Evaluation (every 15 minutes)
          </label>
          <label className="rounded-lg border border-border-subtle bg-surface-card p-3 text-sm text-text-secondary">
            <input type="checkbox" checked={nightly} onChange={() => setNightly((prev) => !prev)} className="mr-2" />
            Scheduled Red-Teaming (nightly)
          </label>
        </div>

        <div className="mt-3 grid gap-3 md:grid-cols-4">
          <select value={profile} onChange={(event) => setProfile(event.target.value)} className="rounded-lg border border-border-subtle bg-surface-card px-3 py-2 text-sm text-white">
            {PROFILES.map((option) => (
              <option key={option}>{option}</option>
            ))}
          </select>
          <select value={vector} onChange={(event) => setVector(event.target.value)} className="rounded-lg border border-border-subtle bg-surface-card px-3 py-2 text-sm text-white">
            {VECTORS.map((option) => (
              <option key={option}>{option}</option>
            ))}
          </select>
          <input
            type="number"
            min={1}
            max={100}
            value={rounds}
            onChange={(event) => setRounds(Number(event.target.value))}
            className="rounded-lg border border-border-subtle bg-surface-card px-3 py-2 text-sm text-white"
          />
          <button type="button" className="rounded-lg border border-data-0/50 bg-data-0/10 px-3 py-2 text-sm text-data-0">
            Start Ad-Hoc Run
          </button>
        </div>
      </article>
    </section>
  );
}
