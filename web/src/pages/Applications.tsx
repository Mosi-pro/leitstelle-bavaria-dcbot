import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Avatar, Badge, Button, DiscordPicker, Empty, ErrorBox, Field, Loading, MemberSelect, Modal, PageHeader, Pagination, Panel, Tabs } from "../components/ui";
import { api, qs, type Page } from "../lib/api";
import { useAuth } from "../lib/auth";
import { APP_STATUS, FIELD_TYPES, fmtDateTime, fmtRelative } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

interface AppRow {
  id: number; status: string; type: { id: number; key: string; name: string };
  applicant: { discord_id: string; username: string; display_name: string | null; avatar_url: string | null };
  reviewer: { id: number; username: string } | null; submitted_at: string; decided_at: string | null; decision_reason: string | null; anonymized: boolean;
}
interface Question { id?: number; label: string; field_type: string; required: boolean; options: string[]; max_length: number }
interface AppType {
  id: number; key: string; name: string; description: string | null; active: boolean; accept_role_id: string | null; auto_assign_role: boolean;
  create_beta_tester: boolean; accept_message: string | null; reject_message: string | null; retention_days: number; questions: Question[];
}

export default function Applications() {
  const { can } = useAuth();
  const [tab, setTab] = useState<"list" | "forms">("list");
  return (
    <>
      <PageHeader title="Bewerbungen" subtitle="Beta-Tester-, Team- und weitere Bewerbungen aus Discord." />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "list", label: "Eingang" }, { key: "forms", label: "Formulare", hidden: !can("applications.configure") }]} />
      {tab === "list" ? <AppList /> : <Forms />}
    </>
  );
}

