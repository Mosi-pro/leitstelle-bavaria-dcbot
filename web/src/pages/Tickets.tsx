import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Avatar, Badge, Button, DiscordPicker, Empty, ErrorBox, Field, Loading, MemberSelect, Modal, PageHeader, Pagination, Panel, Tabs } from "../components/ui";
import { api, qs, type Page } from "../lib/api";
import { useAuth } from "../lib/auth";
import { TICKET_PRIORITY, TICKET_STATUS, fmtDateTime, fmtRelative } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

export interface TicketRow {
  id: number; number: number; subject: string; status: string; priority: string;
  category: { id: number; name: string } | null;
  creator: { discord_id: string; username: string; display_name: string | null; avatar_url: string | null };
  assignee: { id: number; username: string; display_name: string | null } | null;
  channel_id: string | null; created_at: string; last_activity_at: string; first_response_at: string | null;
  closed_at: string | null; closed_by: string | null; close_reason: string | null; reopen_count: number;
  auto_close_warned_at: string | null;
}
interface Category { id: number; name: string; description: string | null; emoji: string | null; discord_category_id: string | null; support_role_id: string | null; active: boolean; sort_order: number }

const num = (n: number) => `#${String(n).padStart(4, "0")}`;

export default function Tickets() {
  const { can } = useAuth();
  const [tab, setTab] = useState<"list" | "categories">("list");
  return (
    <>
      <PageHeader title="Tickets" subtitle="Support-Tickets aus Discord – Zuweisung, Status, interne Notizen und Transkripte." />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "list", label: "Tickets" }, { key: "categories", label: "Kategorien", hidden: !can("tickets.configure") }]} />
      {tab === "list" ? <TicketList /> : <Categories />}
    </>
  );
}

