import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Button, DiscordPicker, Empty, ErrorBox, Field, Loading, Notice, PageHeader, Pagination, Panel, Tabs } from "../components/ui";
import { api, qs, type Page } from "../lib/api";
import { useAuth } from "../lib/auth";
import { OUTBOX_STATUS, fmtDateTime, fmtRelative } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

interface Item { key: string; label: string; type: string; secret: boolean; value: unknown; is_set: boolean; source: string; help: string; restart: boolean; updated_at: string | null; updated_by: string | null }
interface Group { key: string; label: string; items: Item[] }

type Tab = "config" | "discord" | "database" | "automations" | "sync" | "errors" | "backups" | "sessions";

export default function Settings() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "config";
  return (
    <>
      <PageHeader title="Systemeinstellungen" subtitle="Discord-Anbindung, Channels, Rollen, Automatisierungen, Datenbank und Wartung." />
      <Tabs value={tab} onChange={(t) => setParams({ tab: t })} tabs={[
        { key: "config", label: "Konfiguration" }, { key: "discord", label: "Discord-Panels & Bot" }, { key: "database", label: "Datenbank" },
        { key: "automations", label: "Automatisierungen" }, { key: "sync", label: "Synchronisierung" }, { key: "errors", label: "Fehlerprotokoll" },
        { key: "backups", label: "Backups" }, { key: "sessions", label: "Sitzungen" },
      ]} />
      {tab === "config" && <Config />}
      {tab === "discord" && <DiscordTools />}
      {tab === "database" && <Database />}
      {tab === "automations" && <Automations />}
      {tab === "sync" && <Sync />}
      {tab === "errors" && <Errors />}
      {tab === "backups" && <Backups />}
      {tab === "sessions" && <Sessions />}
    </>
  );
}

function Config() {
  const { data, error, reload } = useApi<{ groups: Group[] }>("/api/settings", ["settings"]);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<string | null>(null);
  const act = useAction();
  if (!data) return error ? <ErrorBox error={error} /> : <Loading />;
  const val = (it: Item) => (it.key in values ? values[it.key] : it.secret ? "" : it.value ?? "");
  const set = (k: string, v: unknown) => setValues({ ...values, [k]: v });
  const dirty = Object.keys(values).length > 0;
  const save = async () => {
    const r = await act.run(() => api.put<{ changed: string[]; bot_restart: boolean }>("/api/settings", { values }));
    if (r) {
      setResult(`${r.changed.length} Einstellung(en) gespeichert.${r.bot_restart ? " Der Bot wird automatisch neu gestartet." : ""}`);
      setValues({});
      reload();
    }
  };
  return (
    <div className="stack">
      <div className="row" style={{ position: "sticky", top: 50, zIndex: 5, background: "var(--bg)", padding: "6px 0" }}>
        {result && <span className="muted">{result}</span>}
        <span className="spacer" />
        <Button disabled={!dirty} onClick={() => setValues({})}>Verwerfen</Button>
        <Button variant="primary" disabled={!dirty || act.busy} onClick={save}>Speichern</Button>
      </div>
      <ErrorBox error={act.error} />
      {data.groups.map((g) => (
        <Panel key={g.key} title={g.label}>
          <div className="grid grid-2">
            {g.items.map((it) => (
              <Field key={it.key} label={it.label} hint={<>
                {it.help && <>{it.help} </>}
                <span className="mono">{it.key}</span>
                {it.source === "env" && " · Wert aus .env"}
                {it.updated_by && ` · geändert von ${it.updated_by}, ${fmtRelative(it.updated_at)}`}
              </>}>
                {it.type === "bool" ? (
                  <label className="check" style={{ margin: 0 }}><input type="checkbox" checked={!!val(it)} onChange={(e) => set(it.key, e.target.checked)} /> aktiviert</label>
                ) : it.type === "secret" ? (
                  <div className="row">
                    <input type="password" autoComplete="new-password" value={String(val(it))} placeholder={it.is_set ? "•••••••• (gesetzt – leer lassen zum Beibehalten)" : "nicht gesetzt"} onChange={(e) => set(it.key, e.target.value)} />
                    <Badge status={it.is_set ? "ok" : "failed"} label={it.is_set ? "gesetzt" : "fehlt"} />
                  </div>
                ) : it.type === "text" ? (
                  <textarea value={String(val(it))} onChange={(e) => set(it.key, e.target.value)} />
                ) : it.type === "int" ? (
                  <input type="number" min={0} value={String(val(it))} onChange={(e) => set(it.key, e.target.value)} />
                ) : it.type === "snowflake" && (g.key === "channels" || g.key === "roles") ? (
                  <DiscordPicker kind={g.key === "roles" ? "role" : "channel"}
                    channelTypes={it.key === "channels.bug_forum" ? ["forum"] : it.key === "channels.ticket_category" ? ["category"] : ["text", "news"]}
                    value={String(val(it))} onChange={(v) => set(it.key, v)} />
                ) : (
                  <input value={String(val(it))} onChange={(e) => set(it.key, e.target.value)} />
                )}
              </Field>
            ))}
          </div>
        </Panel>
      ))}
    </div>
  );
}

