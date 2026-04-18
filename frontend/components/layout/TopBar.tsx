"use client";

import { useMemo, useState } from "react";
import clsx from "clsx";
import { useRuntimeConfigStore } from "@/stores/runtime";
import { useSystemStore } from "@/stores/system";

const ORG_OPTIONS = ["default", "demo-prof", "research", "treasury"];
const NETWORK_OPTIONS = ["Mainnet", "Sepolia", "Anvil"];
const WINDOW_OPTIONS = ["15m", "1h", "6h", "24h"];

const formatNumber = (value: number) => Intl.NumberFormat("en-US").format(Math.round(value));

export function TopBar() {
  const org = useRuntimeConfigStore((state) => state.orgId);
  const setOrg = useRuntimeConfigStore((state) => state.setOrgId);
  const apiBase = useRuntimeConfigStore((state) => state.apiBase);
  const [network, setNetwork] = useState(NETWORK_OPTIONS[2]);
  const [window, setWindow] = useState(WINDOW_OPTIONS[1]);

  const mergedOrgOptions = useMemo(() => {
    const normalized = org?.trim();
    if (!normalized) {
      return ORG_OPTIONS;
    }
    if (ORG_OPTIONS.includes(normalized)) {
      return ORG_OPTIONS;
    }
    return [normalized, ...ORG_OPTIONS];
  }, [org]);

  const apiStatus = useSystemStore((state) => state.apiStatus);
  const decisionsPerMin = useSystemStore((state) => state.decisionsPerMin);
  const activeAgentCount = useSystemStore((state) => state.activeAgentCount);
  const wsState = useSystemStore((state) => state.wsState);

  const apiTone = useMemo(() => {
    if (apiStatus === "healthy") {
      return "bg-pass-0";
    }
    if (apiStatus === "degraded") {
      return "bg-warn-0";
    }
    return "bg-critical-1";
  }, [apiStatus]);

  return (
    <header className="sticky top-0 z-20 border-b border-border-subtle bg-black/80 backdrop-blur-xl">
      <div className="grid gap-3 px-4 py-3 md:grid-cols-[1fr_auto] md:items-center md:px-8">
        <div className="flex flex-wrap items-center gap-2">
          <Selector label="Org" value={org} onChange={setOrg} options={mergedOrgOptions} />
          <Selector label="Network" value={network} onChange={setNetwork} options={NETWORK_OPTIONS} />
          <Selector label="Window" value={window} onChange={setWindow} options={WINDOW_OPTIONS} />
          <div className="rounded-full border border-border-subtle bg-surface-card px-3 py-1.5 text-xs text-text-secondary">
            API {apiBase}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-4 text-xs">
          <div className="flex items-center gap-2 rounded-full border border-border-subtle bg-surface-card px-3 py-1.5">
            <span className={clsx("h-2 w-2 rounded-full", apiTone, apiStatus === "healthy" && "animate-pulse")} />
            <span className="text-text-secondary">API</span>
            <span className="font-semibold text-white">{apiStatus}</span>
          </div>

          <div className="rounded-full border border-border-subtle bg-surface-card px-3 py-1.5">
            <span className="text-text-secondary">Decisions/min</span>{" "}
            <span className="number-display text-white">{formatNumber(decisionsPerMin)}</span>
          </div>

          <div className="rounded-full border border-border-subtle bg-surface-card px-3 py-1.5">
            <span className="text-text-secondary">Active agents</span>{" "}
            <span className="number-display text-white">{formatNumber(activeAgentCount)}</span>
          </div>

          {wsState !== "connected" ? (
            <div className="rounded-full border border-warn-0/40 bg-warn-0/10 px-3 py-1.5 text-warn-0">
              reconnecting
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}

interface SelectorProps {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}

function Selector({ label, value, options, onChange }: SelectorProps) {
  return (
    <label className="inline-flex items-center gap-2 rounded-full border border-border-subtle bg-surface-card px-3 py-1.5 text-xs text-text-secondary">
      <span>{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="rounded bg-transparent text-white outline-none"
      >
        {options.map((option) => (
          <option key={option} value={option} className="bg-surface-elevated text-white">
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}
