import { useState } from "react";
import { Avatar, Badge, Button, DiscordPicker, Empty, ErrorBox, Field, Modal, Pagination, PageHeader, Panel, Stat, Tabs } from "../components/ui";
import { api, qs, type Page } from "../lib/api";
import { useAuth } from "../lib/auth";
import { MOD_ACTIONS, fmtDate, fmtDateTime, fmtNumber } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

type Tab = "overview" | "moderation" | "warnings" | "automod" | "announcements" | "polls";

export default function Community() {
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <>
      <PageHeader title="Community" subtitle="Mitglieder, Verifizierung, Moderation, Ankündigungen und Umfragen." />
      <Tabs value={tab} onChange={setTab} tabs={[
        { key: "overview", label: "Mitglieder" }, { key: "moderation", label: "Moderation" }, { key: "warnings", label: "Verwarnungen" },
        { key: "automod", label: "Auto-Moderation" }, { key: "announcements", label: "Ankündigungen" }, { key: "polls", label: "Umfragen" },
      ]} />
      {tab === "overview" && <Members />}
      {tab === "moderation" && <Moderation />}
      {tab === "warnings" && <Warnings />}
      {tab === "automod" && <AutoMod />}
      {tab === "announcements" && <Announcements />}
      {tab === "polls" && <Polls />}
    </>
  );
}

interface Overview { member_count: number | null; joins: number; leaves: number; growth: number; verified: number }
interface MemberRow { discord_id: string; username: string; display_name: string | null; avatar_url: string | null; verified: boolean; joined_guild_at: string | null }