function DiscordTools() {
  const act = useAction();
  const [msg, setMsg] = useState<string | null>(null);
  const { data: status } = useApi<{ bot: { online: boolean; last_seen: string | null; details: Record<string, unknown> | null } }>("/api/system/status", ["system"]);
  const { data: guild } = useApi<{ name: string | null; channels: unknown[]; roles: unknown[] }>("/api/guild", ["system"]);
  const publish = async (panel: string, label: string) => {
    const r = await act.run(() => api.post(`/api/discord/panels/${panel}`));
    if (r) setMsg(`${label} wird vom Bot veröffentlicht.`);
  };
  return (
    <div className="grid grid-2">
      <Panel title="Discord-Bot">
        <dl className="kv">
          <dt>Status</dt><dd>{status?.bot.online ? <Badge status="ok" label="Online" /> : <Badge status="failed" label="Offline" />}</dd>
          <dt>Letztes Signal</dt><dd>{fmtDateTime(status?.bot.last_seen)}</dd>
          <dt>Server</dt><dd>{guild?.name || "–"}</dd>
          <dt>Channels / Rollen</dt><dd>{guild ? `${guild.channels.length} / ${guild.roles.length}` : "–"}</dd>
          <dt>Latenz</dt><dd>{String(status?.bot.details?.latency_ms ?? "–")} ms</dd>
        </dl>
        <div style={{ marginTop: 12 }}>
          <Button onClick={async () => { const r = await act.run(() => api.post("/api/discord/bot/restart")); if (r) setMsg("Neustart angefordert – der Bot startet in wenigen Sekunden neu."); }}>Bot neu starten</Button>
        </div>
      </Panel>
      <Panel title="Panel-Nachrichten in Discord">
        <p className="muted">Veröffentlicht bzw. erneuert die interaktiven Nachrichten in den konfigurierten Channels.</p>
        <ErrorBox error={act.error} />
        {msg && <Notice kind="ok">{msg}</Notice>}
        <div className="stack">
          <div className="row"><Button onClick={() => publish("ticket", "Das Ticket-Menü")}>Ticket-Menü veröffentlichen</Button><span className="muted small">Channel: Ticket-Channel</span></div>
          <div className="row"><Button onClick={() => publish("applications", "Das Bewerbungs-Panel")}>Bewerbungs-Panel veröffentlichen</Button><span className="muted small">Channel: Bewerbungs-Channel</span></div>
          <div className="row"><Button onClick={() => publish("verification", "Das Verifizierungs-Panel")}>Verifizierung veröffentlichen</Button><span className="muted small">Channel: Verifizierungs-Channel</span></div>
        </div>
      </Panel>
    </div>
  );
}

interface DbInfo { host: string; port: number; name: string; user: string; ssl_mode: string; env_file_writable: boolean; ok: boolean; server_version: string | null; checks: { key: string; title: string; ok: boolean; message: string; hint: string | null; required: boolean }[] }

