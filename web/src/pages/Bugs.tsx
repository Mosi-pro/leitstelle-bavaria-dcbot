import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Badge, Button, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Pagination, Panel } from "../components/ui";
import { api, qs, type Page } from "../lib/api";
import { useAuth } from "../lib/auth";
import { BUG_PRIORITY, BUG_STATUS, OUTBOX_STATUS, fmtDateTime, fmtRelative } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

export interface BugRow {
  id: number; ref: string; title: string; description: string; reproduction_steps: string | null; status: string; priority: string;
  reporter: { discord_id: string | null; name: string | null };
  version: { id: number; version: string } | null; version_text: string | null;
  fixed_in_version: { id: number; version: string } | null;
  assignee: { discord_id: string; name: string } | null;
  claimed_at: string | null; resolved_at: string | null; forum_thread_id: string | null; dev_message_id: string | null;
  source: string; created_at: string; updated_at: string;
}
interface Version { id: number; version: string; status: string }

export default function Bugs() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "open";
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [assignee, setAssignee] = useState("");
  const [versionId, setVersionId] = useState("");
  const [priority, setPriority] = useState("");
  const [creating, setCreating] = useState(false);
  const { data: versions } = useApi<Version[]>("/api/versions", ["versions"]);
  const { data, error, loading } = useApi<Page<BugRow>>(
    `/api/bugs${qs({ status, q, assignee, version_id: versionId, priority, page, page_size: 50 })}`, ["bugs"]);
  const navigate = useNavigate();
  return (
    <>
      <PageHeader title="Bug-Management" subtitle="Automatisch erfasste Bugs aus dem Discord-Forum – Übernahme, Bearbeitung und Synchronisierung."
        actions={can("bugs.manage") && <Button variant="primary" onClick={() => setCreating(true)}>Bug manuell erfassen</Button>} />
      <div className="filters">
        <select value={status} onChange={(e) => { setParams({ status: e.target.value }); setPage(1); }}>
          <option value="open">Alle offenen</option>
          <option value="">Alle</option>
          {Object.entries(BUG_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
          <option value="">Alle Entwickler</option>
          <option value="me">Meine Bugs</option>
          <option value="none">Nicht übernommen</option>
        </select>
        <select value={versionId} onChange={(e) => setVersionId(e.target.value)}>
          <option value="">Alle Versionen</option>
          {(versions || []).map((v) => <option key={v.id} value={v.id}>{v.version}</option>)}
        </select>
        <select value={priority} onChange={(e) => setPriority(e.target.value)}>
          <option value="">Alle Prioritäten</option>
          {Object.entries(BUG_PRIORITY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input type="search" placeholder="Suche: #ID, Titel, Melder" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
      </div>
      <ErrorBox error={error} />
      <Panel className="flush">
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Bug-ID</th><th>Titel</th><th>Status</th><th>Priorität</th><th>Version</th><th>Melder</th><th>Entwickler</th><th>Eingang</th></tr></thead>
            <tbody>
              {(data?.items || []).map((b) => (
                <tr key={b.id} className="clickable" onClick={() => navigate(`/bugs/${b.id}`)}>
                  <td className="mono">{b.ref}</td>
                  <td>{b.title}</td>
                  <td><Badge status={b.status} label={BUG_STATUS[b.status]} /></td>
                  <td><Badge status={b.priority === "critical" ? "critical" : b.priority === "high" ? "in_progress" : "muted"} label={BUG_PRIORITY[b.priority]} /></td>
                  <td>{b.version?.version || b.version_text || <span className="muted">–</span>}</td>
                  <td>{b.reporter.name || "–"}</td>
                  <td>{b.assignee?.name || <span className="muted">offen</span>}</td>
                  <td className="muted">{fmtRelative(b.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {loading && !data && <Loading />}
        {data && data.items.length === 0 && <Empty>Keine Bugs gefunden.</Empty>}
        {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      </Panel>
      {creating && <BugCreate versions={versions || []} onClose={() => setCreating(false)} onDone={(id) => navigate(`/bugs/${id}`)} />}
    </>
  );
}

function BugCreate({ versions, onClose, onDone }: { versions: Version[]; onClose: () => void; onDone: (id: number) => void }) {
  const [f, setF] = useState({ title: "", description: "", reproduction_steps: "", priority: "normal", version_id: "", notify_developers: true });
  const act = useAction();
  return (
    <Modal title="Bug manuell erfassen" onClose={onClose} footer={<>
      <Button onClick={onClose}>Abbrechen</Button>
      <Button variant="primary" disabled={!f.title.trim() || act.busy} onClick={async () => {
        const r = await act.run(() => api.post<BugRow>("/api/bugs", { ...f, version_id: f.version_id ? Number(f.version_id) : null }));
        if (r) onDone(r.id);
      }}>Erfassen</Button></>}>
      <ErrorBox error={act.error} />
      <Field label="Titel"><input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} maxLength={200} /></Field>
      <Field label="Beschreibung"><textarea value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
      <Field label="Reproduktionsschritte"><textarea value={f.reproduction_steps} onChange={(e) => setF({ ...f, reproduction_steps: e.target.value })} /></Field>
      <div className="grid grid-2">
        <Field label="Priorität"><select value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}>
          {Object.entries(BUG_PRIORITY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="Spielversion"><select value={f.version_id} onChange={(e) => setF({ ...f, version_id: e.target.value })}>
          <option value="">–</option>{versions.map((v) => <option key={v.id} value={v.id}>{v.version}</option>)}</select></Field>
      </div>
      <label className="check"><input type="checkbox" checked={f.notify_developers} onChange={(e) => setF({ ...f, notify_developers: e.target.checked })} /> Entwickler im Discord benachrichtigen</label>
    </Modal>
  );
}

interface BugDetailData extends BugRow {
  thread_url: string | null;
  attachments: { id: number; filename: string; url: string; content_type: string | null; size: number | null }[];
  comments: { id: number; author_name: string; content: string; internal: boolean; created_at: string }[];
  assignments: { developer_name: string; developer_discord_id: string; assigned_by: string; kind: string; note: string | null; assigned_at: string; ended_at: string | null }[];
  sync: { id: number; kind: string; status: string; attempts: number; last_error: string | null; created_at: string }[];
  can_edit: boolean; can_claim: boolean;
}
interface Dev { member_id: number; discord_id: string; name: string }

const SYNC_LABEL: Record<string, string> = {
  "bug.notify_developers": "Entwickler-Benachrichtigung", "bug.sync_message": "Discord-Nachricht aktualisieren",
  "bug.thread_message": "Abschluss im Forum-Thread", "bug.thread_comment": "Kommentar im Forum-Thread", "bug.dm_handover": "DM bei Übergabe",
};

export function BugDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const { data: b, error, reload } = useApi<BugDetailData>(`/api/bugs/${id}`, ["bugs", "bug_comments", "outbox"]);
  const { data: versions } = useApi<Version[]>("/api/versions", ["versions"]);
  const { data: devs } = useApi<Dev[]>("/api/bugs/developers", ["team"]);
  const act = useAction();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Partial<BugRow>>({});
  const [comment, setComment] = useState("");
  const [publicComment, setPublicComment] = useState(false);
  const [handover, setHandover] = useState<{ discord_id: string; note: string } | null>(null);
  const [statusModal, setStatusModal] = useState<string | null>(null);
  const [statusComment, setStatusComment] = useState("");
  if (!b) return error ? <ErrorBox error={error} /> : <Loading />;
  const run = async (fn: () => Promise<unknown>) => { const r = await act.run(fn); reload(); return r; };
  return (
    <>
      <PageHeader title={`Bug ${b.ref} – ${b.title}`} subtitle={`Gemeldet von ${b.reporter.name || "–"} · ${fmtDateTime(b.created_at)} · Quelle: ${b.source === "discord" ? "Discord-Forum" : "Webpanel"}`}
        actions={<>
          <Link to="/bugs" className="btn">Zurück</Link>
          {b.thread_url && <a className="btn" href={b.thread_url} target="_blank" rel="noreferrer noopener">Zum Forum-Beitrag</a>}
          {b.can_claim && <Button variant="primary" disabled={act.busy} onClick={() => run(() => api.post(`/api/bugs/${b.id}/claim`))}>Ich übernehme</Button>}
          {b.can_edit && <Button onClick={() => { setForm(b); setEditing(true); }}>Bearbeiten</Button>}
        </>} />
      <ErrorBox error={act.error} />
      <div className="grid grid-side">
        <div className="stack">
          <Panel title="Beschreibung">
            <div className="pre">{b.description || <span className="muted">Keine Beschreibung.</span>}</div>
            {b.reproduction_steps && <><h3 style={{ margin: "14px 0 6px" }}>Reproduktionsschritte</h3><div className="pre">{b.reproduction_steps}</div></>}
            {b.attachments.length > 0 && (
              <>
                <h3 style={{ margin: "14px 0 6px" }}>Anhänge</h3>
                {b.attachments.map((a) => (
                  <div key={a.id}><a href={a.url} target="_blank" rel="noreferrer noopener">{a.filename}</a> <span className="muted small">{a.content_type}</span></div>
                ))}
              </>
            )}
          </Panel>
          <Panel title="Kommentare und interne Notizen">
            {b.comments.length === 0 && <p className="muted">Noch keine Einträge.</p>}
            {b.comments.map((c) => (
              <div key={c.id} className={c.internal ? "note" : "msg msg-staff"} style={{ marginBottom: 8 }}>
                <div className="muted small">{c.author_name} · {fmtDateTime(c.created_at)} · {c.internal ? "intern" : "im Forum-Thread veröffentlicht"}</div>
                <div className="pre">{c.content}</div>
              </div>
            ))}
            <textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Notiz oder Kommentar" />
            <div className="row" style={{ marginTop: 8 }}>
              {b.can_edit && b.forum_thread_id && <label className="check" style={{ margin: 0 }}><input type="checkbox" checked={publicComment} onChange={(e) => setPublicComment(e.target.checked)} /> Im Forum-Thread veröffentlichen</label>}
              <span className="spacer" />
              <Button disabled={!comment.trim()} onClick={async () => { await run(() => api.post(`/api/bugs/${b.id}/comments`, { content: comment, internal: !publicComment })); setComment(""); setPublicComment(false); }}>Speichern</Button>
            </div>
          </Panel>
          <Panel title="Synchronisierung mit Discord" className="flush">
            <table className="table">
              <tbody>
                {b.sync.map((s) => (
                  <tr key={s.id}><td>{SYNC_LABEL[s.kind] || s.kind}</td><td><Badge status={s.status} label={OUTBOX_STATUS[s.status] || s.status} /></td><td className="num">{s.attempts} Versuch(e)</td><td className="muted small">{s.last_error}</td></tr>
                ))}
                {b.sync.length === 0 && <tr><td className="muted">Keine Synchronisationsaufträge.</td></tr>}
              </tbody>
            </table>
            {can("bugs.manage") && <div style={{ padding: 10 }}><Button small onClick={() => run(() => api.post(`/api/bugs/${b.id}/resync`))}>Erneut synchronisieren</Button></div>}
          </Panel>
        </div>
        <div className="stack">
          <Panel title="Status">
            <dl className="kv">
              <dt>Status</dt><dd>{b.can_edit ? (
                <select value={b.status} onChange={(e) => { setStatusComment(""); setStatusModal(e.target.value); }}>
                  {Object.entries(BUG_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>) : <Badge status={b.status} label={BUG_STATUS[b.status]} />}</dd>
              <dt>Priorität</dt><dd>{BUG_PRIORITY[b.priority]}</dd>
              <dt>Entwickler</dt><dd>{b.assignee ? <>{b.assignee.name}<div className="mono muted">{b.assignee.discord_id}</div></> : <span className="muted">nicht übernommen</span>}</dd>
              <dt>Übernommen</dt><dd>{fmtDateTime(b.claimed_at)}</dd>
              <dt>Abgeschlossen</dt><dd>{fmtDateTime(b.resolved_at)}</dd>
              <dt>Version</dt><dd>{b.version?.version || b.version_text || "–"}</dd>
              <dt>Behoben in</dt><dd>{b.fixed_in_version?.version || "–"}</dd>
              <dt>Forum-Thread</dt><dd className="mono">{b.forum_thread_id || "–"}</dd>
            </dl>
            {b.can_edit && (
              <div className="row wrap" style={{ marginTop: 12 }}>
                <Button small onClick={() => setHandover({ discord_id: "", note: "" })}>An Entwickler übergeben</Button>
                {can("bugs.manage") && b.assignee && <Button small variant="danger" onClick={() => run(() => api.post(`/api/bugs/${b.id}/unassign`))}>Zuweisung aufheben</Button>}
              </div>
            )}
          </Panel>
          <Panel title="Zuweisungsverlauf">
            <ul className="timeline">
              {b.assignments.map((a, i) => (
                <li key={i}><strong>{a.developer_name}</strong> – {a.kind === "claim" ? "übernommen" : a.kind === "handover" ? `übergeben von ${a.assigned_by}` : `zugewiesen von ${a.assigned_by}`}
                  <div className="muted small">{fmtDateTime(a.assigned_at)}{a.ended_at ? ` bis ${fmtDateTime(a.ended_at)}` : " · aktuell"}{a.note ? ` · ${a.note}` : ""}</div></li>
              ))}
              {b.assignments.length === 0 && <li className="muted">Noch keine Zuweisung.</li>}
            </ul>
          </Panel>
        </div>
      </div>

      {statusModal && (
        <Modal title={`Status: ${BUG_STATUS[statusModal]}`} onClose={() => setStatusModal(null)} footer={<>
          <Button onClick={() => setStatusModal(null)}>Abbrechen</Button>
          <Button variant="primary" onClick={async () => { await run(() => api.post(`/api/bugs/${b.id}/status`, { status: statusModal, comment: statusComment || null })); setStatusModal(null); }}>Übernehmen</Button></>}>
          <Field label="Kommentar (optional, intern)"><textarea value={statusComment} onChange={(e) => setStatusComment(e.target.value)} /></Field>
          {(statusModal === "fixed" || statusModal === "not_reproducible") && <p className="muted small">Die Discord-Nachricht im Entwickler-Channel wird aktualisiert; je nach Einstellung erscheint eine Nachricht im Forum-Thread.</p>}
          {statusModal === "new" && <p className="muted small">„Neu“ entfernt den zuständigen Entwickler und gibt den Bug wieder zur Übernahme frei.</p>}
        </Modal>
      )}
      {handover && (
        <Modal title="Bug übergeben" onClose={() => setHandover(null)} footer={<>
          <Button onClick={() => setHandover(null)}>Abbrechen</Button>
          <Button variant="primary" disabled={!handover.discord_id} onClick={async () => { await run(() => api.post(`/api/bugs/${b.id}/handover`, handover)); setHandover(null); }}>Übergeben</Button></>}>
          <Field label="Entwickler"><select value={handover.discord_id} onChange={(e) => setHandover({ ...handover, discord_id: e.target.value })}>
            <option value="">– auswählen –</option>
            {(devs || []).filter((d) => d.discord_id !== b.assignee?.discord_id).map((d) => <option key={d.discord_id} value={d.discord_id}>{d.name}</option>)}
          </select></Field>
          <Field label="Notiz (optional)"><input value={handover.note} onChange={(e) => setHandover({ ...handover, note: e.target.value })} maxLength={300} /></Field>
        </Modal>
      )}
      {editing && (
        <Modal title={`Bug ${b.ref} bearbeiten`} wide onClose={() => setEditing(false)} footer={<>
          <Button onClick={() => setEditing(false)}>Abbrechen</Button>
          <Button variant="primary" onClick={async () => {
            const r = await run(() => api.put(`/api/bugs/${b.id}`, {
              title: form.title, description: form.description, reproduction_steps: form.reproduction_steps ?? "", priority: form.priority,
              version_id: form.version?.id ?? null, fixed_in_version_id: form.fixed_in_version?.id ?? null,
            }));
            if (r) setEditing(false);
          }}>Speichern</Button></>}>
          <Field label="Titel"><input value={form.title || ""} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
          <Field label="Beschreibung"><textarea rows={6} value={form.description || ""} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          <Field label="Reproduktionsschritte"><textarea value={form.reproduction_steps || ""} onChange={(e) => setForm({ ...form, reproduction_steps: e.target.value })} /></Field>
          <div className="grid grid-3">
            <Field label="Priorität"><select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
              {Object.entries(BUG_PRIORITY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            <Field label="Spielversion"><select value={form.version?.id ?? ""} onChange={(e) => {
              const v = (versions || []).find((x) => x.id === Number(e.target.value));
              setForm({ ...form, version: v ? { id: v.id, version: v.version } : null });
            }}><option value="">–</option>{(versions || []).map((v) => <option key={v.id} value={v.id}>{v.version}</option>)}</select></Field>
            <Field label="Behoben in Version"><select value={form.fixed_in_version?.id ?? ""} onChange={(e) => {
              const v = (versions || []).find((x) => x.id === Number(e.target.value));
              setForm({ ...form, fixed_in_version: v ? { id: v.id, version: v.version } : null });
            }}><option value="">–</option>{(versions || []).map((v) => <option key={v.id} value={v.id}>{v.version}</option>)}</select></Field>
          </div>
        </Modal>
      )}
    </>
  );
}
