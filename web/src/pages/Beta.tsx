import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Avatar, Badge, Button, DiscordPicker, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Pagination, Panel, Tabs } from "../components/ui";
import { api, qs, type Page } from "../lib/api";
import { useAuth } from "../lib/auth";
import { TESTER_STATUS, fmtDate, fmtDateTime } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

interface Tester {
  id: number; discord_id: string; username: string; display_name: string | null; avatar_url: string | null; status: string; notes: string | null;
  joined_at: string; left_at: string | null; application_id: number | null;
  groups: { id: number; name: string }[]; versions: { id: number; version: string }[];
  history?: { actor_name: string; action: string; details: string | null; created_at: string }[];
}
interface Group { id: number; name: string; description: string | null; discord_role_id: string | null; active: boolean; tester_count: number }
interface Version { id: number; version: string; status: string }
interface Invitation { id: number; title: string; message: string; created_by: string; created_at: string; version: string | null; sent: number; pending: number; failed: number }

export default function Beta() {
  const [tab, setTab] = useState<"testers" | "groups" | "invitations">("testers");
  return (
    <>
      <PageHeader title="Beta-Tester" subtitle="Tester, Testgruppen, zugeordnete Versionen und Einladungen zu Testphasen." />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "testers", label: "Tester" }, { key: "groups", label: "Testgruppen" }, { key: "invitations", label: "Einladungen" }]} />
      {tab === "testers" && <Testers />}
      {tab === "groups" && <Groups />}
      {tab === "invitations" && <Invitations />}
    </>
  );
}

