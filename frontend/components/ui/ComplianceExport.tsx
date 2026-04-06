"use client";

import { useState } from "react";
import { fetchDecisionExport } from "@/lib/api";

export function ComplianceExport() {
  const [traceHash, setTraceHash] = useState("");
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const runExport = async () => {
    setLoading(true);
    setError("");
    const result = await fetchDecisionExport(traceHash.trim());
    if (!result) {
      setError("Unable to fetch compliance export for this trace hash.");
      setPayload(null);
      setLoading(false);
      return;
    }

    setPayload(result);
    setLoading(false);
  };

  const download = () => {
    if (!payload) {
      return;
    }

    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${traceHash || "trace"}-compliance-export.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="panel p-4">
      <h3 className="card-label">Audit Export</h3>
      <p className="mt-2 text-sm text-text-secondary">
        Fetch compliance bundle from GET /decisions/{"{trace_hash}"}/export.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          value={traceHash}
          onChange={(event) => setTraceHash(event.target.value)}
          placeholder="Enter trace hash"
          className="min-w-[260px] flex-1 rounded-lg border border-border-subtle bg-surface-elevated px-3 py-2 text-sm text-white outline-none focus:border-data-0"
        />
        <button
          type="button"
          onClick={runExport}
          disabled={!traceHash || loading}
          className="rounded-lg border border-data-0/50 bg-data-0/10 px-3 py-2 text-sm text-data-0 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {loading ? "Loading" : "Generate"}
        </button>
        <button
          type="button"
          onClick={download}
          disabled={!payload}
          className="rounded-lg border border-pass-0/50 bg-pass-0/10 px-3 py-2 text-sm text-pass-0 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Download
        </button>
      </div>

      {error ? <p className="mt-3 text-sm text-critical-1">{error}</p> : null}

      {payload ? (
        <pre className="mt-3 max-h-80 overflow-auto rounded-lg border border-border-subtle bg-black/60 p-3 text-xs text-text-secondary">
          {JSON.stringify(payload, null, 2)}
        </pre>
      ) : null}
    </section>
  );
}
