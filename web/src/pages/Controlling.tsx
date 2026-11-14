import { useState } from "react";
import { BarChart, Button, Empty, ErrorBox, Field, HBars, Loading, Modal, PageHeader, Panel, Stat, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fmtDate, fmtDateTime, fmtHours } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

interface Ctrl {
  period_days: number;
  tickets: { open: number; in_progress: number; waiting: number; closed: number; created_in_period: number; closed_in_period: number; avg_resolution_hours: number | null; avg_first_response_hours: number | null; per_member: { name: string; total: number; closed: number }[] };
  applications: { new: number; in_review: number; inquiry: number; accepted: number; rejected: number; total: number; acceptance_rate: number | null; avg_decision_hours: number | null; by_type: { name: string; total: number; accepted?: number; rejected?: number }[] };
  beta: { active: number; inactive: number; blocked: number; retired: number; groups: number; per_group: { name: string; count: number }[] };
  bugs: { new: number; claimed: number; in_progress: number; fixed: number; not_reproducible: number; total: number; reported_in_period: number; claimed_in_period: number; fixed_in_period: number; avg_resolution_hours: number | null; avg_claim_hours: number | null; per_developer: { name: string; claimed: number; fixed: number }[] };
  team: { active_members: number; members: { member_id: number; name: string; role: string | null; actions: number; tickets_handled: number; tickets_closed: number; bugs_claimed: number; bugs_fixed: number }[] };
  community: { member_count: number | null; joins: number; leaves: number; growth: number; history: { day: string; count: number }[] };
  timeline: { day: string; tickets: number; bugs: number; bugs_fixed: number; applications: number }[];
}
interface Report { id: number; kind: string; period_start: string; period_end: string; content: string; posted: boolean; created_by: string; created_at: string }

export default function Controlling() {
  const [tab, setTab] = useState<"stats" | "reports">("stats");
  return (
    <>
      <PageHeader title="Controlling" subtitle="Kennzahlen zu Tickets, Bewerbungen, Beta, Bugs, Team und Community – aus der Datenbank." />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "stats", label: "Statistiken" }, { key: "reports", label: "Reports" }]} />
      {tab === "stats" ? <Stats /> : <Reports />}
    </>
  );
}