function Testers() {
  const { can } = useAuth();
  const [status, setStatus] = useState("");
  const [groupId, setGroupId] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const { data: groups } = useApi<Group[]>("/api/beta/groups", ["beta"]);
  const { data, error, loading } = useApi<Page<Tester>>(`/api/beta/testers${qs({ status, group_id: groupId, q, page })}`, ["beta"]);
  const navigate = useNavigate();
  return (
    <>
      <div className="filters">
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Alle Status</option>{Object.entries(TESTER_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={groupId} onChange={(e) => setGroupId(e.target.value)}>
          <option value="">Alle Gruppen</option>{(groups || []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
        <input type="search" placeholder="Name oder Discord-ID" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="spacer" />
        {can("beta.manage") && <Button variant="primary" onClick={() => setAdding(true)}>Tester hinzufügen</Button>}
      </div>
      <ErrorBox error={error} />
      <Panel className="flush">
        <table className="table">
          <thead><tr><th>Tester</th><th>Discord-ID</th><th>Status</th><th>Testgruppen</th><th>Versionen</th><th>Seit</th></tr></thead>
          <tbody>
            {(data?.items || []).map((t) => (
              <tr key={t.id} className="clickable" onClick={() => navigate(`/beta/${t.id}`)}>
                <td><div className="row"><Avatar url={t.avatar_url} name={t.username} size={20} />{t.display_name || t.username}</div></td>
                <td className="mono">{t.discord_id}</td>
                <td><Badge status={t.status} label={TESTER_STATUS[t.status]} /></td>
                <td>{t.groups.map((g) => g.name).join(", ") || <span className="muted">–</span>}</td>
                <td>{t.versions.map((v) => v.version).join(", ") || <span className="muted">–</span>}</td>
                <td className="muted">{fmtDate(t.joined_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {loading && !data && <Loading />}
        {data && data.items.length === 0 && <Empty>Keine Tester gefunden.</Empty>}
        {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      </Panel>
      {adding && <TesterForm groups={groups || []} onClose={() => setAdding(false)} onSaved={(id) => navigate(`/beta/${id}`)} />}
    </>
  );
}

function TesterForm({ groups, onClose, onSaved }: { groups: Group[]; onClose: () => void; onSaved: (id: number) => void }) {
  const { data: versions } = useApi<Version[]>("/api/versions", ["versions"]);
  const [f, setF] = useState({ discord_id: "", username: "", notes: "", group_ids: [] as number[], version_ids: [] as number[] });
  const act = useAction();
  const toggle = (list: number[], id: number) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  return (
    <Modal title="Beta-Tester hinzufügen" onClose={onClose} footer={<>
      <Button onClick={onClose}>Abbrechen</Button>
      <Button variant="primary" disabled={act.busy || !f.discord_id} onClick={async () => { const r = await act.run(() => api.post<Tester>("/api/beta/testers", f)); if (r) onSaved(r.id); }}>Hinzufügen</Button></>}>
      <ErrorBox error={act.error} />
      <Field label="Discord-ID" hint="Rechtsklick auf den Nutzer in Discord → „ID kopieren“ (Entwicklermodus)"><input value={f.discord_id} onChange={(e) => setF({ ...f, discord_id: e.target.value.trim() })} inputMode="numeric" /></Field>
      <Field label="Name (optional, wird bei Kontakt automatisch aktualisiert)"><input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
      <Field label="Testgruppen"><div className="check-grid">{groups.map((g) => (
        <label key={g.id} className="check"><input type="checkbox" checked={f.group_ids.includes(g.id)} onChange={() => setF({ ...f, group_ids: toggle(f.group_ids, g.id) })} /> {g.name}</label>))}</div></Field>
      <Field label="Versionen"><div className="check-grid">{(versions || []).map((v) => (
        <label key={v.id} className="check"><input type="checkbox" checked={f.version_ids.includes(v.id)} onChange={() => setF({ ...f, version_ids: toggle(f.version_ids, v.id) })} /> {v.version}</label>))}</div></Field>
      <Field label="Interne Notizen"><textarea value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
    </Modal>
  );
}

export function TesterDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const navigate = useNavigate();
  const { data: t, error, reload } = useApi<Tester>(`/api/beta/testers/${id}`, ["beta"]);
  const { data: groups } = useApi<Group[]>("/api/beta/groups", ["beta"]);
  const { data: versions } = useApi<Version[]>("/api/versions", ["versions"]);
  const act = useAction();
  const [notes, setNotes] = useState<string | null>(null);
  if (!t) return error ? <ErrorBox error={error} /> : <Loading />;
  const manage = can("beta.manage");
  const update = async (body: object) => { await act.run(() => api.put(`/api/beta/testers/${t.id}`, body)); reload(); };
  const gids = t.groups.map((g) => g.id);
  const vids = t.versions.map((v) => v.id);
  return (
    <>
      <PageHeader title={t.display_name || t.username} subtitle={`Beta-Tester seit ${fmtDate(t.joined_at)}`} actions={<>
        <Link to="/beta" className="btn">Zurück</Link>
        {manage && <Button variant="danger" onClick={async () => { if (confirm("Tester wirklich entfernen?")) { const r = await act.run(() => api.del(`/api/beta/testers/${t.id}`)); if (r) navigate("/beta"); } }}>Entfernen</Button>}
      </>} />
      <ErrorBox error={act.error} />
      <div className="grid grid-side">
        <div className="stack">
          <Panel title="Testgruppen">
            <div className="check-grid">
              {(groups || []).map((g) => (
                <label key={g.id} className="check"><input type="checkbox" disabled={!manage} checked={gids.includes(g.id)}
                  onChange={() => update({ group_ids: gids.includes(g.id) ? gids.filter((x) => x !== g.id) : [...gids, g.id] })} /> {g.name}</label>
              ))}
            </div>
          </Panel>
          <Panel title="Zugeordnete Spielversionen">
            <div className="check-grid">
              {(versions || []).map((v) => (
                <label key={v.id} className="check"><input type="checkbox" disabled={!manage} checked={vids.includes(v.id)}
                  onChange={() => update({ version_ids: vids.includes(v.id) ? vids.filter((x) => x !== v.id) : [...vids, v.id] })} /> {v.version}</label>
              ))}
            </div>
          </Panel>
          <Panel title="Testerhistorie">
            <ul className="timeline">
              {(t.history || []).map((h, i) => <li key={i}><strong>{h.action}</strong>{h.details && ` – ${h.details}`}<div className="muted small">{h.actor_name} · {fmtDateTime(h.created_at)}</div></li>)}
            </ul>
          </Panel>
        </div>
        <div className="stack">
          <Panel title="Profil">
            <dl className="kv">
              <dt>Status</dt><dd>{manage ? <select value={t.status} onChange={(e) => update({ status: e.target.value })}>
                {Object.entries(TESTER_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select> : TESTER_STATUS[t.status]}</dd>
              <dt>Discord-ID</dt><dd className="mono">{t.discord_id}</dd>
              <dt>Name</dt><dd>{t.username}</dd>
              {t.left_at && <><dt>Ausgeschieden</dt><dd>{fmtDate(t.left_at)}</dd></>}
              {t.application_id && <><dt>Bewerbung</dt><dd><Link to={`/applications/${t.application_id}`}>Nr. {t.application_id}</Link></dd></>}
            </dl>
          </Panel>
          <Panel title="Interne Notizen">
            <textarea disabled={!manage} value={notes ?? t.notes ?? ""} onChange={(e) => setNotes(e.target.value)} rows={5} />
            {manage && <div style={{ marginTop: 8 }}><Button disabled={notes === null} onClick={async () => { await update({ notes }); setNotes(null); }}>Speichern</Button></div>}
          </Panel>
        </div>
      </div>
    </>
  );
}

function Groups() {
  const { can } = useAuth();
  const { data, error, reload } = useApi<Group[]>("/api/beta/groups", ["beta"]);
  const [edit, setEdit] = useState<Partial<Group> | null>(null);
  const act = useAction();
  return (
    <Panel title="Testgruppen" className="flush" actions={can("beta.manage") && <Button small variant="primary" onClick={() => setEdit({ name: "", active: true })}>Neue Testgruppe</Button>}>
      <ErrorBox error={error} />
      <table className="table">
        <thead><tr><th>Name</th><th>Beschreibung</th><th>Discord-Rolle</th><th className="num">Tester</th><th>Status</th><th /></tr></thead>
        <tbody>
          {(data || []).map((g) => (
            <tr key={g.id}><td>{g.name}</td><td className="muted">{g.description}</td><td className="mono">{g.discord_role_id || "–"}</td><td className="num">{g.tester_count}</td>
              <td><Badge status={g.active ? "active" : "inactive"} label={g.active ? "Aktiv" : "Inaktiv"} /></td>
              <td className="num">{can("beta.manage") && <><Button small onClick={() => setEdit(g)}>Bearbeiten</Button>{" "}
                <Button small variant="danger" onClick={async () => { if (confirm(`Testgruppe „${g.name}“ löschen?`)) { await act.run(() => api.del(`/api/beta/groups/${g.id}`)); reload(); } }}>Löschen</Button></>}</td></tr>
          ))}
        </tbody>
      </table>
      {data && data.length === 0 && <Empty>Noch keine Testgruppen (z. B. Closed Beta, Open Beta, Techniktest, Multiplayer-Test).</Empty>}
      {edit && (
        <Modal title={edit.id ? "Testgruppe bearbeiten" : "Neue Testgruppe"} onClose={() => setEdit(null)} footer={<>
          <Button onClick={() => setEdit(null)}>Abbrechen</Button>
          <Button variant="primary" onClick={async () => { const r = await act.run(() => edit.id ? api.put(`/api/beta/groups/${edit.id}`, edit) : api.post("/api/beta/groups", edit)); if (r) { setEdit(null); reload(); } }}>Speichern</Button></>}>
          <ErrorBox error={act.error} />
          <Field label="Name"><input value={edit.name || ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
          <Field label="Beschreibung"><input value={edit.description || ""} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
          <Field label="Discord-Rolle (wird Mitgliedern automatisch vergeben)"><DiscordPicker kind="role" value={edit.discord_role_id || ""} onChange={(v) => setEdit({ ...edit, discord_role_id: v })} /></Field>
          <label className="check"><input type="checkbox" checked={!!edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} /> Aktiv</label>
        </Modal>
      )}
    </Panel>
  );
}

function Invitations() {
  const { can } = useAuth();
  const { data, error, reload } = useApi<Invitation[]>("/api/beta/invitations", ["beta"]);
  const { data: groups } = useApi<Group[]>("/api/beta/groups", ["beta"]);
  const { data: versions } = useApi<Version[]>("/api/versions", ["versions"]);
  const [f, setF] = useState<{ title: string; message: string; group_ids: number[]; version_id: string } | null>(null);
  const act = useAction();
  return (
    <>
      <ErrorBox error={error} />
      <Panel title="Einladungen zu Testphasen" className="flush" actions={can("beta.manage") && <Button small variant="primary" onClick={() => setF({ title: "", message: "Hallo {name},\n\ndie Testphase für Version {version} beginnt jetzt!", group_ids: [], version_id: "" })}>Neue Einladung</Button>}>
        <table className="table">
          <thead><tr><th>Titel</th><th>Version</th><th>Erstellt</th><th className="num">Gesendet</th><th className="num">Ausstehend</th><th className="num">Fehlgeschlagen</th></tr></thead>
          <tbody>{(data || []).map((i) => (
            <tr key={i.id}><td>{i.title}</td><td>{i.version || "–"}</td><td className="muted">{fmtDateTime(i.created_at)} · {i.created_by}</td>
              <td className="num">{i.sent}</td><td className="num">{i.pending}</td><td className="num">{i.failed ? <Badge status="failed" label={String(i.failed)} /> : 0}</td></tr>
          ))}</tbody>
        </table>
        {data && data.length === 0 && <Empty>Noch keine Einladungen versendet.</Empty>}
      </Panel>
      {f && (
        <Modal title="Einladung zur Testphase" onClose={() => setF(null)} footer={<>
          <Button onClick={() => setF(null)}>Abbrechen</Button>
          <Button variant="primary" disabled={act.busy || !f.title || !f.group_ids.length} onClick={async () => {
            const r = await act.run(() => api.post("/api/beta/invitations", { ...f, version_id: f.version_id ? Number(f.version_id) : null }));
            if (r) { setF(null); reload(); }
          }}>Einladungen senden</Button></>}>
          <ErrorBox error={act.error} />
          <Field label="Titel"><input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
          <Field label="Version"><select value={f.version_id} onChange={(e) => setF({ ...f, version_id: e.target.value })}><option value="">–</option>{(versions || []).map((v) => <option key={v.id} value={v.id}>{v.version}</option>)}</select></Field>
          <Field label="Empfänger (aktive Tester dieser Gruppen)"><div className="check-grid">{(groups || []).map((g) => (
            <label key={g.id} className="check"><input type="checkbox" checked={f.group_ids.includes(g.id)} onChange={() => setF({ ...f, group_ids: f.group_ids.includes(g.id) ? f.group_ids.filter((x) => x !== g.id) : [...f.group_ids, g.id] })} /> {g.name} ({g.tester_count})</label>))}</div></Field>
          <Field label="Nachricht (Discord-DM)" hint="Platzhalter: {name}, {version}"><textarea rows={6} value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} maxLength={1800} /></Field>
        </Modal>
      )}
    </>
  );
}
