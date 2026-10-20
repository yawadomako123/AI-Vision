import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { UNAUTHORIZED_EVENT, type Camera, type VisionEvent } from "./api";

export type LiveState = "connecting" | "live" | "reconnecting";

export interface LiveObject { box: [number, number, number, number]; cls: string; track: number | null; conf: number }
export interface Detections {
  camera_id: number;
  at: number; // unix seconds of the analysed frame
  width: number;
  height: number;
  objects: LiveObject[];
  preview: boolean; // a new preview image is available
}

interface LiveContextValue {
  state: LiveState;
  /** Subscribe to newly created events; returns an unsubscribe function. */
  onEvent: (fn: (e: VisionEvent) => void) => () => void;
  /** Live-wall boxes for watched cameras; returns an unsubscribe function. */
  onDetections: (fn: (d: Detections) => void) => () => void;
  /** Tell the server which cameras are on screen (empty = none). The worker only sends
   * boxes and previews for these, so the live wall costs nothing when closed. */
  watch: (cameraIds: number[]) => void;
}

const LiveContext = createContext<LiveContextValue>({
  state: "connecting", onEvent: () => () => {}, onDetections: () => () => {}, watch: () => {},
});

/** One WebSocket for the whole app; keeps React Query caches fresh from server pushes. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [state, setState] = useState<LiveState>("connecting");
  const listeners = useRef(new Set<(e: VisionEvent) => void>());
  const detectionListeners = useRef(new Set<(d: Detections) => void>());
  const watching = useRef<number[]>([]);
  const socket = useRef<WebSocket | null>(null);
  const sendWatch = () => {
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({ type: "watch", cameras: watching.current }));
    }
  };

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retryMs = 1000;
    let timer: number | undefined;
    let disposed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/api/ws`);
      socket.current = ws;

      ws.onopen = () => {
        setState("live");
        retryMs = 1000;
        sendWatch(); // the server forgets what we watched when the connection drops
        qc.invalidateQueries(); // catch up on anything missed while disconnected
      };

      ws.onmessage = (msg) => {
        const { type, data } = JSON.parse(msg.data);
        switch (type) {
          case "detections":
            detectionListeners.current.forEach((fn) => fn(data as Detections));
            break;
          case "event":
            listeners.current.forEach((fn) => fn(data as VisionEvent));
            qc.invalidateQueries({ queryKey: ["events"] });
            qc.invalidateQueries({ queryKey: ["summary"] });
            break;
          case "event_updated":
            qc.invalidateQueries({ queryKey: ["events"] });
            qc.invalidateQueries({ queryKey: ["summary"] });
            break;
          case "camera_status":
            // Patch the badge instantly, then refetch for fields the push doesn't carry (fps).
            qc.setQueryData<Camera[]>(["cameras"], (old) =>
              old?.map((c) => (c.id === data.id ? { ...c, status: data.status, last_seen_at: data.last_seen_at } : c)),
            );
            qc.invalidateQueries({ queryKey: ["cameras"] });
            qc.invalidateQueries({ queryKey: ["summary"] });
            break;
          case "arming":
            qc.setQueryData(["arming"], data);
            break;
          case "rules":
            qc.invalidateQueries({ queryKey: ["rules", data.camera_id] });
            break;
          case "camera":
          case "camera_deleted":
            qc.invalidateQueries({ queryKey: ["cameras"] });
            qc.invalidateQueries({ queryKey: ["summary"] });
            break;
        }
      };

      ws.onclose = (e) => {
        if (disposed) return;
        if (e.code === 4401) {
          // Session expired: let the auth gate show the login screen instead of retrying.
          window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
          return;
        }
        setState("reconnecting");
        timer = window.setTimeout(connect, retryMs);
        retryMs = Math.min(retryMs * 2, 15000);
      };
    };

    connect();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      ws?.close();
    };
  }, [qc]);

  const onEvent = useCallback((fn: (e: VisionEvent) => void) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  }, []);
  const onDetections = useCallback((fn: (d: Detections) => void) => {
    detectionListeners.current.add(fn);
    return () => {
      detectionListeners.current.delete(fn);
    };
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const watch = useCallback((ids: number[]) => { watching.current = ids; sendWatch(); }, []);
  const value = useMemo(() => ({ state, onEvent, onDetections, watch }), [state, onEvent, onDetections, watch]);
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive() {
  return useContext(LiveContext);
}
