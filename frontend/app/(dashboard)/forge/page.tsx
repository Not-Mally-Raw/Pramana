"use client";

import { useMemo, useState } from "react";

const SCENARIOS = [
  {
    id: "inj_prompt_chain",
    category: "Injection Attacks",
    name: "Prompt Injection Cascade",
    description: "Multi-step payload attempts to bypass tool constraints and trigger unsafe call execution.",
    profiles: ["treasury_bot", "keeper_bot"],
    lastOutcome: "Detected",
  },
  {
    id: "behavior_drift_soft",
    category: "Behavioral Manipulation",
    name: "Soft Drift Conditioning",
    description: "Gradual context poisoning sequence that shifts execution policy over many rounds.",
    profiles: ["rebalancer", "market_maker"],
    lastOutcome: "Partially detected",
  },
  {
    id: "economic_liq_spike",
    category: "Economic Attacks",
    name: "Liquidation Cascade Stress",
    description: "Simulates adverse price shocks and replayed liquidation opportunities under congestion.",
    profiles: ["treasury_bot", "arb_bot"],
    lastOutcome: "Detected",
  },
  {
    id: "aa_nonce_abuse",
    category: "AA/Bundler Attacks",
    name: "Nonce Lane Abuse",
    description: "Generates invalid lane sequencing and replay attempts for ERC-4337 validation path.",
    profiles: ["arb_bot", "keeper_bot"],
    lastOutcome: "Missed",
  },
];

export default function ForgePage() {
  const [selectedScenario, setSelectedScenario] = useState(SCENARIOS[0].id);
  const [profile, setProfile] = useState("treasury_bot");
  const [rounds, setRounds] = useState(5);
  const [intensity, setIntensity] = useState<"low" | "medium" | "high">("medium");
  const [running, setRunning] = useState(false);

  const scenario = useMemo(() => SCENARIOS.find((item) => item.id === selectedScenario) || SCENARIOS[0], [selectedScenario]);

  const runSimulation = () => {
    setRunning(true);
    window.setTimeout(() => setRunning(false), 2200);
  };

  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Forge</h1>
        <p className="text-sm text-text-secondary">Scenario library and simulation controls for adversarial testing before production policy changes.</p>
      </header>

      <article className="panel p-4">
        <h2 className="card-label mb-3">Scenario Library</h2>
        <div className="grid gap-3 md:grid-cols-2">
          {SCENARIOS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setSelectedScenario(item.id)}
              className={`rounded-xl border p-4 text-left transition duration-200 ease-out ${
                selectedScenario === item.id
                  ? "border-data-0/60 bg-data-0/10"
                  : "border-border-subtle bg-surface-card hover:border-white/30"
              }`}
            >
              <p className="text-[11px] uppercase tracking-[0.08em] text-text-tertiary">{item.category}</p>
              <p className="mt-2 text-base font-semibold text-white">{item.name}</p>
              <p className="mt-1 text-sm text-text-secondary">{item.description}</p>
              <p className="mt-2 text-xs text-text-secondary">profiles: {item.profiles.join(", ")}</p>
              <p className="mt-1 text-xs text-text-secondary">last outcome: {item.lastOutcome}</p>
            </button>
          ))}
        </div>
      </article>

      <article className="panel p-4">
        <h2 className="card-label mb-3">Simulation Configuration</h2>
        <div className="grid gap-3 md:grid-cols-4">
          <select value={selectedScenario} onChange={(event) => setSelectedScenario(event.target.value)} className="rounded-lg border border-border-subtle bg-surface-card px-3 py-2 text-sm text-white">
            {SCENARIOS.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>

          <input
            value={profile}
            onChange={(event) => setProfile(event.target.value)}
            className="rounded-lg border border-border-subtle bg-surface-card px-3 py-2 text-sm text-white"
            placeholder="target profile"
          />

          <input
            type="number"
            min={1}
            max={100}
            value={rounds}
            onChange={(event) => setRounds(Number(event.target.value))}
            className="rounded-lg border border-border-subtle bg-surface-card px-3 py-2 text-sm text-white"
          />

          <select
            value={intensity}
            onChange={(event) => setIntensity(event.target.value as "low" | "medium" | "high")}
            className="rounded-lg border border-border-subtle bg-surface-card px-3 py-2 text-sm text-white"
          >
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>
        </div>

        <button
          type="button"
          onClick={runSimulation}
          disabled={running}
          className="mt-3 rounded-lg border border-data-0/50 bg-data-0/10 px-4 py-2 text-sm text-data-0 disabled:opacity-40"
        >
          {running ? "Running simulation" : "Run Simulation"}
        </button>
      </article>

      <article className="panel p-4">
        <h2 className="card-label mb-3">Before / After Comparison</h2>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border border-border-subtle bg-surface-card p-4">
            <p className="text-[11px] uppercase tracking-[0.08em] text-text-tertiary">before update</p>
            <p className="number-display mt-2 text-3xl text-white">78.4%</p>
            <p className="text-xs text-text-secondary">Detection rate</p>
          </div>
          <div className="rounded-lg border border-pass-0/35 bg-pass-0/10 p-4">
            <p className="text-[11px] uppercase tracking-[0.08em] text-pass-0/80">after update</p>
            <p className="number-display mt-2 text-3xl text-pass-0">91.2%</p>
            <p className="text-xs text-text-secondary">Detection rate</p>
          </div>
        </div>

        {running ? <p className="mt-3 text-sm text-data-0">Streaming results for {scenario.name} against {profile} at {intensity} intensity ({rounds} rounds).</p> : null}
      </article>
    </section>
  );
}
