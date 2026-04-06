"use client";

import { useAgentsStore } from "@/stores/agents";
import { AgentCard } from "@/components/ui/AgentCard";

export default function AgentsIndexPage() {
  const fleet = useAgentsStore((state) => state.fleet);

  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Agent Detail</h1>
        <p className="text-sm text-text-secondary">
          Select an agent to enter the triage workspace for decision history, score matrix, and trace replay.
        </p>
      </header>

      {fleet.length === 0 ? (
        <div className="panel p-6 text-sm text-text-secondary">
          No agents are currently registered. Start by calling POST /agents/register.
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {fleet.map((agent) => (
            <AgentCard key={agent.agent_addr} agent={agent} />
          ))}
        </div>
      )}
    </section>
  );
}