function CheckList({ checks }: { checks: DbInfo["checks"] }) {
  return (
    <table className="table"><tbody>{checks.map((c) => (
      <tr key={c.key}><td style={{ width: 30 }}><Badge status={c.ok ? "ok" : c.required ? "failed" : "warn"} label={c.ok ? "OK" : c.required ? "Fehler" : "Hinweis"} /></td>
        <td><strong>{c.title}</strong><div>{c.message}</div>{!c.ok && c.hint && <div className="muted small">{c.hint}</div>}</td></tr>
    ))}</tbody></table>
  );
}

function Database() {
  const { me } = useAuth();
  const { data, error, reload } = useApi<DbInfo>("/api/system/database");
  const [f, setF] = useState({ host: "", port: "", name: "", user: "", password: "", ssl_mode: "" });
  const [test, setTest] = useState<DbInfo | null>(null);
  const act = useAction();
  useEffect(() => { if (data) setF({ host: data.host, port: String(data.port), name: data.name, user: data.user, password: "", ssl_mode: data.ssl_mode }); }, [data]);
  if (!data) return error ? <ErrorBox error={error} /> : <Loading />;
  return (
    <div className="grid grid-2">
      <Panel title="STRATO-MySQL – aktueller Zustand" actions={<Button small onClick={reload}>Erneut prüfen</Button>}>
        <dl className="kv" style={{ marginBottom: 10 }}>
          <dt>Server</dt><dd className="mono">{data.host}:{data.port}</dd><dt>Datenbank</dt><dd className="mono">{data.name}</dd>
          <dt>Benutzer</dt><dd className="mono">{data.user}</dd><dt>TLS-Modus</dt><dd>{data.ssl_mode}</dd><dt>Version</dt><dd>{data.server_version || "–"}</dd>
        </dl>
        <CheckList checks={data.checks} />
      </Panel>
      <Panel title="Verbindung ändern">
        {!me?.is_owner && <Notice kind="warn">Nur der Inhaber kann die Datenbankverbindung ändern.</Notice>}
        {!data.env_file_writable && <Notice kind="warn">Die .env-Datei ist im Container nicht beschreibbar. Änderung über <span className="mono">sudo ./install.sh --reconfigure</span>.</Notice>}
        <ErrorBox error={act.error} />
        <div className="grid grid-2">
          <Field label="DB_HOST"><input value={f.host} onChange={(e) => setF({ ...f, host: e.target.value })} /></Field>
          <Field label="DB_PORT"><input value={f.port} onChange={(e) => setF({ ...f, port: e.target.value })} /></Field>
          <Field label="DB_NAME"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="DB_USER"><input value={f.user} onChange={(e) => setF({ ...f, user: e.target.value })} /></Field>
          <Field label="DB_PASSWORD" hint="leer = unverändert"><input type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
          <Field label="DB_SSL_MODE"><select value={f.ssl_mode} onChange={(e) => setF({ ...f, ssl_mode: e.target.value })}>
            {["disabled", "preferred", "required", "verify_ca", "verify_identity"].map((m) => <option key={m} value={m}>{m}</option>)}</select></Field>
        </div>
        <div className="row">
          <Button disabled={act.busy} onClick={async () => { const r = await act.run(() => api.post<DbInfo>("/api/system/database/test", f)); if (r) setTest(r); }}>Verbindung testen</Button>
          <Button variant="primary" disabled={act.busy || !me?.is_owner || !data.env_file_writable} onClick={async () => {
            if (!confirm("Neue Verbindung speichern? Backend und Bot werden neu gestartet. Die alte Datenbank wird NICHT verändert oder gelöscht.")) return;
            const r = await act.run(() => api.put<DbInfo & { saved: boolean }>("/api/system/database", f));
            if (r) setTest(r);
          }}>Speichern und neu starten</Button>
        </div>
        {test && <div style={{ marginTop: 12 }}>
          {"saved" in test && (test as { saved: boolean }).saved && <Notice kind="ok">Gespeichert. Die Anwendung startet neu – bitte die Seite in einigen Sekunden neu laden.</Notice>}
          <CheckList checks={test.checks} /></div>}
      </Panel>
    </div>
  );
}

