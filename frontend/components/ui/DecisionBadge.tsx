import clsx from "clsx";

interface DecisionBadgeProps {
  decision: "PASS" | "ESCALATE" | "PAUSE";
}

const BADGE_STYLES = {
  PASS: "border-pass-0/40 bg-pass-0/10 text-pass-0",
  ESCALATE: "border-warn-0/40 bg-warn-0/10 text-warn-0",
  PAUSE: "border-critical-1/50 bg-critical-1/10 text-critical-1",
} as const;

export function DecisionBadge({ decision }: DecisionBadgeProps) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold tracking-wide",
        BADGE_STYLES[decision],
      )}
    >
      {decision}
    </span>
  );
}
