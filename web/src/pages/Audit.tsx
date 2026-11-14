import { useState } from "react";
import { Badge, Empty, ErrorBox, Modal, PageHeader, Pagination, Panel } from "../components/ui";
import { qs, type Page } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import { useApi } from "../lib/hooks";

interface Entry { id: number; actor_discord_id: string | null; actor_name: string; action: string; object_type: string | null; object_id: string | null; details: Record<string, unknown> | null; source: string; ip_address: string | null; created_at: string }

export default function Audit() {
  const [f, setF] = useState({ action: "", actor: "", object_type: "", object_id: "", source: "", date_from: "", date_to: "" });
  const [page, setPage] = useState(1);
  const { data: actions } = useApi<string[]>("/api/audit/actions", ["audit"]);
  const { data, error } = useApi<Page<Entry>>(`/api/audit${qs({ ...f, page, page_size: 100 })}`, ["audit"]);
  const [detail, setDetail] = useState<Entry | null>(null);
  const set = (k: keyof typeof f, v: string) => { setF({ ...f, [k]: v }); setPage(1); };
  return (
    <>
      <PageHeader title="Audit-Logs" subtitle="Unveränderliches Protokoll aller administrativen Aktionen. Einträge können nicht bearbeitet oder gelöscht werden." />
      <div className="filters">
        <select value={f.action} onChange={(e) => set("action", e.target.value)}><option value="">Alle Aktionen</option>{(actions || []).map((a) => <option key={a} value={a}>{a}</option>)}</select>
        <input type="search" placeholder="Benutzer oder Discord-ID" value={f.actor} onChange={(e) => set("actor", e.target.value)} />
        <input placeholder="Objekttyp (z. B. bug)" value={f.object_type} onChange={(e) => set("object_type", e.target.value)} />
        <input placeholder="Objekt-ID" value={f.object_id} onChange={(e) => set("object_id", e.target.value)} />
        <select value={f.source} onChange={(e) => set("source", e.target.value)}><option value="">Alle Quellen</option><option value="panel">Webpanel</option><option value="discord">Discord</option><option value="system">System</option></select>
        <input type="date" value={f.date_from} onChange={(e) => set("date_from", e.target.value)} title="Von" />
        <input type="date" value={f.date_to} onChange={(e) => set("date_to", e.target.value)} title="Bis" />
      </div>
      <ErrorBox error={error} />
      <Panel className="flush">
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Zeitpunkt</th><th>Benutzer</th><th>Benutzer-ID</th><th>Aktion</th><th>Objekt</th><th>Objekt-ID</th><th>Quelle</th><th>Änderungen</th></tr></thead>
            <tbody>{(data?.items || []).map((e) => (
              <tr key={e.id} className="clickable" onClick={() => setDetail(e)}>
                <td className="muted">{fmtDateTime(e.created_at)}</td><td>{e.actor_name}</td><td className="mono">{e.actor_discord_id || "–"}</td>
                <td className="mono">{e.action}</td><td>{e.object_type || "–"}</td><td className="mono">{e.object_id || "–"}</td>
                <td><Badge status="muted" label={e.source} /></td>
                <td className="muted small" style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.details ? JSON.stringify(e.details) : ""}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        {data && data.items.length === 0 && <Empty>Keine Einträge.</Empty>}
        {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      </Panel>
      {detail && (
        <Modal title={`Audit-Eintrag ${detail.id}`} onClose={() => setDetail(null)}>
          <dl className="kv">
            <dt>Zeitpunkt</dt><dd>{fmtDateTime(detail.created_at)}</dd>
            <dt>Benutzer</dt><dd>{detail.actor_name}</dd><dt>Benutzer-ID</dt><dd className="mono">{detail.actor_discord_id || "–"}</dd>
            <dt>Aktion</dt><dd className="mono">{detail.action}</dd><dt>Objekt</dt><dd>{detail.object_type} {detail.object_id}</dd>
            <dt>Quelle</dt><dd>{detail.source}</dd><dt>IP-Adresse</dt><dd className="mono">{detail.ip_address || "–"}</dd>
          </dl>
          <pre className="pre mono" style={{ marginTop: 12 }}>{detail.details ? JSON.stringify(detail.details, null, 2) : "Keine Änderungsdetails."}</pre>
        </Modal>
      )}
    </>
  );
}
