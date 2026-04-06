"use client";

import type { ReactNode } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { useSystemStore } from "@/stores/system";

export function DashboardShell({ children }: { children: ReactNode }) {
  const wsState = useSystemStore((state) => state.wsState);

  return (
    <div className="min-h-screen bg-canvas text-text-primary">
      <div className="flex min-h-screen">
        <Sidebar />

        <main className="flex min-h-screen min-w-0 flex-1 flex-col">
          <TopBar />

          <div className="relative flex-1 px-4 pb-8 pt-4 md:px-8">
            {wsState !== "connected" ? (
              <div
                className="mb-4 h-[1px] w-full"
                style={{
                  background:
                    wsState === "reconnecting"
                      ? "linear-gradient(90deg, rgba(255,184,0,0), rgba(255,184,0,0.9), rgba(255,184,0,0))"
                      : "linear-gradient(90deg, rgba(255,0,64,0), rgba(255,0,64,0.9), rgba(255,0,64,0))",
                }}
              />
            ) : null}
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