function TicketList() {
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const status = params.get("status") ?? "active";
  const [q, setQ] = useState("");
  const [assignee, setAssignee] = useState("");
  const [priority, setPriority] = useState("");
  const { data: cats } = useApi<Category[]>("/api/tickets/categories", ["ticket_categories"]);
  const [cat, setCat] = useState("");
  const { data, error, loading } = useApi<Page<TicketRow>>(
    `/api/tickets${qs({ status, q, assignee, priority, category_id: cat, page, page_size: 50 })}`, ["tickets", "ticket_messages"]);
  const navigate = useNavigate();
  return (
    <>
      <div className="filters">
        <select value={status} onChange={(e) => { setParams({ status: e.target.value }); setPage(1); }}>
          <option value="active">Alle aktiven</option>
          <option value="">Alle</option>
          {Object.entries(TICKET_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={assignee} onChange={(e) => { setAssignee(e.target.value); setPage(1); }}>
          <option value="">Alle Bearbeiter</option>
          <option value="me">Mir zugewiesen</option>
          <option value="none">Nicht zugewiesen</option>
        </select>
        <select value={priority} onChange={(e) => setPriority(e.target.value)}>
          <option value="">Alle Prioritäten</option>
          {Object.entries(TICKET_PRIORITY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={cat} onChange={(e) => setCat(e.target.value)}>
          <option value="">Alle Kategorien</option>
          {(cats || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <input type="search" placeholder="Suche: Nummer, Betreff, Ersteller" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
      </div>
      <ErrorBox error={error} />
      <Panel className="flush">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Nr.</th><th>Betreff</th><th>Kategorie</th><th>Ersteller</th><th>Status</th><th>Priorität</th><th>Zuständig</th><th>Letzte Aktivität</th></tr>
            </thead>
            <tbody>
              {(data?.items || []).map((t) => (
                <tr key={t.id} className="clickable" onClick={() => navigate(`/tickets/${t.id}`)}>
                  <td className="mono">{num(t.number)}</td>
                  <td>{t.subject}</td>
                  <td className="muted">{t.category?.name || "–"}</td>
                  <td><div className="row"><Avatar url={t.creator.avatar_url} name={t.creator.username} size={20} />{t.creator.display_name || t.creator.username}</div></td>
                  <td><Badge status={t.status} label={TICKET_STATUS[t.status]} /></td>
                  <td><Badge status={t.priority === "urgent" ? "urgent" : t.priority === "high" ? "in_progress" : "muted"} label={TICKET_PRIORITY[t.priority]} /></td>
                  <td>{t.assignee ? t.assignee.display_name || t.assignee.username : <span className="muted">–</span>}</td>
                  <td className="muted">{fmtRelative(t.last_activity_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {loading && !data && <Loading />}
        {data && data.items.length === 0 && <Empty>Keine Tickets gefunden.</Empty>}
        {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      </Panel>
    </>
  );
}

interface TicketDetailData extends TicketRow {
  messages: { id: number; author_name: string; author_discord_id: string; is_staff: boolean; content: string; attachments: { url: string; filename: string }[]; source: string; created_at: string; edited_at: string | null; deleted: boolean }[];
  notes: { id: number; author_name: string; content: string; created_at: string }[];
  transcripts: { id: number; message_count: number; created_at: string }[];
}

export function TicketDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const { data: t, error, reload } = useApi<TicketDetailData>(`/api/tickets/${id}`, ["tickets", "ticket_messages", "ticket_notes"]);
  const act = useAction();
  const [note, setNote] = useState("");
  const [reply, setReply] = useState("");
  const [closing, setClosing] = useState(false);
  const [reason, setReason] = useState("");
  if (!t) return error ? <ErrorBox error={error} /> : <Loading />;
  const manage = can("tickets.manage");
  const assign = can("tickets.assign");
  const closed = t.status === "closed";
  const run = async (fn: () => Promise<unknown>) => { await act.run(fn); reload(); };
  return (
    <>
      <PageHeader title={`Ticket ${num(t.number)} – ${t.subject}`} subtitle={`${t.category?.name || "Ohne Kategorie"} · erstellt ${fmtDateTime(t.created_at)}`}
        actions={<>
          <Link to="/tickets" className="btn">Zurück</Link>
          {assign && !t.assignee && !closed && <Button variant="primary" disabled={act.busy} onClick={() => run(() => api.post(`/api/tickets/${t.id}/claim`))}>Übernehmen</Button>}
          {manage && !closed && <Button variant="danger" onClick={() => setClosing(true)}>Schließen</Button>}
          {manage && closed && <Button onClick={() => run(() => api.post(`/api/tickets/${t.id}/reopen`))}>Wiedereröffnen</Button>}
        </>} />
      <ErrorBox error={act.error} />
      <div className="grid grid-side">
        <div className="stack">
          <Panel title={`Gesprächsprotokoll (${t.messages.length})`} className="flush">
            <div className="messages">
              {t.messages.length === 0 && <Empty>Noch keine Nachrichten.</Empty>}
              {t.messages.map((m) => (
                <div key={m.id} className={`msg${m.is_staff ? " msg-staff" : ""}${m.deleted ? " msg-deleted" : ""}`}>
                  <div className="msg-head">
                    <strong>{m.author_name}</strong>
                    {m.is_staff && <Badge status="info" label={m.source === "panel" ? "Team · Panel" : "Team"} />}
                    <span className="muted">{fmtDateTime(m.created_at)}{m.edited_at ? " · bearbeitet" : ""}{m.deleted ? " · gelöscht" : ""}</span>
                  </div>
                  <div className="pre">{m.content}</div>
                  {m.attachments.map((a) => <div key={a.url}><a href={a.url} target="_blank" rel="noreferrer noopener">{a.filename || a.url}</a></div>)}
                </div>
              ))}
            </div>
            {manage && !closed && t.channel_id && (
              <div style={{ padding: 12, borderTop: "1px solid var(--border)" }}>
                <Field label="Antwort an den Ersteller (wird im Discord-Ticket gepostet)">
                  <textarea value={reply} onChange={(e) => setReply(e.target.value)} maxLength={1900} />
                </Field>
                <Button variant="primary" disabled={!reply.trim() || act.busy} onClick={async () => { await run(() => api.post(`/api/tickets/${t.id}/reply`, { content: reply })); setReply(""); }}>Antwort senden</Button>
              </div>
            )}
          </Panel>
        </div>
        <div className="stack">
          <Panel title="Details">
            <dl className="kv">
              <dt>Status</dt><dd>{manage ? (
                <select value={t.status} onChange={(e) => e.target.value === "closed" ? setClosing(true) : run(() => api.post(`/api/tickets/${t.id}/status`, { status: e.target.value }))}>
                  {Object.entries(TICKET_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>) : <Badge status={t.status} label={TICKET_STATUS[t.status]} />}</dd>
              <dt>Priorität</dt><dd>{manage ? (
                <select value={t.priority} onChange={(e) => run(() => api.post(`/api/tickets/${t.id}/priority`, { priority: e.target.value }))}>
                  {Object.entries(TICKET_PRIORITY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>) : TICKET_PRIORITY[t.priority]}</dd>
              <dt>Zuständig</dt><dd>{assign && !closed ? (
                <MemberSelect value={t.assignee?.id ?? null} onChange={(mid) => run(() => api.post(`/api/tickets/${t.id}/assign`, { member_id: mid }))} />
              ) : t.assignee ? t.assignee.display_name || t.assignee.username : "–"}</dd>
              <dt>Ersteller</dt><dd>{t.creator.display_name || t.creator.username}<div className="mono muted">{t.creator.discord_id}</div></dd>
              <dt>Erste Reaktion</dt><dd>{fmtDateTime(t.first_response_at)}</dd>
              <dt>Letzte Aktivität</dt><dd>{fmtDateTime(t.last_activity_at)}</dd>
              {t.auto_close_warned_at && <><dt>Auto-Schließung</dt><dd>gewarnt {fmtRelative(t.auto_close_warned_at)}</dd></>}
              {closed && <><dt>Geschlossen</dt><dd>{fmtDateTime(t.closed_at)} von {t.closed_by}{t.close_reason ? ` – ${t.close_reason}` : ""}</dd></>}
              <dt>Wiedereröffnet</dt><dd>{t.reopen_count}×</dd>
              <dt>Discord-Channel</dt><dd className="mono">{t.channel_id || "–"}</dd>
            </dl>
          </Panel>
          <Panel title="Interne Notizen">
            <p className="muted small">Nur für berechtigte Teammitglieder sichtbar – wird niemals an den Ersteller gesendet.</p>
            {t.notes.map((n) => (
              <div key={n.id} className="note"><div className="muted small">{n.author_name} · {fmtDateTime(n.created_at)}</div><div className="pre">{n.content}</div></div>
            ))}
            {manage && (
              <>
                <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Notiz hinzufügen" />
                <div style={{ marginTop: 8 }}><Button disabled={!note.trim()} onClick={async () => { await run(() => api.post(`/api/tickets/${t.id}/notes`, { content: note })); setNote(""); }}>Notiz speichern</Button></div>
              </>
            )}
          </Panel>
          <Panel title="Transkripte">
            {t.transcripts.length === 0 && <p className="muted">Wird beim Schließen erstellt.</p>}
            {t.transcripts.map((tr) => (
              <div key={tr.id}><a href={`/api/tickets/${t.id}/transcripts/${tr.id}`}>{fmtDateTime(tr.created_at)} · {tr.message_count} Nachrichten</a></div>
            ))}
          </Panel>
        </div>
      </div>
      {closing && (
        <Modal title={`Ticket ${num(t.number)} schließen`} onClose={() => setClosing(false)} footer={<>
          <Button onClick={() => setClosing(false)}>Abbrechen</Button>
          <Button variant="danger" onClick={async () => { await run(() => api.post(`/api/tickets/${t.id}/status`, { status: "closed", reason })); setClosing(false); }}>Schließen</Button>
        </>}>
          <Field label="Grund (optional, wird dem Ersteller mitgeteilt)"><input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} /></Field>
          <p className="muted small">Ein Transkript wird erstellt; der Discord-Channel wird gesperrt bzw. gelöscht (siehe Einstellungen).</p>
        </Modal>
      )}
    </>
  );
}

function Categories() {
  const { data, error, reload } = useApi<Category[]>("/api/tickets/categories", ["ticket_categories"]);
  const [edit, setEdit] = useState<Partial<Category> | null>(null);
  const act = useAction();
  const save = async () => {
    if (!edit) return;
    const r = await act.run(() => edit.id ? api.put(`/api/tickets/categories/${edit.id}`, edit) : api.post("/api/tickets/categories", edit));
    if (r) { setEdit(null); reload(); }
  };
  return (
    <Panel title="Ticketkategorien" actions={<Button variant="primary" small onClick={() => setEdit({ name: "", active: true, sort_order: 100 })}>Neue Kategorie</Button>} className="flush">
      <ErrorBox error={error} />
      <table className="table">
        <thead><tr><th>Name</th><th>Beschreibung</th><th>Discord-Kategorie</th><th>Support-Rolle</th><th>Status</th><th className="num">Reihenfolge</th><th /></tr></thead>
        <tbody>
          {(data || []).map((c) => (
            <tr key={c.id}>
              <td>{c.emoji} {c.name}</td>
              <td className="muted">{c.description}</td>
              <td className="mono">{c.discord_category_id || "Standard"}</td>
              <td className="mono">{c.support_role_id || "Standard"}</td>
              <td><Badge status={c.active ? "active" : "inactive"} label={c.active ? "Aktiv" : "Inaktiv"} /></td>
              <td className="num">{c.sort_order}</td>
              <td className="num">
                <Button small onClick={() => setEdit(c)}>Bearbeiten</Button>{" "}
                <Button small variant="danger" onClick={async () => { await act.run(() => api.del(`/api/tickets/categories/${c.id}`)); reload(); }}>Entfernen</Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small" style={{ padding: "8px 14px" }}>Änderungen aktualisieren das Ticket-Menü in Discord automatisch. Kategorien mit Tickets werden beim Entfernen deaktiviert.</p>
      {edit && (
        <Modal title={edit.id ? "Kategorie bearbeiten" : "Neue Kategorie"} onClose={() => setEdit(null)} footer={<>
          <Button onClick={() => setEdit(null)}>Abbrechen</Button><Button variant="primary" disabled={act.busy} onClick={save}>Speichern</Button></>}>
          <ErrorBox error={act.error} />
          <Field label="Name"><input value={edit.name || ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} maxLength={80} /></Field>
          <Field label="Beschreibung"><input value={edit.description || ""} onChange={(e) => setEdit({ ...edit, description: e.target.value })} maxLength={100} /></Field>
          <Field label="Emoji (optional)"><input value={edit.emoji || ""} onChange={(e) => setEdit({ ...edit, emoji: e.target.value })} maxLength={40} /></Field>
          <Field label="Discord-Kategorie für Channels" hint="Leer = Standard aus den Systemeinstellungen">
            <DiscordPicker kind="channel" channelTypes={["category"]} value={edit.discord_category_id || ""} onChange={(v) => setEdit({ ...edit, discord_category_id: v })} />
          </Field>
          <Field label="Support-Rolle" hint="Leer = Standard-Support-Rolle">
            <DiscordPicker kind="role" value={edit.support_role_id || ""} onChange={(v) => setEdit({ ...edit, support_role_id: v })} />
          </Field>
          <Field label="Reihenfolge"><input type="number" value={edit.sort_order ?? 100} onChange={(e) => setEdit({ ...edit, sort_order: Number(e.target.value) })} /></Field>
          <label className="check"><input type="checkbox" checked={!!edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} /> Aktiv</label>
        </Modal>
      )}
    </Panel>
  );
}