interface Auto { key: string; name: string; description: string | null; enabled: boolean; interval_minutes: number; last_run_at: string | null; next_run_at: string | null; last_status: string | null; last_message: string | null }

function Automations() {
  const { data, error, reload } = useApi<Auto[]>("/api/system/automations", ["automations"]);
  const act = useAction();
  const [out, setOut] = useState<string | null>(null);
  return (
    <Panel title="Automatisierungen" className="flush">
      <ErrorBox error={error || act.error} />
      {out && <div style={{ padding: "8px 14px" }}><Notice>{out}</Notice></div>}
      <table className="table">
        <thead><tr><th>Automatisierung</th><th>Intervall (Min.)</th><th>Letzte Ausführung</th><th>Ergebnis</th><th>Aktiv</th><th /></tr></thead>
        <tbody>{(data || []).map((a) => (
          <tr key={a.key}>
            <td><strong>{a.name}</strong><div className="muted small">{a.description}</div></td>
            <td><input type="number" min={1} style={{ width: 90 }} defaultValue={a.interval_minutes} onBlur={async (e) => { if (Number(e.target.value) !== a.interval_minutes) { await act.run(() => api.put(`/api/system/automations/${a.key}`, { interval_minutes: Number(e.target.value) })); reload(); } }} /></td>
            <td className="muted">{fmtDateTime(a.last_run_at)}</td>
            <td>{a.last_status && <Badge status={a.last_status} label={a.last_status === "ok" ? "OK" : "Fehler"} />}<div className="muted small" style={{ maxWidth: 360 }}>{a.last_message}</div></td>
            <td><input type="checkbox" checked={a.enabled} onChange={async (e) => { await act.run(() => api.put(`/api/system/automations/${a.key}`, { enabled: e.target.checked })); reload(); }} /></td>
            <td className="num"><Button small onClick={async () => { const r = await act.run(() => api.post<{ result: unknown }>(`/api/system/automations/${a.key}/run`)); if (r) setOut(`${a.name}: ${JSON.stringify(r.result)}`); reload(); }}>Jetzt ausführen</Button></td>
          </tr>
        ))}</tbody>
      </table>
    </Panel>
  );
}

interface Job { id: number; kind: string; status: string; attempts: number; max_attempts: number; payload: Record<string, unknown>; last_error: string | null; created_at: string; next_attempt_at: string; processed_at: string | null }

function Sync() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const { data, error, reload } = useApi<Page<Job> & { counts: Record<string, number> }>(`/api/system/outbox${qs({ status, page })}`, ["outbox"]);
  const act = useAction();
  return (
    <>
      <Notice>Änderungen aus dem Webpanel werden über diese Warteschlange an den Discord-Bot übergeben. Fehlgeschlagene Aufträge werden automatisch mit wachsendem Abstand wiederholt.</Notice>
      <div className="filters">
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Alle ({Object.values(data?.counts || {}).reduce((a, b) => a + b, 0)})</option>
          {["pending", "processing", "done", "failed"].map((s) => <option key={s} value={s}>{OUTBOX_STATUS[s]} ({data?.counts[s] || 0})</option>)}
        </select>
      </div>
      <ErrorBox error={error || act.error} />
      <Panel className="flush">
        <table className="table">
          <thead><tr><th>ID</th><th>Auftrag</th><th>Status</th><th className="num">Versuche</th><th>Erstellt</th><th>Nächster Versuch</th><th>Fehler</th><th /></tr></thead>
          <tbody>{(data?.items || []).map((j) => (
            <tr key={j.id}><td className="mono">{j.id}</td><td className="mono">{j.kind}<div className="muted small">{JSON.stringify(j.payload).slice(0, 80)}</div></td>
              <td><Badge status={j.status} label={OUTBOX_STATUS[j.status] || j.status} /></td><td className="num">{j.attempts}/{j.max_attempts}</td>
              <td className="muted">{fmtDateTime(j.created_at)}</td><td className="muted">{j.status === "pending" ? fmtDateTime(j.next_attempt_at) : "–"}</td>
              <td className="small" style={{ maxWidth: 300 }}>{j.last_error}</td>
              <td className="num">{(j.status === "failed" || j.status === "pending") && <Button small onClick={async () => { await act.run(() => api.post(`/api/system/outbox/${j.id}/retry`)); reload(); }}>Wiederholen</Button>}{" "}
                {j.status === "pending" && <Button small variant="danger" onClick={async () => { await act.run(() => api.post(`/api/system/outbox/${j.id}/cancel`)); reload(); }}>Abbrechen</Button>}</td></tr>
          ))}</tbody>
        </table>
        {data && data.items.length === 0 && <Empty>Keine Aufträge.</Empty>}
        {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      </Panel>
    </>
  );
}

