import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setCsrfToken, setUnauthorizedHandler } from "./api";
import { startLive, stopLive } from "./live";

export interface Me {
  member_id: number;
  discord_id: string;
  username: string;
  display_name: string | null;
  avatar_url: string | null;
  role: string | null;
  is_owner: boolean;
  permissions: string[];
  csrf_token: string;
}

interface AuthState {
  me: Me | null;
  loading: boolean;
  can: (perm: string) => boolean;
  logout: () => Promise<void>;
  refresh: () => void;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    api
      .get<Me>("/api/auth/me")
      .then((m) => {
        setCsrfToken(m.csrf_token);
        setMe(m);
        startLive();
      })
      .catch(() => {
        setMe(null);
        stopLive();
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setMe(null);
      stopLive();
    });
    refresh();
  }, [refresh]);

  const logout = useCallback(async () => {
    try {
      await api.post("/api/auth/logout");
    } finally {
      stopLive();
      setMe(null);
    }
  }, []);

  const can = useCallback((perm: string) => !!me && me.permissions.includes(perm), [me]);
  return <Ctx.Provider value={{ me, loading, can, logout, refresh }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("AuthProvider fehlt");
  return v;
}
