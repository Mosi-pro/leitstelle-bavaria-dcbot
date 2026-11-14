import { Link } from "react-router-dom";
import { Badge, ErrorBox, Loading, PageHeader, Panel, Stat } from "../components/ui";
import { BUG_STATUS, TICKET_STATUS, fmtDateTime, fmtHours, fmtNumber, fmtRelative } from "../lib/format";
import { useApi } from "../lib/hooks";

interface Dash {
  community: { member_count: number | null; growth: number; joins: number; leaves: number; verified: number };
  tickets: { open: number; in_progress: number; waiting: number; closed: number; avg_resolution_hours: number | null; avg_first_response_hours: number | null };
  applications: { new: number; in_review: number; inquiry: number; accepted: number; rejected: number };
  beta: { active: number; inactive: number; groups: number };
  bugs: { new: number; claimed: number; in_progress: number; fixed: number; not_reproducible: number };
  team: { active_members: number; tickets_handled_7d: number; tickets_closed_7d: number; bugs_claimed_7d: number };
  recent_bugs: { id: number; title: string; status: string; assignee: string | null; created_at: string }[];
  recent_tickets: { id: number; number: number; subject: string; status: string; created_at: string }[];
  system: {
    bot: { online: boolean; last_seen: string | null; details: { latency_ms?: number; guild?: string } | null };
    database: { connected: boolean; last_success: string | null; last_error: string | null };
    api: { online: boolean };
    outbox: { pending: number; failed: number };
    errors: { id: number; component: string; message: string; created_at: string }[];
    errors_24h: number;
  };
}

const TOPICS = ["tickets", "bugs", "applications", "beta", "team", "community", "system", "outbox"];

