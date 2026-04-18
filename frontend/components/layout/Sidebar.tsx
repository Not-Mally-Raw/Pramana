"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  ChartScatter,
  FlaskConical,
  GitBranch,
  Gavel,
  RadioTower,
  ShieldCheck,
} from "lucide-react";
import clsx from "clsx";
import { useAlertsStore } from "@/stores/alerts";
import { useSystemStore } from "@/stores/system";

const NAV_ITEMS = [
  { href: "/launch", label: "Launchpad", Icon: Activity },
  { href: "/", label: "Command Center", Icon: Activity },
  { href: "/agents", label: "Agent Detail", Icon: ShieldCheck },
  { href: "/pipeline", label: "Flow Explorer", Icon: ChartScatter },
  { href: "/governance", label: "Governance", Icon: Gavel },
  { href: "/recovery", label: "Recovery Ops", Icon: AlertTriangle },
  { href: "/evaluations", label: "Evaluation Lab", Icon: RadioTower },
  { href: "/lifecycle", label: "Lifecycle / Audit", Icon: GitBranch },
  { href: "/forge", label: "Forge", Icon: FlaskConical },
] as const;

export function Sidebar() {
  const pathname = usePathname();
  const criticalAlerts = useAlertsStore((state) => state.unresolvedCriticalCount);
  const pendingVotes = useSystemStore((state) => state.pendingGuardianVotes);

  return (
    <aside className="group/sidebar sticky top-0 z-30 h-screen w-16 shrink-0 border-r border-border-subtle bg-black/90 p-2 transition-[width] duration-200 ease-out hover:w-60">
      <div className="mb-4 flex h-12 items-center rounded-lg border border-border-subtle bg-surface-card px-3">
        <div className="h-6 w-6 rounded bg-gradient-to-br from-chain-0 to-chain-1" />
        <span className="ml-3 overflow-hidden whitespace-nowrap text-sm font-semibold tracking-wide text-white opacity-0 transition-opacity duration-200 group-hover/sidebar:opacity-100">
          Pramana
        </span>
      </div>

      <nav className="flex flex-col gap-1">
        {NAV_ITEMS.map(({ href, label, Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          const badge =
            href === "/recovery"
              ? criticalAlerts
              : href === "/governance"
                ? pendingVotes
                : 0;

          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "relative flex h-11 items-center rounded-lg border px-3 transition-colors duration-200",
                active
                  ? "border-white/20 bg-white/[0.06]"
                  : "border-transparent text-text-secondary hover:border-border-subtle hover:bg-white/[0.04]",
              )}
            >
              <Icon className={clsx("h-4 w-4", badge > 0 ? "text-white" : "text-text-secondary")} />
              <span className="ml-3 overflow-hidden whitespace-nowrap text-xs uppercase tracking-[0.08em] opacity-0 transition-opacity duration-200 group-hover/sidebar:opacity-100">
                {label}
              </span>

              {badge > 0 ? (
                <span
                  className={clsx(
                    "ml-auto rounded-full px-2 py-0.5 text-[10px] font-semibold",
                    href === "/recovery" ? "bg-critical-1 text-white" : "bg-warn-0 text-black",
                  )}
                >
                  {badge}
                </span>
              ) : null}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