function AppList() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "open";
  const [typeId, setTypeId] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const { data: types } = useApi<AppType[]>("/api/applications/types", ["application_types"]);
  const { data, error, loading } = useApi<Page<AppRow>>(`/api/applications${qs({ status, type_id: typeId, q, page })}`, ["applications"]);
  const navigate = useNavigate();
  return (
    <>
      <div className="filters">
        <select value={status} onChange={(e) => { setParams({ status: e.target.value }); setPage(1); }}>
          <option value="open">Offene (Neu, In Prüfung, Rückfragen)</option>
          <option value="">Alle</option>
          {Object.entries(APP_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={typeId} onChange={(e) => setTypeId(e.target.value)}>
          <option value="">Alle Arten</option>
          {(types || []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <input type="search" placeholder="Bewerber oder Discord-ID" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <ErrorBox error={error} />
      <Panel className="flush">
        <table className="table">
          <thead><tr><th>Nr.</th><th>Bewerber</th><th>Art</th><th>Status</th><th>Bearbeiter</th><th>Eingang</th></tr></thead>
          <tbody>
            {(data?.items || []).map((a) => (
              <tr key={a.id} className="clickable" onClick={() => navigate(`/applications/${a.id}`)}>
                <td className="mono">{a.id}</td>
                <td><div className="row"><Avatar url={a.applicant.avatar_url} name={a.applicant.username} size={20} />{a.applicant.display_name || a.applicant.username}</div></td>
                <td>{a.type.name}</td>
                <td><Badge status={a.status} label={APP_STATUS[a.status]} /></td>
                <td>{a.reviewer?.username || <span className="muted">–</span>}</td>
                <td className="muted">{fmtRelative(a.submitted_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {loading && !data && <Loading />}
        {data && data.items.length === 0 && <Empty>Keine Bewerbungen.</Empty>}
        {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      </Panel>
    </>
  );
}

interface AppDetailData extends AppRow {
  answers: { question: string; value: string }[];
  notes: { id: number; author_name: string; content: string; created_at: string }[];
  ratings: { member_id: number; username: string; score: number; comment: string | null; created_at: string }[];
  average_score: number | null;
  history: { actor_name: string; action: string; from_status: string | null; to_status: string | null; comment: string | null; created_at: string }[];
}

const HISTORY_LABEL: Record<string, string> = {
  submitted: "Bewerbung eingereicht", status_changed: "Status geändert", note_added: "Notiz hinzugefügt", rated: "Bewertet",
  reviewer_assigned: "Bearbeiter zugewiesen", inquiry_sent: "Rückfrage gesendet",
};

export function ApplicationDetail() {
  const { id } = useParams();
  const { can, me } = useAuth();
  const { data: a, error, reload } = useApi<AppDetailData>(`/api/applications/${id}`, ["applications"]);
  const act = useAction();
  const [note, setNote] = useState("");
  const [score, setScore] = useState(0);
  const [ratingComment, setRatingComment] = useState("");
  const [decision, setDecision] = useState<{ status: string; comment: string; message: string } | null>(null);
  if (!a) return error ? <ErrorBox error={error} /> : <Loading />;
  const review = can("applications.review") && !a.anonymized;
  const decided = a.status === "accepted" || a.status === "rejected";
  const run = async (fn: () => Promise<unknown>) => { const r = await act.run(fn); reload(); return r; };
  const mine = a.ratings.find((r) => r.member_id === me?.member_id);
  return (
    <>
      <PageHeader title={`${a.type.name} von ${a.applicant.display_name || a.applicant.username}`} subtitle={`Nr. ${a.id} · eingegangen ${fmtDateTime(a.submitted_at)}`}
        actions={<>
          <Link to="/applications" className="btn">Zurück</Link>
          {review && !decided && <>
            <Button onClick={() => setDecision({ status: "inquiry", comment: "", message: "" })}>Rückfrage</Button>
            <Button variant="danger" onClick={() => setDecision({ status: "rejected", comment: "", message: "" })}>Ablehnen</Button>
            <Button variant="primary" onClick={() => setDecision({ status: "accepted", comment: "", message: "" })}>Annehmen</Button>
          </>}
        </>} />
      <ErrorBox error={act.error} />
      {a.anonymized && <div className="alert alert-info">Diese Bewerbung wurde gemäß Aufbewahrungsfrist anonymisiert.</div>}
      <div className="grid grid-side">
        <div className="stack">
          <Panel title="Antworten">
            <dl className="kv" style={{ gridTemplateColumns: "minmax(160px, 38%) 1fr" }}>
              {a.answers.map((x, i) => <div key={i} style={{ display: "contents" }}><dt>{x.question}</dt><dd className="pre">{x.value || "–"}</dd></div>)}
            </dl>
            {a.answers.length === 0 && <p className="muted">Keine Antworten gespeichert.</p>}
          </Panel>
          <Panel title="Änderungsverlauf">
            <ul className="timeline">
              {a.history.map((h, i) => (
                <li key={i}><strong>{HISTORY_LABEL[h.action] || h.action}</strong>
                  {h.to_status && <> {h.from_status ? `${APP_STATUS[h.from_status]} → ` : ""}{APP_STATUS[h.to_status]}</>}
                  {h.comment && <span className="muted"> – {h.comment}</span>}
                  <div className="muted small">{h.actor_name} · {fmtDateTime(h.created_at)}</div></li>
              ))}
            </ul>
          </Panel>
        </div>
        <div className="stack">
          <Panel title="Bewerberprofil">
            <dl className="kv">
              <dt>Status</dt><dd><Badge status={a.status} label={APP_STATUS[a.status]} /></dd>
              <dt>Discord-Name</dt><dd>{a.applicant.username}</dd>
              <dt>Discord-ID</dt><dd className="mono">{a.applicant.discord_id}</dd>
              <dt>Eingang</dt><dd>{fmtDateTime(a.submitted_at)}</dd>
              <dt>Bearbeiter</dt><dd>{review ? <MemberSelect value={a.reviewer?.id ?? null} onChange={(mid) => run(() => api.post(`/api/applications/${a.id}/reviewer`, { member_id: mid }))} /> : a.reviewer?.username || "–"}</dd>
              {decided && <><dt>Entscheidung</dt><dd>{fmtDateTime(a.decided_at)}{a.decision_reason ? ` – ${a.decision_reason}` : ""}</dd></>}
            </dl>
          </Panel>
          <Panel title={`Bewertungen${a.average_score ? ` · Ø ${a.average_score.toLocaleString("de-DE")}` : ""}`}>
            {a.ratings.map((r) => (
              <div key={r.member_id} style={{ marginBottom: 6 }}><strong>{r.username}</strong>: {r.score}/5 {r.comment && <span className="muted">– {r.comment}</span>}</div>
            ))}
            {review && (
              <div style={{ marginTop: 8 }}>
                <div className="row">
                  <select value={score || mine?.score || 0} onChange={(e) => setScore(Number(e.target.value))} style={{ width: 120 }}>
                    <option value={0}>Bewertung</option>{[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n} / 5</option>)}
                  </select>
                  <input placeholder="Kommentar" value={ratingComment} onChange={(e) => setRatingComment(e.target.value)} />
                </div>
                <div style={{ marginTop: 8 }}><Button disabled={!(score || mine?.score)} onClick={async () => { await run(() => api.post(`/api/applications/${a.id}/rating`, { score: score || mine?.score, comment: ratingComment })); setRatingComment(""); }}>{mine ? "Bewertung aktualisieren" : "Bewerten"}</Button></div>
              </div>
            )}
          </Panel>
          <Panel title="Interne Notizen">
            {a.notes.map((n) => <div key={n.id} className="note"><div className="muted small">{n.author_name} · {fmtDateTime(n.created_at)}</div><div className="pre">{n.content}</div></div>)}
            {review && <>
              <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Notiz hinzufügen" />
              <div style={{ marginTop: 8 }}><Button disabled={!note.trim()} onClick={async () => { await run(() => api.post(`/api/applications/${a.id}/notes`, { content: note })); setNote(""); }}>Speichern</Button></div>
            </>}
          </Panel>
        </div>
      </div>
      {decision && (
        <Modal title={decision.status === "accepted" ? "Bewerbung annehmen" : decision.status === "rejected" ? "Bewerbung ablehnen" : "Rückfrage an den Bewerber"} onClose={() => setDecision(null)}
          footer={<><Button onClick={() => setDecision(null)}>Abbrechen</Button>
            <Button variant={decision.status === "rejected" ? "danger" : "primary"} disabled={decision.status === "inquiry" && !decision.message.trim()} onClick={async () => {
              const r = await run(() => api.post(`/api/applications/${a.id}/status`, { status: decision.status, comment: decision.comment || null, message: decision.message || null }));
              if (r) setDecision(null);
            }}>Bestätigen</Button></>}>
          {decision.status !== "inquiry" && <Field label="Interne Begründung (optional)"><textarea value={decision.comment} onChange={(e) => setDecision({ ...decision, comment: e.target.value })} /></Field>}
          <Field label={decision.status === "inquiry" ? "Nachricht an den Bewerber (Discord-DM)" : "Persönliche Nachricht (optional)"}
            hint={decision.status === "inquiry" ? undefined : "Leer = Standardtext der Bewerbungsart. Versand per Discord-DM, sofern aktiviert."}>
            <textarea value={decision.message} onChange={(e) => setDecision({ ...decision, message: e.target.value })} maxLength={1800} />
          </Field>
          {decision.status === "accepted" && <p className="muted small">Je nach Formular-Einstellung wird automatisch eine Discord-Rolle vergeben bzw. ein Beta-Tester angelegt.</p>}
        </Modal>
      )}
    </>
  );
}

const EMPTY_TYPE: AppType = { id: 0, key: "", name: "", description: "", active: true, accept_role_id: "", auto_assign_role: false, create_beta_tester: false,
  accept_message: "", reject_message: "", retention_days: 180, questions: [{ label: "", field_type: "text", required: true, options: [], max_length: 300 }] };

function Forms() {
  const { data, error, reload } = useApi<AppType[]>("/api/applications/types", ["application_types"]);
  const [edit, setEdit] = useState<AppType | null>(null);
  const act = useAction();
  const save = async () => {
    if (!edit) return;
    const body = { ...edit, questions: edit.questions.map((q) => ({ ...q, options: q.options.filter((o) => o.trim()) })) };
    const r = await act.run(() => edit.id ? api.put(`/api/applications/types/${edit.id}`, body) : api.post("/api/applications/types", body));
    if (r) { setEdit(null); reload(); }
  };
  const setQ = (i: number, patch: Partial<Question>) => edit && setEdit({ ...edit, questions: edit.questions.map((q, j) => (j === i ? { ...q, ...patch } : q)) });
  const move = (i: number, d: number) => {
    if (!edit) return;
    const qs2 = [...edit.questions];
    const [x] = qs2.splice(i, 1);
    qs2.splice(i + d, 0, x);
    setEdit({ ...edit, questions: qs2 });
  };
  return (
    <>
      <ErrorBox error={error} />
      <Panel title="Bewerbungsformulare" className="flush" actions={<Button small variant="primary" onClick={() => setEdit(structuredClone(EMPTY_TYPE))}>Neue Bewerbungsart</Button>}>
        <table className="table">
          <thead><tr><th>Name</th><th>Schlüssel</th><th className="num">Fragen</th><th>Bei Annahme</th><th className="num">Aufbewahrung</th><th>Status</th><th /></tr></thead>
          <tbody>
            {(data || []).map((t) => (
              <tr key={t.id}>
                <td>{t.name}</td><td className="mono">{t.key}</td><td className="num">{t.questions.length}</td>
                <td className="muted">{[t.create_beta_tester && "Beta-Tester anlegen", t.auto_assign_role && t.accept_role_id && "Rolle vergeben"].filter(Boolean).join(", ") || "–"}</td>
                <td className="num">{t.retention_days} Tage</td>
                <td><Badge status={t.active ? "active" : "inactive"} label={t.active ? "Aktiv" : "Inaktiv"} /></td>
                <td className="num"><Button small onClick={() => setEdit(structuredClone(t))}>Bearbeiten</Button>{" "}
                  <Button small variant="danger" onClick={async () => { await act.run(() => api.del(`/api/applications/types/${t.id}`)); reload(); }}>Entfernen</Button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      {edit && (
        <Modal wide title={edit.id ? `Formular „${edit.name}“` : "Neue Bewerbungsart"} onClose={() => setEdit(null)}
          footer={<><Button onClick={() => setEdit(null)}>Abbrechen</Button><Button variant="primary" disabled={act.busy} onClick={save}>Speichern</Button></>}>
          <ErrorBox error={act.error} />
          <div className="grid grid-2">
            <Field label="Name"><input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Schlüssel" hint="a–z, 0–9, _ und -"><input value={edit.key} onChange={(e) => setEdit({ ...edit, key: e.target.value })} /></Field>
          </div>
          <Field label="Beschreibung (wird im Discord-Panel angezeigt)"><input value={edit.description || ""} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
          <div className="grid grid-2">
            <Field label="Nachricht bei Annahme"><textarea value={edit.accept_message || ""} onChange={(e) => setEdit({ ...edit, accept_message: e.target.value })} /></Field>
            <Field label="Nachricht bei Ablehnung"><textarea value={edit.reject_message || ""} onChange={(e) => setEdit({ ...edit, reject_message: e.target.value })} /></Field>
          </div>
          <div className="grid grid-3">
            <Field label="Rolle bei Annahme"><DiscordPicker kind="role" value={edit.accept_role_id || ""} onChange={(v) => setEdit({ ...edit, accept_role_id: v })} /></Field>
            <Field label="Aufbewahrung nach Entscheidung (Tage)" hint="Danach automatische Anonymisierung"><input type="number" min={1} value={edit.retention_days} onChange={(e) => setEdit({ ...edit, retention_days: Number(e.target.value) })} /></Field>
            <div>
              <label className="check"><input type="checkbox" checked={edit.auto_assign_role} onChange={(e) => setEdit({ ...edit, auto_assign_role: e.target.checked })} /> Rolle automatisch vergeben</label>
              <label className="check"><input type="checkbox" checked={edit.create_beta_tester} onChange={(e) => setEdit({ ...edit, create_beta_tester: e.target.checked })} /> Als Beta-Tester anlegen</label>
              <label className="check"><input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} /> Aktiv</label>
            </div>
          </div>
          <h3 style={{ margin: "8px 0" }}>Fragen</h3>
          <p className="muted small">Discord zeigt bis zu 5 Fragen pro Formularseite; längere Formulare werden automatisch in Seiten aufgeteilt.</p>
          {edit.questions.map((q, i) => (
            <div key={i} className="panel" style={{ padding: 10, marginBottom: 8 }}>
              <div className="row">
                <span className="muted mono">{i + 1}</span>
                <input value={q.label} placeholder="Fragetext" onChange={(e) => setQ(i, { label: e.target.value })} maxLength={200} />
                <select value={q.field_type} onChange={(e) => setQ(i, { field_type: e.target.value })} style={{ width: 200 }}>
                  {Object.entries(FIELD_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                <Button small disabled={i === 0} onClick={() => move(i, -1)} aria-label="nach oben">↑</Button>
                <Button small disabled={i === edit.questions.length - 1} onClick={() => move(i, 1)} aria-label="nach unten">↓</Button>
                <Button small variant="danger" onClick={() => setEdit({ ...edit, questions: edit.questions.filter((_, j) => j !== i) })}>Entfernen</Button>
              </div>
              <div className="row" style={{ marginTop: 8 }}>
                <label className="check" style={{ margin: 0 }}><input type="checkbox" checked={q.required} onChange={(e) => setQ(i, { required: e.target.checked })} /> Pflichtfeld</label>
                {(q.field_type === "text" || q.field_type === "textarea") && <>
                  <span className="muted small">Max. Zeichen</span>
                  <input type="number" style={{ width: 100 }} min={1} max={4000} value={q.max_length} onChange={(e) => setQ(i, { max_length: Number(e.target.value) })} />
                </>}
                {(q.field_type === "select" || q.field_type === "multiselect") && (
                  <input placeholder="Optionen, durch Komma getrennt" value={q.options.join(", ")} onChange={(e) => setQ(i, { options: e.target.value.split(",").map((s) => s.trimStart()) })} />
                )}
              </div>
            </div>
          ))}
          <Button onClick={() => setEdit({ ...edit, questions: [...edit.questions, { label: "", field_type: "text", required: true, options: [], max_length: 300 }] })}>Frage hinzufügen</Button>
        </Modal>
      )}
    </>
  );
}
