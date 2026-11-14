import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fmtRelative } from "../lib/format";
import { useApi } from "../lib/hooks";
import { onLiveStatus } from "../lib/live";
import { Avatar, Button } from "./ui";

export const NAV = [
  { to: "/", label: "Dashboard", perm: "dashboard.view" },
  { to: "/tickets", label: "Tickets", perm: "tickets.view" },
  { to: "/applications", label: "Bewerbungen", perm: "applications.view" },
  { to: "/beta", label: "Beta-Tester", perm: "beta.view" },
  { to: "/bugs", label: "Bug-Management", perm: "bugs.view" },
  { to: "/development", label: "Entwicklung", perm: "versions.view" },
  { to: "/community", label: "Community", perm: "community.view" },
  { to: "/team", label: "Teamverwaltung", perm: "team.view" },
  { to: "/controlling", label: "Controlling", perm: "controlling.view" },
  { to: "/audit", label: "Audit-Logs", perm: "logs.view" },
  { to: "/settings", label: "Systemeinstellungen", perm: "settings.manage" },
];

interface Notif { id: number; title: string; body: string | null; link: string | null; created_at: string; read: boolean }

function Notifications() {
  const { data, reload } = useApi<{ items: Notif[]; unread: number }>("/api/notifications", ["notifications"]);
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const markAll = async () => {
    await api.post("/api/notifications/read", {});
    reload();
  };
  return (
    <div>
      <Button small variant="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>
        Benachrichtigungen{data && data.unread > 0 ? ` (${data.unread})` : ""}
      </Button>
      {open && (
        <div className="notif-list" onMouseLeave={() => setOpen(false)}>
          <div className="row" style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
            <strong className="small">Benachrichtigungen</strong>
            <span className="spacer" />
            <Button small variant="ghost" onClick={markAll}>Alle gelesen</Button>
          </div>
          {(data?.items || []).length === 0 && <div className="empty">Keine Benachrichtigungen.</div>}
          {(data?.items || []).map((n) => (
            <a key={n.id} href={n.link || "#"} className={`notif-item${n.read ? "" : " unread"}`} onClick={async (e) => {
              e.preventDefault();
              await api.post("/api/notifications/read", { ids: [n.id] });
              setOpen(false);
              reload();
              if (n.link) navigate(n.link);
            }}>
              <div>{n.title}</div>
              <div className="muted small">{n.body ? `${n.body} · ` : ""}{fmtRelative(n.created_at)}</div>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

interface SysStatus { bot: { online: boolean }; outbox: { pending: number; failed: number } }

export default function Layout() {
  const { me, can, logout } = useAuth();
  const [live, setLive] = useState(false);
  useEffect(() => onLiveStatus(setLive), []);
  const { data: status } = useApi<SysStatus>(can("dashboard.view") ? "/api/system/status" : null, ["system", "outbox"]);
  const items = NAV.filter((n) => can(n.perm));
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-title">Leitstelle Bavaria</div>
          <div className="brand-sub">Control · Verwaltung</div>
        </div>
        <nav className="nav">
          {items.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} title={n.label}>
              <span className="nav-num">{NAV.indexOf(n) + 1}</span>
              <span className="nav-label">{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div title="Discord-Bot"><span className={`status-dot ${status?.bot.online ? "ok" : "danger"}`} /><span className="nav-label">Bot {status?.bot.online ? "online" : "offline"}</span></div>
          <div title="Live-Verbindung"><span className={`status-dot ${live ? "ok" : "warn"}`} /><span className="nav-label">Live {live ? "verbunden" : "getrennt"}</span></div>
          {status && status.outbox.failed > 0 && can("settings.manage") && (
            <div><span className="status-dot danger" /><Link to="/settings?tab=sync" className="nav-label">{status.outbox.failed} Sync-Fehler</Link></div>
          )}
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <span className="spacer" />
          <Notifications />
          {me && (
            <div className="user-chip">
              <Avatar url={me.avatar_url} name={me.display_name || me.username} />
              <span>{me.display_name || me.username}</span>
              <span className="muted">{me.role || "ohne Rolle"}</span>
            </div>
          )}
          <Button small variant="ghost" onClick={logout}>Abmelden</Button>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
