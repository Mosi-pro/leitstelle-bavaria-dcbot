// WebSocket-Live-Aktualisierung mit automatischem Reconnect (exponentielles Backoff).
import { useEffect, useRef } from "react";

type Listener = (topics: string[]) => void;

const listeners = new Set<Listener>();
let socket: WebSocket | null = null;
let retry = 0;
let timer: number | undefined;
let stopped = true;
let statusListeners = new Set<(online: boolean) => void>();

function notifyStatus(online: boolean) {
  statusListeners.forEach((l) => l(online));
}

function connect() {
  if (stopped) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/ws`);
  socket = ws;
  let ping: number | undefined;
  ws.onopen = () => {
    const wasReconnect = retry > 0;
    retry = 0;
    notifyStatus(true);
    ping = window.setInterval(() => ws.readyState === 1 && ws.send("ping"), 25000);
    // Nach Wiederverbindung alles neu laden – verpasste Änderungen nachholen
    if (wasReconnect) listeners.forEach((l) => l(["*"]));
  };
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "change") listeners.forEach((l) => l(msg.topics as string[]));
    } catch {
      /* ignorieren */
    }
  };
  ws.onclose = (ev) => {
    window.clearInterval(ping);
    notifyStatus(false);
    socket = null;
    if (stopped || ev.code === 4401) return;
    retry += 1;
    const delay = Math.min(30000, 1000 * 2 ** Math.min(retry, 5));
    timer = window.setTimeout(connect, delay);
  };
}

export function startLive() {
  if (!stopped) return;
  stopped = false;
  connect();
}

export function stopLive() {
  stopped = true;
  window.clearTimeout(timer);
  socket?.close();
}

export function onLiveStatus(fn: (online: boolean) => void) {
  statusListeners.add(fn);
  return () => {
    statusListeners.delete(fn);
  };
}

/** Ruft `reload` auf, sobald sich eines der Themen ändert (gedrosselt). */
export function useLive(topics: string[], reload: () => void) {
  const ref = useRef(reload);
  ref.current = reload;
  const key = topics.join(",");
  useEffect(() => {
    let pending: number | undefined;
    const fn: Listener = (changed) => {
      if (changed.includes("*") || changed.some((t) => topics.includes(t))) {
        window.clearTimeout(pending);
        pending = window.setTimeout(() => ref.current(), 300);
      }
    };
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
      window.clearTimeout(pending);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}

export function _resetForTests() {
  listeners.clear();
  statusListeners = new Set();
}
