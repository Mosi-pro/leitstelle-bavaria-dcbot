import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Button, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Panel } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { BUG_STATUS, VERSION_STATUS, fmtDate } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";
import type { BugRow } from "./Bugs";

interface Version {
  id: number; version: string; name: string | null; status: string; release_date: string | null; description: string | null; changelog: string | null;
  bug_counts: Record<string, number>; bug_total: number;
  developers: { id: number; username: string; display_name: string | null }[]; groups: { id: number; name: string }[];
  bugs?: BugRow[];
}
interface Dir { id: number; name: string; role: string | null }
interface Group { id: number; name: string }

export default function Development() {
  const { can } = useAuth();
  const { data, error, loading } = useApi<Version[]>("/api/versions", ["versions", "bugs"]);
  const [edit, setEdit] = useState<Partial<Version> | null>(null);
  const navigate = useNavigate();
  return (
    <>
      <PageHeader title="Entwicklung" subtitle="Versionsverwaltung von Leitstelle Bavaria – Status, Changelogs, zugeordnete Bugs und Testgruppen."
        actions={can("versions.manage") && <Button variant="primary" onClick={() => setEdit({ version: "", status: "development" })}>Neue Version</Button>} />
      <ErrorBox error={error} />
      <Panel className="flush">
        <table className="table">
          <thead><tr><th>Version</th><th>Name</th><th>Status</th><th>Veröffentlichung</th><th className="num">Bugs offen</th><th className="num">Behoben</th><th>Entwickler</th><th>Testgruppen</th></tr></thead>
          <tbody>
            {(data || []).map((v) => {
              const open = (v.bug_counts.new || 0) + (v.bug_counts.claimed || 0) + (v.bug_counts.in_progress || 0);
              return (
                <tr key={v.id} className="clickable" onClick={() => navigate(`/development/${v.id}`)}>
                  <td className="mono"><strong>{v.version}</strong></td><td>{v.name || "–"}</td>
                  <td><Badge status={v.status} label={VERSION_STATUS[v.status]} /></td><td>{fmtDate(v.release_date)}</td>
                  <td className="num">{open}</td><td className="num">{v.bug_counts.fixed || 0}</td>
                  <td>{v.developers.map((d) => d.display_name || d.username).join(", ") || <span className="muted">–</span>}</td>
                  <td>{v.groups.map((g) => g.name).join(", ") || <span className="muted">–</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {loading && !data && <Loading />}
        {data && data.length === 0 && <Empty>Noch keine Versionen angelegt.</Empty>}
      </Panel>
      {edit && <VersionForm initial={edit} onClose={() => setEdit(null)} onSaved={(id) => navigate(`/development/${id}`)} />}
    </>
  );
}

function VersionForm({ initial, onClose, onSaved }: { initial: Partial<Version>; onClose: () => void; onSaved: (id: number) => void }) {
  const { data: team } = useApi<Dir[]>("/api/team/directory", ["team"]);
  const { data: groups } = useApi<Group[]>("/api/beta/groups", ["beta"]);
  const [f, setF] = useState({
    version: initial.version || "", name: initial.name || "", status: initial.status || "development", release_date: initial.release_date || "",
    description: initial.description || "", changelog: initial.changelog || "",
    developer_ids: (initial.developers || []).map((d) => d.id), group_ids: (initial.groups || []).map((g) => g.id),
  });
  const act = useAction();
  const toggle = (list: number[], id: number) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  return (
    <Modal wide title={initial.id ? `Version ${initial.version} bearbeiten` : "Neue Version"} onClose={onClose} footer={<>
      <Button onClick={onClose}>Abbrechen</Button>
      <Button variant="primary" disabled={act.busy} onClick={async () => {
        const body = { ...f, release_date: f.release_date || null };
        const r = await act.run(() => initial.id ? api.put<Version>(`/api/versions/${initial.id}`, body) : api.post<Version>("/api/versions", body));
        if (r) { onSaved(r.id); onClose(); }
      }}>Speichern</Button></>}>
      <ErrorBox error={act.error} />
      <div className="grid grid-3">
        <Field label="Versionsnummer"><input value={f.version} placeholder="0.8.2" onChange={(e) => setF({ ...f, version: e.target.value })} /></Field>
        <Field label="Versionsname"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Status"><select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
          {Object.entries(VERSION_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
      </div>
      <div className="grid grid-3">
        <Field label="Veröffentlichungsdatum"><input type="date" value={f.release_date ?? ""} onChange={(e) => setF({ ...f, release_date: e.target.value })} /></Field>
      </div>
      <Field label="Beschreibung"><textarea value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
      <Field label="Changelog"><textarea rows={8} className="mono" value={f.changelog} onChange={(e) => setF({ ...f, changelog: e.target.value })} /></Field>
      <div className="grid grid-2">
        <Field label="Zuständige Entwickler"><div className="check-grid">{(team || []).map((m) => (
          <label key={m.id} className="check"><input type="checkbox" checked={f.developer_ids.includes(m.id)} onChange={() => setF({ ...f, developer_ids: toggle(f.developer_ids, m.id) })} /> {m.name}</label>))}</div></Field>
        <Field label="Beta-Testgruppen"><div className="check-grid">{(groups || []).map((g) => (
          <label key={g.id} className="check"><input type="checkbox" checked={f.group_ids.includes(g.id)} onChange={() => setF({ ...f, group_ids: toggle(f.group_ids, g.id) })} /> {g.name}</label>))}</div></Field>
      </div>
    </Modal>
  );
}

export function VersionDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const navigate = useNavigate();
  const { data: v, error, reload } = useApi<Version>(`/api/versions/${id}`, ["versions", "bugs"]);
  const [edit, setEdit] = useState(false);
  const [assignIds, setAssignIds] = useState("");
  const act = useAction();
  if (!v) return error ? <ErrorBox error={error} /> : <Loading />;
  return (
    <>
      <PageHeader title={`Version ${v.version}${v.name ? ` – ${v.name}` : ""}`} subtitle={`${VERSION_STATUS[v.status]}${v.release_date ? ` · ${fmtDate(v.release_date)}` : ""}`} actions={<>
        <Link to="/development" className="btn">Zurück</Link>
        {can("versions.manage") && <Button onClick={() => setEdit(true)}>Bearbeiten</Button>}
        {can("versions.manage") && <Button variant="danger" onClick={async () => { if (confirm("Version löschen?")) { const r = await act.run(() => api.del(`/api/versions/${v.id}`)); if (r) navigate("/development"); } }}>Löschen</Button>}
      </>} />
      <ErrorBox error={act.error} />
      <div className="grid grid-side">
        <div className="stack">
          <Panel title="Changelog"><div className="pre mono">{v.changelog || <span className="muted">Kein Changelog hinterlegt.</span>}</div></Panel>
          <Panel title={`Zugeordnete Bugs (${v.bugs?.length || 0})`} className="flush">
            <table className="table">
              <tbody>{(v.bugs || []).map((b) => (
                <tr key={b.id}><td className="mono">{b.ref}</td><td><Link to={`/bugs/${b.id}`}>{b.title}</Link></td>
                  <td><Badge status={b.status} label={BUG_STATUS[b.status]} /></td>
                  <td className="muted">{b.fixed_in_version?.id === v.id ? "behoben in dieser Version" : "gemeldet für diese Version"}</td><td>{b.assignee?.name || "–"}</td></tr>
              ))}</tbody>
            </table>
            {can("versions.manage") && can("bugs.manage") && (
              <div className="row" style={{ padding: 10 }}>
                <input placeholder="Bug-IDs zuordnen, z. B. 12, 15, 42" value={assignIds} onChange={(e) => setAssignIds(e.target.value)} />
                <Button onClick={async () => {
                  const ids = assignIds.split(/[,\s]+/).map((s) => Number(s.replace("#", ""))).filter(Boolean);
                  await act.run(() => api.post(`/api/versions/${v.id}/bugs`, { bug_ids: ids }));
                  setAssignIds(""); reload();
                }}>Zuordnen</Button>
                <Button onClick={async () => {
                  const ids = assignIds.split(/[,\s]+/).map((s) => Number(s.replace("#", ""))).filter(Boolean);
                  await act.run(() => api.post(`/api/versions/${v.id}/bugs`, { bug_ids: ids, field: "fixed_in_version_id" }));
                  setAssignIds(""); reload();
                }}>Als „behoben in“</Button>
              </div>
            )}
          </Panel>
        </div>
        <div className="stack">
          <Panel title="Übersicht">
            <dl className="kv">
              <dt>Status</dt><dd><Badge status={v.status} label={VERSION_STATUS[v.status]} /></dd>
              <dt>Beschreibung</dt><dd className="pre">{v.description || "–"}</dd>
              <dt>Entwickler</dt><dd>{v.developers.map((d) => d.display_name || d.username).join(", ") || "–"}</dd>
              <dt>Testgruppen</dt><dd>{v.groups.map((g) => g.name).join(", ") || "–"}</dd>
              {Object.entries(BUG_STATUS).map(([k, l]) => <div key={k} style={{ display: "contents" }}><dt>Bugs: {l}</dt><dd>{v.bug_counts[k] || 0}</dd></div>)}
            </dl>
          </Panel>
        </div>
      </div>
      {edit && <VersionForm initial={v} onClose={() => { setEdit(false); reload(); }} onSaved={() => reload()} />}
    </>
  );
}
