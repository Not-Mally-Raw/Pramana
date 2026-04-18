"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { RealtimeProvider } from "@/components/providers/RealtimeProvider";
import { useRuntimeConfigStore } from "@/stores/runtime";

export function AppProviders({ children }: { children: ReactNode }) {
  const hydrateRuntime = useRuntimeConfigStore((state) => state.hydrate);
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  useEffect(() => {
    hydrateRuntime();
  }, [hydrateRuntime]);

  return (
    <QueryClientProvider client={queryClient}>
      <RealtimeProvider>{children}</RealtimeProvider>
    </QueryClientProvider>
  );
}
