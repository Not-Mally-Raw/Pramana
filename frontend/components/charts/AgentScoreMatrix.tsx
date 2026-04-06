"use client";

import { riskToHex } from "@/lib/design-tokens";

interface AgentScoreMatrixProps {
  agentNames: string[];
  values: number[][];
}

export function AgentScoreMatrix({ agentNames, values }: AgentScoreMatrixProps) {
  if (agentNames.length === 0 || values.length === 0) {
    return <div className="rounded-lg border border-dashed border-border-subtle p-6 text-sm text-text-secondary">No score matrix data available.</div>;
  }

  return (
    <div className="space-y-2 overflow-auto">
      {agentNames.map((agentName, rowIdx) => (
        <div key={agentName} className="grid grid-cols-[130px_1fr] items-center gap-2">
          <p className="truncate text-xs text-text-secondary">{agentName}</p>
          <div className="grid grid-flow-col auto-cols-fr gap-1">
            {values[rowIdx]?.map((score, colIdx) => (
              <div
                key={`${agentName}-${colIdx}`}
                className="h-5 rounded"
                style={{
                  backgroundColor: riskToHex(score),
                  opacity: 0.75,
                }}
                title={`${agentName}: ${(score * 100).toFixed(1)}%`}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