function Stats() {
  const [days, setDays] = useState(30);
  const { data: c, error } = useApi<Ctrl>(`/api/controlling?days=${days}`, ["tickets", "bugs", "applications", "beta", "team", "community"]);
  if (!c) return error ? <ErrorBox error={error} /> : <Loading />;
  const series = (k: "tickets" | "bugs" | "bugs_fixed" | "applications") => c.timeline.map((t) => ({ key: t.day, label: fmtDate(t.day), value: t[k] }));
  return (
    <div className="stack">
      <div className="filters">
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 30, 90, 180, 365].map((d) => <option key={d} value={d}>Letzte {d} Tage</option>)}
        </select>
      </div>
      <div className="grid grid-2">
        <Panel title="Tickets">
          <div className="stats">
            <Stat label="Offen" value={c.tickets.open + c.tickets.in_progress + c.tickets.waiting} sub={`${c.tickets.in_progress} in Bearbeitung`} />
            <Stat label="Geschlossen (Zeitraum)" value={c.tickets.closed_in_period} sub={`${c.tickets.created_in_period} neu`} />
            <Stat label="Ø Bearbeitungszeit" value={fmtHours(c.tickets.avg_resolution_hours)} />
            <Stat label="Ø Reaktionszeit" value={fmtHours(c.tickets.avg_first_response_hours)} />
          </div>
          <h3 style={{ margin: "14px 0 6px" }}>Neue Tickets pro Tag</h3>
          <BarChart data={series("tickets")} label="Neue Tickets" />
          <h3 style={{ margin: "14px 0 6px" }}>Tickets pro Mitarbeiter (gesamt / geschlossen)</h3>
          <HBars rows={c.tickets.per_member.map((m) => ({ label: m.name, value: m.total, sub: `${m.closed} geschlossen` }))} />
        </Panel>
        <Panel title="Bugs">
          <div className="stats">
            <Stat label="Neu (Zeitraum)" value={c.bugs.reported_in_period} />
            <Stat label="Übernommen (Zeitraum)" value={c.bugs.claimed_in_period} />
            <Stat label="Behoben (Zeitraum)" value={c.bugs.fixed_in_period} />
            <Stat label="Ø Bearbeitungsdauer" value={fmtHours(c.bugs.avg_resolution_hours)} sub={`Ø bis Übernahme ${fmtHours(c.bugs.avg_claim_hours)}`} />
          </div>
          <h3 style={{ margin: "14px 0 6px" }}>Gemeldete Bugs pro Tag</h3>
          <BarChart data={series("bugs")} label="Gemeldete Bugs" />
          <h3 style={{ margin: "14px 0 6px" }}>Behobene Bugs pro Tag</h3>
          <BarChart data={series("bugs_fixed")} label="Behobene Bugs" />
          <h3 style={{ margin: "14px 0 6px" }}>Bugs pro Entwickler (übernommen)</h3>
          <HBars rows={c.bugs.per_developer.map((d) => ({ label: d.name, value: d.claimed, sub: `${d.fixed} behoben` }))} />
        </Panel>
      </div>
      <div className="grid grid-2">
        <Panel title="Bewerbungen (Zeitraum)">
          <div className="stats">
            <Stat label="Eingegangen" value={c.applications.total} />
            <Stat label="Angenommen" value={c.applications.accepted} />
            <Stat label="Abgelehnt" value={c.applications.rejected} />
            <Stat label="Annahmequote" value={c.applications.acceptance_rate === null ? "–" : `${c.applications.acceptance_rate.toLocaleString("de-DE")} %`} sub={`Ø Entscheidung ${fmtHours(c.applications.avg_decision_hours)}`} />
          </div>
          <h3 style={{ margin: "14px 0 6px" }}>Bewerbungen pro Tag</h3>
          <BarChart data={series("applications")} label="Bewerbungen" />
          <h3 style={{ margin: "14px 0 6px" }}>Nach Bewerbungsart</h3>
          <HBars rows={c.applications.by_type.map((t) => ({ label: t.name, value: t.total, sub: `${t.accepted || 0} angenommen` }))} />
        </Panel>
        <Panel title="Beta">
          <div className="stats">
            <Stat label="Aktive Tester" value={c.beta.active} />
            <Stat label="Inaktive Tester" value={c.beta.inactive} />
            <Stat label="Gesperrt / ausgeschieden" value={`${c.beta.blocked} / ${c.beta.retired}`} />
            <Stat label="Testgruppen" value={c.beta.groups} />
          </div>
          <h3 style={{ margin: "14px 0 6px" }}>Tester pro Gruppe</h3>
          <HBars rows={c.beta.per_group.map((g) => ({ label: g.name, value: g.count }))} />
          <h3 style={{ margin: "14px 0 6px" }}>Community</h3>
          <div className="stats">
            <Stat label="Mitglieder" value={c.community.member_count ?? "–"} />
            <Stat label="Wachstum (Zeitraum)" value={`${c.community.growth >= 0 ? "+" : ""}${c.community.growth}`} sub={`${c.community.joins} rein · ${c.community.leaves} raus`} />
          </div>
          {c.community.history.length > 1 && <><h3 style={{ margin: "14px 0 6px" }}>Mitglieder (Tageswerte)</h3>
            <BarChart data={c.community.history.map((h) => ({ key: h.day, label: fmtDate(h.day), value: h.count }))} label="Mitglieder" /></>}
        </Panel>
      </div>
      <Panel title="Team – Aktivität im Zeitraum" className="flush">
        <table className="table">
          <thead><tr><th>Mitarbeiter</th><th>Rolle</th><th className="num">Aktionen</th><th className="num">Bearbeitete Tickets</th><th className="num">Geschlossene Tickets</th><th className="num">Übernommene Bugs</th><th className="num">Abgeschlossene Bugs</th></tr></thead>
          <tbody>{c.team.members.map((m) => (
            <tr key={m.member_id}><td>{m.name}</td><td className="muted">{m.role || "–"}</td><td className="num">{m.actions}</td><td className="num">{m.tickets_handled}</td>
              <td className="num">{m.tickets_closed}</td><td className="num">{m.bugs_claimed}</td><td className="num">{m.bugs_fixed}</td></tr>
          ))}</tbody>
        </table>
      </Panel>
    </div>
  );
}

