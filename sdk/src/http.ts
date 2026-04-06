import type { PramanaConfig } from "./types";

/** Build authenticated headers for Pramana API calls. */
export function buildPramanaHeaders(config: Pick<PramanaConfig, "orgId" | "apiToken">): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Org-Id": config.orgId,
  };

  const token = (config.apiToken ?? "").trim();
  if (token.length > 0) {
    headers.Authorization = `Bearer ${token}`;
  }

  return headers;
}
