"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchPlatformReadiness,
  fetchProviderStatus,
  validateProviderKeys,
} from "@/lib/api";
import type { ProviderValidationCheck } from "@/lib/types";
import { useRuntimeConfigStore } from "@/stores/runtime";

type ProviderName = "groq" | "cerebras";

interface SlotInput {
  slot: string;
  label: string;
  provider: ProviderName;
  model: string;
  apiBase: string;
  apiKey: string;
}

const DEFAULT_SLOTS: SlotInput[] = [
  {
    slot: "agent_1",
    label: "Agent 1",
    provider: "groq",
    model: "llama-3.1-8b-instant",
    apiBase: "",
    apiKey: "",
  },
  {
    slot: "agent_2",
    label: "Agent 2",
    provider: "groq",
    model: "llama-3.1-8b-instant",
    apiBase: "",
    apiKey: "",
  },
  {
    slot: "agent_3",
    label: "Agent 3",
    provider: "groq",
    model: "llama-3.1-8b-instant",
    apiBase: "",
    apiKey: "",
  },
  {
    slot: "agent_4",
    label: "Agent 4",
    provider: "groq",
    model: "llama-3.1-8b-instant",
    apiBase: "",
    apiKey: "",
  },
  {
    slot: "agent_5",
    label: "Agent 5",
    provider: "groq",
    model: "llama-3.1-8b-instant",
    apiBase: "",
    apiKey: "",
  },
];