function Errors() {
  const [page, setPage] = useState(1);
  const { data, error } = useApi<Page<{ id: number; component: string; message: string; context: string | null; created_at: string }>>(`/api/system/errors?page=${page}`, ["system"]);
  return (
    <Panel className="flush">
      <ErrorBox error={error} />
      <table className="table">
        <thead><tr><th>Zeitpunkt</th><th>Komponente</th><th>Kontext</th><th>Meldung</th></tr></thead>
        <tbody>{(data?.items || []).map((e) => <tr key={e.id}><td className="muted">{fmtDateTime(e.created_at)}</td><td>{e.component}</td><td className="muted">{e.context}</td><td className="pre small">{e.message}</td></tr>)}</tbody>
      </table>
      {data && data.items.length === 0 && <Empty>Keine Fehler protokolliert.</Empty>}
      {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
    </Panel>
  );
}

function Backups() {
  const { data, error } = useApi<{ name: string; size: number; created_at: string }[]>("/api/system/backups");
  return (
    <div className="grid grid-2">
      <Panel title="Lokale Backups auf dem Server" className="flush">
        <ErrorBox error={error} />
        <table className="table"><tbody>{(data || []).map((b) => (
          <tr key={b.name}><td className="mono">{b.name}</td><td className="num">{(b.size / 1024 / 1024).toLocaleString("de-DE", { maximumFractionDigits: 2 })} MB</td><td className="muted">{fmtDateTime(b.created_at)}</td></tr>
        ))}</tbody></table>
        {data && data.length === 0 && <Empty>Noch keine Backups vorhanden.</Empty>}
      </Panel>
      <Panel title="Hinweise">
        <p>Backups enthalten einen vollständigen Dump der STRATO-Datenbank und die Konfiguration (.env). Sie werden täglich automatisch erstellt; Uhrzeit und Anzahl sind unter <em>Konfiguration → Backup</em> einstellbar.</p>
        <p>Manuelles Backup auf dem Server:</p>
        <pre className="mono pre">sudo ./backup.sh</pre>
        <p>Wiederherstellung:</p>
        <pre className="mono pre">sudo ./restore.sh backups/&lt;datei&gt;.tar.gz</pre>
        <p className="muted small">Aus Sicherheitsgründen erfolgt die Wiederherstellung nur direkt auf dem Server, nicht über das Webpanel.</p>
      </Panel>
    </div>
  );
}

function Sessions() {
  const { data, error, reload } = useApi<{ id: number; created_at: string; last_seen_at: string; ip_address: string | null; user_agent: string | null; current: boolean }[]>("/api/auth/sessions");
  const act = useAction();
  return (
    <Panel title="Meine aktiven Sitzungen" className="flush" actions={<Button small onClick={async () => { await act.run(() => api.post("/api/auth/sessions/revoke-others")); reload(); }}>Alle anderen abmelden</Button>}>
      <ErrorBox error={error || act.error} />
      <table className="table"><thead><tr><th>Angemeldet</th><th>Zuletzt aktiv</th><th>IP-Adresse</th><th>Browser</th><th /></tr></thead>
        <tbody>{(data || []).map((s) => (
          <tr key={s.id}><td>{fmtDateTime(s.created_at)}</td><td>{fmtRelative(s.last_seen_at)}</td><td className="mono">{s.ip_address}</td><td className="muted small">{s.user_agent}</td><td>{s.current && <Badge status="ok" label="diese Sitzung" />}</td></tr>
        ))}</tbody></table>
    </Panel>
  );
}
