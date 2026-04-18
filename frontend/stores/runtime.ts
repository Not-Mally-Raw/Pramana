import { create } from "zustand";

const STORAGE_KEYS = {
  apiBase: "pramana.apiBase",
  orgId: "pramana.orgId",
  jwt: "pramana.jwt",
} as const;

function decodeOrgIdFromJwt(token: string): string {
  if (!token) {
    return "";
  }

  try {
    const payloadSegment = token.split(".")[1];
    if (!payloadSegment) {
      return "";
    }

    const normalized = payloadSegment.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
    const decoded = globalThis.atob(padded);
    const parsed = JSON.parse(decoded) as { org_id?: unknown };
    return typeof parsed.org_id === "string" ? parsed.org_id : "";
  } catch {
    return "";
  }
}

const DEFAULT_API_BASE =
  process.env.NEXT_PUBLIC_PRAMANA_API_URL || "http://127.0.0.1:8000";
const DEFAULT_JWT = process.env.NEXT_PUBLIC_PRAMANA_JWT || "";
const DEFAULT_ORG =
  process.env.NEXT_PUBLIC_PRAMANA_ORG_ID || decodeOrgIdFromJwt(DEFAULT_JWT) || "default";

interface RuntimeConfigState {
  hydrated: boolean;
  apiBase: string;
  orgId: string;
  jwt: string;
  hydrate: () => void;
  setApiBase: (value: string) => void;
  setOrgId: (value: string) => void;
  setJwt: (value: string) => void;
}

function persistValue(key: string, value: string): void {
  if (typeof window === "undefined") {
    return;
  }
  if (!value) {
    window.localStorage.removeItem(key);
  } else {
    window.localStorage.setItem(key, value);
  }
}

function emitRuntimeChange(): void {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new Event("pramana-runtime-config-updated"));
}

export const useRuntimeConfigStore = create<RuntimeConfigState>((set) => ({
  hydrated: false,
  apiBase: DEFAULT_API_BASE,
  orgId: DEFAULT_ORG,
  jwt: DEFAULT_JWT,
  hydrate: () => {
    if (typeof window === "undefined") {
      set(() => ({ hydrated: true }));
      return;
    }

    const apiBase = window.localStorage.getItem(STORAGE_KEYS.apiBase) || DEFAULT_API_BASE;
    const jwt = window.localStorage.getItem(STORAGE_KEYS.jwt) || DEFAULT_JWT;
    const orgFromJwt = decodeOrgIdFromJwt(jwt);
    const orgStored = window.localStorage.getItem(STORAGE_KEYS.orgId) || "";
    const orgId = orgStored || orgFromJwt || DEFAULT_ORG;

    set(() => ({
      hydrated: true,
      apiBase,
      jwt,
      orgId,
    }));
  },
  setApiBase: (value) => {
    const next = value.trim() || DEFAULT_API_BASE;
    persistValue(STORAGE_KEYS.apiBase, next);
    set(() => ({ apiBase: next }));
    emitRuntimeChange();
  },
  setOrgId: (value) => {
    const next = value.trim() || "default";
    persistValue(STORAGE_KEYS.orgId, next);
    set(() => ({ orgId: next }));
    emitRuntimeChange();
  },
  setJwt: (value) => {
    const next = value.trim();
    const derivedOrg = decodeOrgIdFromJwt(next);
    persistValue(STORAGE_KEYS.jwt, next);
    set((state) => ({
      jwt: next,
      orgId: derivedOrg || state.orgId || DEFAULT_ORG,
    }));
    if (derivedOrg) {
      persistValue(STORAGE_KEYS.orgId, derivedOrg);
    }
    emitRuntimeChange();
  },
}));