export default function LaunchpadPage() {
  const orgId = useRuntimeConfigStore((state) => state.orgId);
  const [slots, setSlots] = useState<SlotInput[]>(DEFAULT_SLOTS);
  const [progress, setProgress] = useState(0);
  const [activeSlot, setActiveSlot] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<ProviderValidationCheck[]>([]);

  const readinessQuery = useQuery({
    queryKey: ["launch-readiness", orgId],
    queryFn: fetchPlatformReadiness,
    refetchInterval: 5_000,
  });

  const statusQuery = useQuery({
    queryKey: ["provider-status"],
    queryFn: fetchProviderStatus,
    staleTime: 20_000,
  });

  useEffect(() => {
    const models = statusQuery.data?.candidate_models;
    if (!models) {
      return;
    }

    setSlots((current) =>
      current.map((slot) => {
        if (slot.model.trim()) {
          return slot;
        }
        const firstModel = models[slot.provider]?.[0] || "";
        return { ...slot, model: firstModel };
      }),
    );
  }, [statusQuery.data]);

  const readinessTone = useMemo(() => {
    const status = readinessQuery.data?.api_status || "down";
    if (status === "healthy") {
      return "text-pass-0";
    }
    if (status === "degraded") {
      return "text-warn-0";
    }
    return "text-critical-1";
  }, [readinessQuery.data?.api_status]);

  const quickSummary = useMemo(() => {
    const ready = readinessQuery.data?.ready_for_live_demo;
    if (ready) {
      return "System is demo-ready for this org.";
    }
    return "System is online but no active live fleet for this org yet.";
  }, [readinessQuery.data?.ready_for_live_demo]);

  const completion = useMemo(() => {
    if (results.length === 0) {
      return null;
    }
    const ok = results.filter((item) => item.ok).length;
    return `${ok}/${results.length} provider checks passed`;
  }, [results]);

  const onSlotChange = (slotId: string, patch: Partial<SlotInput>) => {
    setSlots((current) => current.map((slot) => (slot.slot === slotId ? { ...slot, ...patch } : slot)));
  };

  const runValidation = async () => {
    setRunning(true);
    setProgress(6);
    setResults([]);

    const nextResults: ProviderValidationCheck[] = [];
    for (let idx = 0; idx < slots.length; idx += 1) {
      const slot = slots[idx];
      setActiveSlot(slot.slot);
      setProgress(Math.min(92, 8 + idx * 18));

      if (!slot.apiKey.trim()) {
        nextResults.push({
          slot: slot.slot,
          provider: slot.provider,
          model: slot.model,
          ok: false,
          latency_ms: 0,
          error: "No key pasted yet",
        });
        setResults([...nextResults]);
        continue;
      }

      try {
        const response = await validateProviderKeys([
          {
            slot: slot.slot,
            provider: slot.provider,
            api_key: slot.apiKey,
            model: slot.model,
            api_base: slot.apiBase,
          },
        ]);
        const check = response.checks[0];
        if (check) {
          nextResults.push(check);
        }
      } catch (error) {
        nextResults.push({
          slot: slot.slot,
          provider: slot.provider,
          model: slot.model,
          ok: false,
          latency_ms: 0,
          error: error instanceof Error ? error.message : "Validation failed",
        });
      }

      setResults([...nextResults]);
    }

    setProgress(100);
    setActiveSlot("");
    setRunning(false);
  };

  return (
    <section className="space-y-6">
      <header className="relative overflow-hidden rounded-2xl border border-border-subtle bg-surface-card p-6">
        <div className="absolute inset-0 opacity-40" style={{
          background:
            "radial-gradient(700px circle at 10% 10%, rgba(59,130,246,0.35), transparent 45%), radial-gradient(700px circle at 90% 0%, rgba(255,0,64,0.28), transparent 40%)",
        }} />
        <div className="absolute inset-0 chain-grid" />

        <div className="relative z-10 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight">Launchpad</h1>
            <p className="mt-2 max-w-2xl text-sm text-text-secondary">
              Validate runtime health, paste and test your 5 provider keys, then open the live Command Center with confidence.
            </p>
          </div>
          <Link
            href="/"
            className="rounded-lg border border-white/25 bg-white/10 px-4 py-2 text-sm font-semibold text-white transition hover:bg-white/20"
          >
            Open Command Center
          </Link>
        </div>
      </header>

      <article className="panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="card-label">Step 2: Live Readiness</h2>
          <span className={`text-xs font-semibold ${readinessTone}`}>
            API {readinessQuery.data?.api_status || "checking"}
          </span>
        </div>

        <p className="mb-4 text-sm text-text-secondary">{quickSummary}</p>

        <div className="grid gap-3 md:grid-cols-5">
          <ReadinessPill label="Org" value={readinessQuery.data?.org_id || orgId || "default"} />
          <ReadinessPill label="Active agents" value={String(readinessQuery.data?.active_agents ?? 0)} />
          <ReadinessPill label="Fleet size" value={String(readinessQuery.data?.fleet_size ?? 0)} />
          <ReadinessPill label="Decisions/min" value={(readinessQuery.data?.decisions_per_min ?? 0).toFixed(2)} />
          <ReadinessPill label="DAG stream" value={readinessQuery.data?.dag_stream_enabled ? "enabled" : "disabled"} />
        </div>
      </article>

      <article className="panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="card-label">Step 3: Five-Key Wiring (Preflight)</h2>
          <button
            type="button"
            onClick={runValidation}
            disabled={running}
            className="rounded-lg border border-data-0/40 bg-data-0/10 px-4 py-2 text-sm font-semibold text-data-0 disabled:opacity-50"
          >
            {running ? "Validating..." : "Validate API Keys"}
          </button>
        </div>

        <div className="mb-4 overflow-hidden rounded-xl border border-border-subtle bg-black/40">
          <div
            className="intense-progress h-3 transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
        <p className="mb-4 text-xs text-text-secondary">
          {running
            ? `Testing ${activeSlot || "provider"}...`
            : completion || "Paste keys in the requested order, then run validation."}
        </p>

        <div className="grid gap-3 lg:grid-cols-2">
          {slots.map((slot) => {
            const candidates = statusQuery.data?.candidate_models?.[slot.provider] || [];
            const result = results.find((item) => item.slot === slot.slot);
            return (
              <div key={slot.slot} className="rounded-xl border border-border-subtle bg-surface-card p-3">
                <div className="mb-2 flex items-center justify-between">
                  <p className="text-sm font-semibold text-white">{slot.label}</p>
                  {result ? (
                    <span className={`text-xs ${result.ok ? "text-pass-0" : "text-critical-1"}`}>
                      {result.ok ? "validated" : "failed"}
                    </span>
                  ) : null}
                </div>

                <div className="grid gap-2">
                  <label className="text-xs text-text-secondary">Provider</label>
                  <select
                    value={slot.provider}
                    onChange={(event) => onSlotChange(slot.slot, { provider: event.target.value as ProviderName })}
                    className="rounded-lg border border-border-subtle bg-black/40 px-3 py-2 text-sm text-white"
                  >
                    <option value="groq">groq</option>
                    <option value="cerebras">cerebras</option>
                  </select>

                  <label className="text-xs text-text-secondary">Model</label>
                  <input
                    value={slot.model}
                    onChange={(event) => onSlotChange(slot.slot, { model: event.target.value })}
                    list={`${slot.slot}-models`}
                    className="rounded-lg border border-border-subtle bg-black/40 px-3 py-2 text-sm text-white"
                  />
                  <datalist id={`${slot.slot}-models`}>
                    {candidates.map((model) => (
                      <option key={model} value={model} />
                    ))}
                  </datalist>

                  {slot.provider === "cerebras" ? (
                    <>
                      <label className="text-xs text-text-secondary">API Base</label>
                      <input
                        value={slot.apiBase}
                        onChange={(event) => onSlotChange(slot.slot, { apiBase: event.target.value })}
                        className="rounded-lg border border-border-subtle bg-black/40 px-3 py-2 text-sm text-white"
                      />
                    </>
                  ) : null}

                  <label className="text-xs text-text-secondary">API Key</label>
                  <input
                    type="password"
                    value={slot.apiKey}
                    onChange={(event) => onSlotChange(slot.slot, { apiKey: event.target.value })}
                    placeholder="Paste key"
                    className="rounded-lg border border-border-subtle bg-black/40 px-3 py-2 text-sm text-white"
                  />

                  {result?.error ? <p className="text-xs text-critical-1">{result.error}</p> : null}
                  {result?.ok ? (
                    <p className="text-xs text-pass-0">latency {result.latency_ms}ms via {result.provider_used || result.provider}</p>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      </article>

      <article className="panel p-4">
        <h2 className="card-label mb-3">What You Do Next</h2>
        <ol className="list-decimal space-y-2 pl-5 text-sm text-text-secondary">
          <li>Paste your 3 Groq keys into Agent 1-3, and 2 Cerebras keys into Agent 4-5.</li>
          <li>Run validation and confirm all five slots are green.</li>
          <li>Copy those values to scripts/agents/.env.agents and start fleet runner.</li>
          <li>Keep this page open while opening Command Center to verify live updates.</li>
        </ol>
      </article>
    </section>
  );
}

function ReadinessPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border-subtle bg-surface-card px-3 py-3">
      <p className="text-[11px] uppercase tracking-[0.08em] text-text-tertiary">{label}</p>
      <p className="number-display mt-1 text-lg text-white">{value}</p>
    </div>
  );
}