function Reports() {
  const { can } = useAuth();
  const { data, error, reload } = useApi<Report[]>("/api/reports", ["reports"]);
  const [view, setView] = useState<Report | null>(null);
  const [custom, setCustom] = useState<{ start: string; end: string; post: boolean } | null>(null);
  const act = useAction();
  return (
    <>
      <ErrorBox error={error || act.error} />
      <Panel title="Reports" className="flush" actions={can("reports.manage") && <>
        <Button small onClick={async () => { await act.run(() => api.post("/api/reports", {})); reload(); }}>Wochenreport (Vorwoche) erstellen</Button>
        <Button small onClick={() => setCustom({ start: "", end: "", post: false })}>Report für Zeitraum</Button></>}>
        <table className="table">
          <thead><tr><th>Zeitraum</th><th>Art</th><th>Erstellt</th><th>In Discord</th><th /></tr></thead>
          <tbody>{(data || []).map((r) => (
            <tr key={r.id}><td>{fmtDate(r.period_start)} – {fmtDate(new Date(new Date(r.period_end + "Z").getTime() - 86400000).toISOString())}</td><td>{r.kind === "weekly" ? "Wochenreport" : "Zeitraum"}</td>
              <td className="muted">{fmtDateTime(r.created_at)} · {r.created_by}</td><td>{r.posted ? "veröffentlicht" : "–"}</td>
              <td className="num"><Button small onClick={() => setView(r)}>Anzeigen</Button>{" "}
                {can("reports.manage") && !r.posted && <Button small onClick={async () => { await act.run(() => api.post(`/api/reports/${r.id}/post`)); reload(); }}>In Discord posten</Button>}</td></tr>
          ))}</tbody>
        </table>
        {data && data.length === 0 && <Empty>Noch keine Reports. Der Wochenreport wird automatisch erstellt (Systemeinstellungen → Reports).</Empty>}
      </Panel>
      {view && <Modal title="Report" onClose={() => setView(null)}><pre className="pre mono" style={{ margin: 0 }}>{view.content}</pre></Modal>}
      {custom && (
        <Modal title="Report für Zeitraum" onClose={() => setCustom(null)} footer={<>
          <Button onClick={() => setCustom(null)}>Abbrechen</Button>
          <Button variant="primary" disabled={!custom.start || !custom.end} onClick={async () => { const r = await act.run(() => api.post("/api/reports", custom)); if (r) { setCustom(null); reload(); } }}>Erstellen</Button></>}>
          <ErrorBox error={act.error} />
          <div className="grid grid-2">
            <Field label="Von"><input type="date" value={custom.start} onChange={(e) => setCustom({ ...custom, start: e.target.value })} /></Field>
            <Field label="Bis (exklusiv)"><input type="date" value={custom.end} onChange={(e) => setCustom({ ...custom, end: e.target.value })} /></Field>
          </div>
          <label className="check"><input type="checkbox" checked={custom.post} onChange={(e) => setCustom({ ...custom, post: e.target.checked })} /> Direkt im Report-Channel veröffentlichen</label>
        </Modal>
      )}
    </>
  );
}
