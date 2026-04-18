import { getApiBase, getApiConfig } from "@/lib/api";
import type { StreamEvent } from "@/lib/types";

export type ConnectionState = "connected" | "reconnecting" | "disconnected";

export interface StreamOptions {
  orgId?: string;
  traceId?: string;
  eventTypes?: StreamEvent["type"][];
  getSince?: () => number;
  onEvent: (event: StreamEvent) => void;
  onConnectionChange?: (state: ConnectionState) => void;
}

function toWsBase(url: string): string {
  if (url.startsWith("https://")) {
    return `wss://${url.replace("https://", "")}`;
  }
  if (url.startsWith("http://")) {
    return `ws://${url.replace("http://", "")}`;
  }
  return url;
}

function createStreamUrl(options: StreamOptions): string {
  const params = new URLSearchParams();
  const runtimeOrg = getApiConfig().orgId;
  const orgId = options.orgId || runtimeOrg;

  if (orgId) {
    params.set("org_id", orgId);
  }
  if (options.traceId) {
    params.set("trace_id", options.traceId);
  }
  if (options.getSince) {
    const since = options.getSince();
    if (since > 0) {
      params.set("since", String(since));
    }
  }

  const qs = params.toString();
  return `${toWsBase(getApiBase())}/ws/stream${qs ? `?${qs}` : ""}`;
}

export function openRealtimeStream(options: StreamOptions): () => void {
  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let reconnectAttempts = 0;
  let disposed = false;

  const connect = () => {
    if (disposed) {
      return;
    }

    options.onConnectionChange?.("reconnecting");

    try {
      socket = new WebSocket(createStreamUrl(options));
    } catch {
      scheduleReconnect();
      return;
    }

    socket.onopen = () => {
      reconnectAttempts = 0;
      options.onConnectionChange?.("connected");
    };

    socket.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as StreamEvent;
        if (options.eventTypes && options.eventTypes.length > 0 && !options.eventTypes.includes(parsed.type)) {
          return;
        }
        options.onEvent(parsed);
      } catch {
        // Ignore malformed frames and keep stream alive.
      }
    };

    socket.onerror = () => {
      socket?.close();
    };

    socket.onclose = () => {
      if (disposed) {
        return;
      }
      scheduleReconnect();
    };
  };

  const scheduleReconnect = () => {
    options.onConnectionChange?.("reconnecting");
    const backoff = Math.min(5_000, 500 * 2 ** reconnectAttempts);
    reconnectAttempts += 1;

    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
    }

    reconnectTimer = setTimeout(connect, backoff);
  };

  connect();

  return () => {
    disposed = true;
    options.onConnectionChange?.("disconnected");
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
    }
    socket?.close();
  };
}

export function subscribeToTrace(
  traceId: string,
  onEvent: (event: StreamEvent) => void,
  options?: Omit<StreamOptions, "traceId" | "onEvent">,
): () => void {
  return openRealtimeStream({
    ...options,
    traceId,
    eventTypes: options?.eventTypes && options.eventTypes.length > 0 ? options.eventTypes : ["dag_node"],
    onEvent,
  });
}