function Members() {
  const { data: o } = useApi<Overview>("/api/community/overview", ["community"]);
  const [q, setQ] = useState("");
  const [verified, setVerified] = useState("");
  const [page, setPage] = useState(1);
  const { data, error } = useApi<Page<MemberRow>>(`/api/community/members${qs({ q, verified, page })}`, ["community"]);
  return (
    <div className="stack">
      <div className="stats">
        <Stat label="Mitglieder" value={fmtNumber(o?.member_count)} />
        <Stat label="Beitritte (30 T.)" value={fmtNumber(o?.joins)} />
        <Stat label="Austritte (30 T.)" value={fmtNumber(o?.leaves)} />
        <Stat label="Wachstum (30 T.)" value={o ? `${o.growth >= 0 ? "+" : ""}${o.growth}` : "–"} />
        <Stat label="Verifiziert" value={fmtNumber(o?.verified)} />
      </div>
      <div className="filters">
        <input type="search" placeholder="Name oder Discord-ID" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={verified} onChange={(e) => setVerified(e.target.value)}><option value="">Alle</option><option value="true">Verifiziert</option><option value="false">Nicht verifiziert</option></select>
      </div>
      <ErrorBox error={error} />
      <Panel className="flush">
        <table className="table">
          <thead><tr><th>Mitglied</th><th>Discord-ID</th><th>Verifiziert</th><th>Beigetreten</th></tr></thead>
          <tbody>{(data?.items || []).map((m) => (
            <tr key={m.discord_id}><td><div className="row"><Avatar url={m.avatar_url} name={m.username} size={20} />{m.display_name || m.username}</div></td>
              <td className="mono">{m.discord_id}</td><td>{m.verified ? <Badge status="ok" label="Ja" /> : <Badge status="muted" label="Nein" />}</td><td>{fmtDate(m.joined_guild_at)}</td></tr>
          ))}</tbody>
        </table>
        {data && data.items.length === 0 && <Empty>Noch keine Mitgliedsdaten – der Bot erfasst Beitritte ab seinem Start.</Empty>}
        {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      </Panel>
    </div>
  );
}

interface ModRow { id: number; action: string; label: string; target: { discord_id: string; username: string }; moderator_name: string; reason: string; duration_minutes: number | null; source: string; status: string; error: string | null; created_at: string }

function Moderation() {
  const { can } = useAuth();
  const [action, setAction] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const { data, error, reload } = useApi<Page<ModRow>>(`/api/community/moderation${qs({ action, q, page })}`, ["moderation"]);
  const [f, setF] = useState<{ discord_id: string; action: string; reason: string; duration_minutes: number; delete_message_days: number } | null>(null);
  const [history, setHistory] = useState<string | null>(null);
  const act = useAction();
  return (
    <>
      <div className="filters">
        <select value={action} onChange={(e) => setAction(e.target.value)}><option value="">Alle Aktionen</option>{Object.entries(MOD_ACTIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <input type="search" placeholder="Name oder Discord-ID" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="spacer" />
        {can("community.moderate") && <Button variant="primary" onClick={() => setF({ discord_id: "", action: "warn", reason: "", duration_minutes: 60, delete_message_days: 0 })}>Moderationsaktion</Button>}
      </div>
      <ErrorBox error={error} />
      <Panel className="flush">
        <table className="table">
          <thead><tr><th>Zeitpunkt</th><th>Aktion</th><th>Mitglied</th><th>Grund</th><th>Moderator</th><th>Quelle</th><th>Status</th></tr></thead>
          <tbody>{(data?.items || []).map((m) => (
            <tr key={m.id} className="clickable" onClick={() => setHistory(m.target.discord_id)}>
              <td className="muted">{fmtDateTime(m.created_at)}</td><td>{m.label}{m.duration_minutes ? ` (${m.duration_minutes} Min.)` : ""}</td>
              <td>{m.target.username}</td><td>{m.reason}</td><td>{m.moderator_name}</td><td className="muted">{m.source}</td>
              <td><Badge status={m.status} label={m.status === "executed" ? "ausgeführt" : m.status === "pending" ? "ausstehend" : m.status === "expired" ? "abgelaufen" : "fehlgeschlagen"} />{m.error && <div className="muted small">{m.error}</div>}</td></tr>
          ))}</tbody>
        </table>
        {data && data.items.length === 0 && <Empty>Keine Moderationsvorgänge.</Empty>}
        {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      </Panel>
      {f && (
        <Modal title="Moderationsaktion" onClose={() => setF(null)} footer={<>
          <Button onClick={() => setF(null)}>Abbrechen</Button>
          <Button variant={f.action === "warn" ? "primary" : "danger"} disabled={act.busy} onClick={async () => { const r = await act.run(() => api.post("/api/community/moderation", f)); if (r) { setF(null); reload(); } }}>Ausführen</Button></>}>
          <ErrorBox error={act.error} />
          <Field label="Discord-ID des Mitglieds"><input value={f.discord_id} onChange={(e) => setF({ ...f, discord_id: e.target.value.trim() })} /></Field>
          <Field label="Aktion"><select value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })}>{Object.entries(MOD_ACTIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          {f.action === "timeout" && <Field label="Dauer (Minuten, max. 40320)"><input type="number" min={1} max={40320} value={f.duration_minutes} onChange={(e) => setF({ ...f, duration_minutes: Number(e.target.value) })} /></Field>}
          {f.action === "ban" && <Field label="Nachrichten der letzten Tage löschen (0–7)"><input type="number" min={0} max={7} value={f.delete_message_days} onChange={(e) => setF({ ...f, delete_message_days: Number(e.target.value) })} /></Field>}
          <Field label="Grund"><textarea value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
          <p className="muted small">Die Aktion wird vom Bot in Discord ausgeführt und protokolliert. Verwarnungen werden dem Mitglied per DM mitgeteilt.</p>
        </Modal>
      )}
      {history && <UserHistory discordId={history} onClose={() => setHistory(null)} />}
    </>
  );
}

function UserHistory({ discordId, onClose }: { discordId: string; onClose: () => void }) {
  const { data, error } = useApi<{ user: { username: string; discord_id: string; is_member: boolean; verified: boolean }; actions: ModRow[]; warnings: WarnRow[] }>(`/api/community/moderation/user/${discordId}`, ["moderation"]);
  return (
    <Modal wide title={`Moderationshistorie ${data?.user.username || discordId}`} onClose={onClose}>
      <ErrorBox error={error} />
      {data && (
        <>
          <p className="muted">Discord-ID {data.user.discord_id} · {data.user.is_member ? "Mitglied" : "kein Mitglied"} · {data.user.verified ? "verifiziert" : "nicht verifiziert"} · {data.warnings.filter((w) => w.active).length} aktive Verwarnungen</p>
          <ul className="timeline">{data.actions.map((a) => <li key={a.id}><strong>{a.label}</strong> – {a.reason}<div className="muted small">{fmtDateTime(a.created_at)} · {a.moderator_name}</div></li>)}</ul>
        </>
      )}
    </Modal>
  );
}

interface WarnRow { id: number; user: { discord_id: string; username: string }; reason: string; moderator_name: string; active: boolean; revoked_by: string | null; revoked_at: string | null; created_at: string }

function Warnings() {
  const { can } = useAuth();
  const [active, setActive] = useState("true");
  const [page, setPage] = useState(1);
  const { data, error, reload } = useApi<Page<WarnRow>>(`/api/community/warnings${qs({ active, page })}`, ["moderation"]);
  const act = useAction();
  return (
    <>
      <div className="filters"><select value={active} onChange={(e) => setActive(e.target.value)}><option value="true">Aktive</option><option value="false">Aufgehobene</option><option value="">Alle</option></select></div>
      <ErrorBox error={error || act.error} />
      <Panel className="flush">
        <table className="table">
          <thead><tr><th>Datum</th><th>Mitglied</th><th>Grund</th><th>Moderator</th><th>Status</th><th /></tr></thead>
          <tbody>{(data?.items || []).map((w) => (
            <tr key={w.id}><td className="muted">{fmtDateTime(w.created_at)}</td><td>{w.user.username}<div className="mono muted">{w.user.discord_id}</div></td><td>{w.reason}</td><td>{w.moderator_name}</td>
              <td>{w.active ? <Badge status="warn" label="Aktiv" /> : <Badge status="muted" label={`Aufgehoben (${w.revoked_by})`} />}</td>
              <td className="num">{w.active && can("community.moderate") && <Button small onClick={async () => { await act.run(() => api.post(`/api/community/warnings/${w.id}/revoke`)); reload(); }}>Aufheben</Button>}</td></tr>
          ))}</tbody>
        </table>
        {data && data.items.length === 0 && <Empty>Keine Verwarnungen.</Empty>}
        {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      </Panel>
    </>
  );
}

interface Rule { id?: number; name: string; rule_type: string; config: { words?: string[]; max_mentions?: number; min_length?: number; max_ratio?: number }; action: string; timeout_minutes: number; enabled: boolean }
const RULE_TYPES: Record<string, string> = { word_filter: "Wortfilter", invite_links: "Discord-Einladungslinks", mention_spam: "Massen-Erwähnungen", caps: "Großbuchstaben-Spam" };
const RULE_ACTIONS: Record<string, string> = { delete: "Nachricht löschen", warn: "Löschen + Verwarnung", timeout: "Löschen + Timeout" };

function AutoMod() {
  const { can } = useAuth();
  const { data, error, reload } = useApi<Rule[]>("/api/community/automod", ["automod"]);
  const [edit, setEdit] = useState<Rule | null>(null);
  const act = useAction();
  return (
    <Panel title="Auto-Moderation" className="flush" actions={can("community.moderate") && <Button small variant="primary" onClick={() => setEdit({ name: "", rule_type: "word_filter", config: { words: [] }, action: "delete", timeout_minutes: 10, enabled: true })}>Neue Regel</Button>}>
      <ErrorBox error={error} />
      <table className="table">
        <thead><tr><th>Name</th><th>Typ</th><th>Aktion</th><th>Status</th><th /></tr></thead>
        <tbody>{(data || []).map((r) => (
          <tr key={r.id}><td>{r.name}</td><td>{RULE_TYPES[r.rule_type]}</td><td>{RULE_ACTIONS[r.action]}{r.action === "timeout" ? ` (${r.timeout_minutes} Min.)` : ""}</td>
            <td><Badge status={r.enabled ? "active" : "inactive"} label={r.enabled ? "Aktiv" : "Inaktiv"} /></td>
            <td className="num">{can("community.moderate") && <><Button small onClick={() => setEdit(r)}>Bearbeiten</Button>{" "}
              <Button small variant="danger" onClick={async () => { await act.run(() => api.del(`/api/community/automod/${r.id}`)); reload(); }}>Löschen</Button></>}</td></tr>
        ))}</tbody>
      </table>
      {data && data.length === 0 && <Empty>Keine Regeln. Mitglieder mit „Nachrichten verwalten“ sind von der Auto-Moderation ausgenommen.</Empty>}
      {edit && (
        <Modal title={edit.id ? "Regel bearbeiten" : "Neue Regel"} onClose={() => setEdit(null)} footer={<>
          <Button onClick={() => setEdit(null)}>Abbrechen</Button>
          <Button variant="primary" onClick={async () => { const r = await act.run(() => edit.id ? api.put(`/api/community/automod/${edit.id}`, edit) : api.post("/api/community/automod", edit)); if (r) { setEdit(null); reload(); } }}>Speichern</Button></>}>
          <ErrorBox error={act.error} />
          <Field label="Name"><input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
          <Field label="Typ"><select value={edit.rule_type} onChange={(e) => setEdit({ ...edit, rule_type: e.target.value, config: {} })}>{Object.entries(RULE_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          {edit.rule_type === "word_filter" && <Field label="Wörter (eine Zeile pro Wort)"><textarea value={(edit.config.words || []).join("\n")} onChange={(e) => setEdit({ ...edit, config: { words: e.target.value.split("\n") } })} /></Field>}
          {edit.rule_type === "mention_spam" && <Field label="Maximale Erwähnungen pro Nachricht"><input type="number" min={1} value={edit.config.max_mentions ?? 5} onChange={(e) => setEdit({ ...edit, config: { max_mentions: Number(e.target.value) } })} /></Field>}
          {edit.rule_type === "caps" && <div className="grid grid-2">
            <Field label="Ab Buchstabenanzahl"><input type="number" value={edit.config.min_length ?? 15} onChange={(e) => setEdit({ ...edit, config: { ...edit.config, min_length: Number(e.target.value) } })} /></Field>
            <Field label="Max. Anteil Großbuchstaben (0–1)"><input type="number" step="0.05" value={edit.config.max_ratio ?? 0.8} onChange={(e) => setEdit({ ...edit, config: { ...edit.config, max_ratio: Number(e.target.value) } })} /></Field></div>}
          <Field label="Aktion"><select value={edit.action} onChange={(e) => setEdit({ ...edit, action: e.target.value })}>{Object.entries(RULE_ACTIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          {edit.action === "timeout" && <Field label="Timeout (Minuten)"><input type="number" min={1} value={edit.timeout_minutes} onChange={(e) => setEdit({ ...edit, timeout_minutes: Number(e.target.value) })} /></Field>}
          <label className="check"><input type="checkbox" checked={edit.enabled} onChange={(e) => setEdit({ ...edit, enabled: e.target.checked })} /> Aktiv</label>
        </Modal>
      )}
    </Panel>
  );
}

interface Ann { id: number; channel_id: string; title: string | null; content: string; as_embed: boolean; color: string | null; footer: string | null; image_url: string | null; mention_role_id: string | null; status: string; error: string | null; created_by: string; created_at: string; sent_at: string | null }
interface Tpl { id: number; name: string; title: string | null; content: string; color: string | null; as_embed: boolean }
const ANN_STATUS: Record<string, string> = { draft: "Entwurf", queued: "Wird gesendet", sent: "Veröffentlicht", failed: "Fehlgeschlagen" };

function Announcements() {
  const { can } = useAuth();
  const { data, error, reload } = useApi<Ann[]>("/api/community/announcements", ["announcements"]);
  const { data: tpls, reload: reloadTpl } = useApi<Tpl[]>("/api/community/templates", ["announcements"]);
  const [edit, setEdit] = useState<Partial<Ann> | null>(null);
  const [saveAsTpl, setSaveAsTpl] = useState("");
  const act = useAction();
  const blank: Partial<Ann> = { channel_id: "", title: "", content: "", as_embed: true, color: "#3987e5" };
  const submit = async (publish: boolean) => {
    if (!edit) return;
    const body = { ...edit, publish };
    if (saveAsTpl) await act.run(() => api.post("/api/community/templates", { name: saveAsTpl, title: edit.title, content: edit.content, color: edit.color, as_embed: edit.as_embed }));
    const r = await act.run(async () => {
      if (edit.id) {
        await api.put(`/api/community/announcements/${edit.id}`, body);
        if (publish) await api.post(`/api/community/announcements/${edit.id}/publish`);
        return true;
      }
      return api.post("/api/community/announcements", body);
    });
    if (r) { setEdit(null); setSaveAsTpl(""); reload(); reloadTpl(); }
  };
  return (
    <div className="stack">
      <ErrorBox error={error || act.error} />
      <Panel title="Ankündigungen" className="flush" actions={can("community.announce") && <Button small variant="primary" onClick={() => setEdit(blank)}>Neue Ankündigung</Button>}>
        <table className="table">
          <thead><tr><th>Titel</th><th>Channel</th><th>Status</th><th>Erstellt</th><th /></tr></thead>
          <tbody>{(data || []).map((a) => (
            <tr key={a.id}><td>{a.title || a.content.slice(0, 60)}</td><td className="mono">{a.channel_id}</td>
              <td><Badge status={a.status} label={ANN_STATUS[a.status]} />{a.error && <div className="muted small">{a.error}</div>}</td>
              <td className="muted">{fmtDateTime(a.created_at)} · {a.created_by}</td>
              <td className="num">{can("community.announce") && <>
                {(a.status === "draft" || a.status === "failed") && <Button small onClick={() => setEdit(a)}>Bearbeiten</Button>}{" "}
                <Button small variant="danger" onClick={async () => { if (confirm(a.status === "sent" ? "Ankündigung löschen? Die Discord-Nachricht wird ebenfalls entfernt." : "Entwurf löschen?")) { await act.run(() => api.del(`/api/community/announcements/${a.id}`)); reload(); } }}>Löschen</Button></>}</td></tr>
          ))}</tbody>
        </table>
        {data && data.length === 0 && <Empty>Noch keine Ankündigungen.</Empty>}
      </Panel>
      <Panel title="Nachrichten-Vorlagen" className="flush">
        <table className="table"><tbody>{(tpls || []).map((t) => (
          <tr key={t.id}><td>{t.name}</td><td className="muted">{t.title}</td>
            <td className="num">{can("community.announce") && <><Button small onClick={() => setEdit({ ...blank, title: t.title, content: t.content, color: t.color, as_embed: t.as_embed })}>Verwenden</Button>{" "}
              <Button small variant="danger" onClick={async () => { await act.run(() => api.del(`/api/community/templates/${t.id}`)); reloadTpl(); }}>Löschen</Button></>}</td></tr>
        ))}</tbody></table>
        {tpls && tpls.length === 0 && <Empty>Keine Vorlagen. Beim Erstellen einer Ankündigung kann sie als Vorlage gespeichert werden.</Empty>}
      </Panel>
      {edit && (
        <Modal wide title={edit.id ? "Ankündigung bearbeiten" : "Neue Ankündigung"} onClose={() => setEdit(null)} footer={<>
          <Button onClick={() => setEdit(null)}>Abbrechen</Button>
          <Button disabled={act.busy} onClick={() => submit(false)}>Als Entwurf speichern</Button>
          <Button variant="primary" disabled={act.busy} onClick={() => submit(true)}>Veröffentlichen</Button></>}>
          <ErrorBox error={act.error} />
          <div className="grid grid-2">
            <div>
              <Field label="Zielchannel" hint="Leer = Ankündigungs-Channel aus den Einstellungen"><DiscordPicker kind="channel" channelTypes={["text", "news"]} value={edit.channel_id || ""} onChange={(v) => setEdit({ ...edit, channel_id: v })} /></Field>
              <Field label="Titel"><input value={edit.title || ""} maxLength={256} onChange={(e) => setEdit({ ...edit, title: e.target.value })} /></Field>
              <Field label="Text"><textarea rows={8} value={edit.content || ""} onChange={(e) => setEdit({ ...edit, content: e.target.value })} /></Field>
              <div className="grid grid-2">
                <Field label="Farbe"><input type="color" value={edit.color || "#3987e5"} onChange={(e) => setEdit({ ...edit, color: e.target.value })} /></Field>
                <Field label="Rolle erwähnen (optional)"><DiscordPicker kind="role" value={edit.mention_role_id || ""} onChange={(v) => setEdit({ ...edit, mention_role_id: v })} /></Field>
              </div>
              <Field label="Fußzeile"><input value={edit.footer || ""} onChange={(e) => setEdit({ ...edit, footer: e.target.value })} /></Field>
              <Field label="Bild-URL"><input value={edit.image_url || ""} onChange={(e) => setEdit({ ...edit, image_url: e.target.value })} /></Field>
              <label className="check"><input type="checkbox" checked={!!edit.as_embed} onChange={(e) => setEdit({ ...edit, as_embed: e.target.checked })} /> Als Discord-Embed senden</label>
              <Field label="Zusätzlich als Vorlage speichern unter (optional)"><input value={saveAsTpl} onChange={(e) => setSaveAsTpl(e.target.value)} /></Field>
            </div>
            <div>
              <div className="field-label" style={{ marginBottom: 6 }}>Vorschau</div>
              <div className="panel" style={{ padding: 12, borderLeft: edit.as_embed ? `4px solid ${edit.color || "#3987e5"}` : undefined }}>
                {edit.title && <div style={{ fontWeight: 600, marginBottom: 6 }}>{edit.title}</div>}
                <div className="pre">{edit.content || <span className="muted">Kein Text</span>}</div>
                {edit.footer && <div className="muted small" style={{ marginTop: 8 }}>{edit.footer}</div>}
              </div>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

interface PollRow { id: number; question: string; description: string | null; channel_id: string; multiple_choice: boolean; status: string; ends_at: string | null; created_by: string; created_at: string;
  results: { total_votes: number; voters: number; options: { id: number; label: string; votes: number; percent: number }[] } }
const POLL_STATUS: Record<string, string> = { draft: "Entwurf", queued: "Wird veröffentlicht", open: "Läuft", closed: "Beendet", failed: "Fehlgeschlagen" };

function Polls() {
  const { can } = useAuth();
  const { data, error, reload } = useApi<PollRow[]>("/api/community/polls", ["polls"]);
  const [f, setF] = useState<{ question: string; description: string; options: string[]; channel_id: string; multiple_choice: boolean; duration_hours: number } | null>(null);
  const act = useAction();
  return (
    <div className="stack">
      <div className="row"><span className="spacer" />{can("community.announce") && <Button variant="primary" onClick={() => setF({ question: "", description: "", options: ["", ""], channel_id: "", multiple_choice: false, duration_hours: 72 })}>Neue Umfrage</Button>}</div>
      <ErrorBox error={error || act.error} />
      {data && data.length === 0 && <Empty>Noch keine Umfragen.</Empty>}
      <div className="grid grid-2">
        {(data || []).map((p) => (
          <Panel key={p.id} title={p.question} actions={<Badge status={p.status === "open" ? "active" : p.status} label={POLL_STATUS[p.status]} />}>
            <p className="muted small">{p.results.voters} Teilnehmer · {p.multiple_choice ? "Mehrfachauswahl" : "Einfachauswahl"}{p.ends_at ? ` · Ende ${fmtDateTime(p.ends_at)}` : ""}</p>
            <table className="hbars"><tbody>{p.results.options.map((o) => (
              <tr key={o.id}><td className="hbars-label">{o.label}</td><td className="hbars-bar"><span style={{ width: `${o.percent}%` }} /></td><td className="num">{o.votes} · {o.percent.toLocaleString("de-DE")} %</td></tr>
            ))}</tbody></table>
            {can("community.announce") && (
              <div className="row" style={{ marginTop: 10 }}>
                {(p.status === "draft" || p.status === "failed") && <Button small variant="primary" onClick={async () => { await act.run(() => api.post(`/api/community/polls/${p.id}/publish`)); reload(); }}>Veröffentlichen</Button>}
                {p.status === "open" && <Button small onClick={async () => { await act.run(() => api.post(`/api/community/polls/${p.id}/close`)); reload(); }}>Beenden</Button>}
              </div>
            )}
          </Panel>
        ))}
      </div>
      {f && (
        <Modal title="Neue Umfrage" onClose={() => setF(null)} footer={<>
          <Button onClick={() => setF(null)}>Abbrechen</Button>
          <Button disabled={act.busy} onClick={async () => { const r = await act.run(() => api.post("/api/community/polls", { ...f, publish: false })); if (r) { setF(null); reload(); } }}>Als Entwurf</Button>
          <Button variant="primary" disabled={act.busy} onClick={async () => { const r = await act.run(() => api.post("/api/community/polls", { ...f, publish: true })); if (r) { setF(null); reload(); } }}>Veröffentlichen</Button></>}>
          <ErrorBox error={act.error} />
          <Field label="Frage"><input value={f.question} onChange={(e) => setF({ ...f, question: e.target.value })} maxLength={300} /></Field>
          <Field label="Beschreibung (optional)"><textarea value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
          <Field label="Antwortmöglichkeiten">
            {f.options.map((o, i) => (
              <div key={i} className="row" style={{ marginBottom: 6 }}>
                <input value={o} maxLength={80} onChange={(e) => setF({ ...f, options: f.options.map((x, j) => (j === i ? e.target.value : x)) })} placeholder={`Option ${i + 1}`} />
                <Button small disabled={f.options.length <= 2} onClick={() => setF({ ...f, options: f.options.filter((_, j) => j !== i) })}>Entfernen</Button>
              </div>
            ))}
            {f.options.length < 20 && <Button small onClick={() => setF({ ...f, options: [...f.options, ""] })}>Option hinzufügen</Button>}
          </Field>
          <Field label="Channel" hint="Leer = Ankündigungs-Channel"><DiscordPicker kind="channel" channelTypes={["text", "news"]} value={f.channel_id} onChange={(v) => setF({ ...f, channel_id: v })} /></Field>
          <Field label="Laufzeit (Stunden, leer = unbegrenzt)"><input type="number" min={1} value={f.duration_hours || ""} onChange={(e) => setF({ ...f, duration_hours: Number(e.target.value) })} /></Field>
          <label className="check"><input type="checkbox" checked={f.multiple_choice} onChange={(e) => setF({ ...f, multiple_choice: e.target.checked })} /> Mehrfachauswahl erlauben</label>
        </Modal>
      )}
    </div>
  );
}
