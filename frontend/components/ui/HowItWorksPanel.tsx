export function HowItWorksPanel() {
  const steps = [
    {
      icon: "IN",
      step: "1. Transaction Arrives",
      explain:
        "Before any transaction from your AI agent reaches the blockchain, it passes through Pramana first.",
    },
    {
      icon: "CK",
      step: "2. Safety Checks Run",
      explain:
        "Nine specialized detectors inspect each transaction for injection attacks, unusual behavior, economic anomalies, and sanctions violations.",
    },
    {
      icon: "RS",
      step: "3. Risk Score Computed",
      explain:
        "All detector outputs are fused into one risk score from 0% (safe) to 100% (dangerous), weighted by signal confidence.",
    },
    {
      icon: "DV",
      step: "4. Decision Made",
      explain:
        "Low risk is approved automatically, medium risk is escalated for review, and high risk is blocked before chain submission.",
    },
    {
      icon: "LG",
      step: "5. Everything Recorded",
      explain:
        "Each decision is signed and persisted with full trace context for auditability and post-incident review.",
    },
  ];

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950 p-5">
      <h3 className="mb-4 flex items-center gap-2 font-semibold text-white">
        <span className="rounded bg-zinc-900 px-2 py-0.5 text-xs font-bold text-zinc-400">SAFE</span>
        How Pramana Protects Your Agents
      </h3>
      <div className="space-y-3">
        {steps.map(({ icon, step, explain }) => (
          <div key={step} className="flex gap-3">
            <span className="mt-0.5 inline-flex h-7 min-w-7 items-center justify-center rounded bg-zinc-900 px-2 text-[10px] font-bold text-zinc-400">
              {icon}
            </span>
            <div>
              <p className="text-sm font-medium text-zinc-300">{step}</p>
              <p className="text-xs leading-relaxed text-zinc-500">{explain}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