export default function Dashboard() {
  const { data: d, error, loading } = useApi<Dash>("/api/dashboard", TOPICS);
  if (!d) return <>{loading ? <Loading /> : <ErrorBox error={error} />}</>;
  const s = d.system;
  return (
    <>
      <PageHeader title="Dashboard" subtitle="Aktueller Stand des Projekts – aktualisiert sich automatisch." />
      <ErrorBox error={error} />
      <div className="stack">
        <Panel title="System">
          <div className="stats">
            <Stat label="Discord-Bot" value={<Badge status={s.bot.online ? "ok" : "failed"} label={s.bot.online ? "Online" : "Offline"} />}
              sub={s.bot.last_seen ? `Signal ${fmtRelative(s.bot.last_seen)}${s.bot.details?.latency_ms ? ` · ${s.bot.details.latency_ms} ms` : ""}` : "noch kein Signal"} />
            <Stat label="Datenbank (STRATO)" value={<Badge status={s.database.connected ? "ok" : "failed"} label={s.database.connected ? "Verbunden" : "Fehler"} />}
              sub={`Letzter Zugriff ${fmtDateTime(s.database.last_success)}`} />
            <Stat label="API" value={<Badge status="ok" label="Online" />} sub="Backend erreichbar" />
            <Stat label="Synchronisierung" value={fmtNumber(s.outbox.pending)} sub={s.outbox.failed ? `${s.outbox.failed} fehlgeschlagen` : "wartende Discord-Aufträge"} to="/settings?tab=sync" />
            <Stat label="Fehlermeldungen (24 Std.)" value={fmtNumber(s.errors_24h)} to="/settings?tab=errors" />
          </div>
          {s.errors.length > 0 && (
            <ul className="timeline" style={{ marginTop: 10 }}>
              {s.errors.slice(0, 3).map((e) => (
                <li key={e.id}><span className="muted">{fmtDateTime(e.created_at)} · {e.component}</span> – {e.message}</li>
              ))}
            </ul>
          )}
        </Panel>

        <div className="grid grid-2">
          <Panel title="Community">
            <div className="stats">
              <Stat label="Mitglieder" value={fmtNumber(d.community.member_count)} sub={`${fmtNumber(d.community.verified)} verifiziert`} to="/community" />
              <Stat label="Wachstum (30 Tage)" value={`${d.community.growth >= 0 ? "+" : ""}${d.community.growth}`} sub={`${d.community.joins} Beitritte · ${d.community.leaves} Austritte`} />
            </div>
          </Panel>
          <Panel title="Team">
            <div className="stats">
              <Stat label="Aktive Mitarbeiter" value={d.team.active_members} to="/team" />
              <Stat label="Bearbeitete Tickets (7 T.)" value={d.team.tickets_handled_7d} sub={`${d.team.tickets_closed_7d} geschlossen`} />
              <Stat label="Übernommene Bugs (7 T.)" value={d.team.bugs_claimed_7d} />
            </div>
          </Panel>
        </div>

        <div className="grid grid-2">
          <Panel title="Tickets" actions={<Link to="/tickets">Alle Tickets</Link>}>
            <div className="stats">
              <Stat label="Offen" value={d.tickets.open} to="/tickets?status=open" />
              <Stat label="In Bearbeitung" value={d.tickets.in_progress} to="/tickets?status=in_progress" />
              <Stat label="Wartend" value={d.tickets.waiting} to="/tickets?status=waiting" />
              <Stat label="Geschlossen" value={d.tickets.closed} to="/tickets?status=closed" />
              <Stat label="Ø Bearbeitungszeit" value={fmtHours(d.tickets.avg_resolution_hours)} sub={`Ø Reaktion ${fmtHours(d.tickets.avg_first_response_hours)}`} />
            </div>
          </Panel>
          <Panel title="Bewerbungen" actions={<Link to="/applications">Alle Bewerbungen</Link>}>
            <div className="stats">
              <Stat label="Neu" value={d.applications.new} to="/applications?status=new" />
              <Stat label="In Prüfung" value={d.applications.in_review + d.applications.inquiry} sub={`${d.applications.inquiry} mit Rückfragen`} to="/applications?status=open" />
              <Stat label="Angenommen" value={d.applications.accepted} to="/applications?status=accepted" />
              <Stat label="Abgelehnt" value={d.applications.rejected} to="/applications?status=rejected" />
            </div>
          </Panel>
        </div>

        <div className="grid grid-2">
          <Panel title="Bugs" actions={<Link to="/bugs">Bug-Management</Link>}>
            <div className="stats">
              <Stat label="Neu" value={d.bugs.new} to="/bugs?status=new" />
              <Stat label="Übernommen" value={d.bugs.claimed} to="/bugs?status=claimed" />
              <Stat label="In Bearbeitung" value={d.bugs.in_progress} to="/bugs?status=in_progress" />
              <Stat label="Behoben" value={d.bugs.fixed} to="/bugs?status=fixed" />
            </div>
          </Panel>
          <Panel title="Beta" actions={<Link to="/beta">Beta-Tester</Link>}>
            <div className="stats">
              <Stat label="Aktive Tester" value={d.beta.active} />
              <Stat label="Inaktive Tester" value={d.beta.inactive} />
              <Stat label="Testgruppen" value={d.beta.groups} />
            </div>
          </Panel>
        </div>

        <div className="grid grid-2">
          <Panel title="Neueste Bugs" className="flush">
            <table className="table">
              <tbody>
                {d.recent_bugs.map((b) => (
                  <tr key={b.id}>
                    <td className="mono">#{String(b.id).padStart(4, "0")}</td>
                    <td><Link to={`/bugs/${b.id}`}>{b.title}</Link></td>
                    <td><Badge status={b.status} label={BUG_STATUS[b.status]} /></td>
                    <td className="muted">{b.assignee || "–"}</td>
                  </tr>
                ))}
                {d.recent_bugs.length === 0 && <tr><td className="muted">Noch keine Bugs erfasst.</td></tr>}
              </tbody>
            </table>
          </Panel>
          <Panel title="Aktive Tickets" className="flush">
            <table className="table">
              <tbody>
                {d.recent_tickets.map((t) => (
                  <tr key={t.id}>
                    <td className="mono">#{String(t.number).padStart(4, "0")}</td>
                    <td><Link to={`/tickets/${t.id}`}>{t.subject}</Link></td>
                    <td><Badge status={t.status} label={TICKET_STATUS[t.status]} /></td>
                    <td className="muted">{fmtRelative(t.created_at)}</td>
                  </tr>
                ))}
                {d.recent_tickets.length === 0 && <tr><td className="muted">Keine offenen Tickets.</td></tr>}
              </tbody>
            </table>
          </Panel>
        </div>
      </div>
    </>
  );
}
