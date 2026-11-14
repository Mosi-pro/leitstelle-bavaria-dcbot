import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useLive } from "./live";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
  setData: (d: T) => void;
}

/** Lädt Daten von der API und aktualisiert sie automatisch bei Live-Änderungen. */
export function useApi<T>(path: string | null, topics: string[] = []): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);

  const reload = useCallback(() => {
    if (!path) return;
    const id = ++seq.current;
    setLoading(true);
    api
      .get<T>(path)
      .then((d) => {
        if (id === seq.current) {
          setData(d);
          setError(null);
        }
      })
      .catch((e: Error) => id === seq.current && setError(e.message))
      .finally(() => id === seq.current && setLoading(false));
  }, [path]);

  useEffect(() => {
    reload();
  }, [reload]);
  useLive(topics, reload);
  return { data, error, loading, reload, setData };
}

/** Führt eine Aktion aus und liefert Status/Fehler für die Oberfläche. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async <R,>(fn: () => Promise<R>): Promise<R | undefined> => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError((e as Error).message);
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run, setError };
}
