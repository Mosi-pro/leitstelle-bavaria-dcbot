import { useState } from "react";
import { Avatar, Badge, Button, DiscordPicker, ErrorBox, Field, Loading, Modal, PageHeader, Panel, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { MEMBER_STATUS, fmtDate, fmtRelative } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

interface Perm { key: string; label: string }
interface Role { id: number; name: string; description: string | null; position: number; is_owner: boolean; discord_role_id: string | null; permissions: string[]; member_count: number }
interface Member {
  id: number; discord_id: string; username: string; display_name: string | null; avatar_url: string | null;
  role: { id: number; name: string; is_owner: boolean } | null; status: string; joined_team_at: string | null; notes: string | null;
  last_login_at: string | null; last_activity_at: string | null; overrides: { permission: string; allow: boolean }[]; permissions: string[];
  stats: { actions: number; tickets_handled: number; tickets_closed: number; bugs_claimed: number; bugs_fixed: number } | null;
}

export default function Team() {
  const [tab, setTab] = useState<"members" | "roles">("members");
  return (
    <>
      <PageHeader title="Teamverwaltung" subtitle="Teammitglieder, Rollen und individuelle Berechtigungen. Nur hier eingetragene Personen können sich anmelden." />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "members", label: "Teammitglieder" }, { key: "roles", label: "Rollen und Rechte" }]} />
      {tab === "members" ? <Members /> : <Roles />}
    </>
  );
}

function Members() {
  const { can, me } = useAuth();
  const { data, error, reload } = useApi<Member[]>("/api/team/members", ["team"]);
  const { data: roles } = useApi<Role[]>("/api/team/roles", ["team"]);
  const { data: perms } = useApi<Perm[]>("/api/team/permissions");
  const [adding, setAdding] = useState<{ discord_id: string; username: string; role_id: string; notes: string } | null>(null);
  const [edit, setEdit] = useState<Member | null>(null);
  const act = useAction();
  const manage = can("team.manage");
  if (!data) return error ? <ErrorBox error={error} /> : <Loading />;
  return (
    <>
      <ErrorBox error={act.error} />
      <Panel className="flush" title={`${data.length} Teammitglieder`} actions={manage && <Button small variant="primary" onClick={() => setAdding({ discord_id: "", username: "", role_id: "", notes: "" })}>Teammitglied hinzufügen</Button>}>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Mitglied</th><th>Discord-ID</th><th>Rolle</th><th>Status</th><th>Im Team seit</th><th>Letzte Aktivität</th><th className="num">Aktionen (30 T.)</th><th className="num">Tickets</th><th className="num">Bugs</th><th /></tr></thead>
            <tbody>{data.map((m) => (
              <tr key={m.id}>
                <td><div className="row"><Avatar url={m.avatar_url} name={m.username} size={22} />{m.display_name || m.username}</div></td>
                <td className="mono">{m.discord_id}</td>
                <td>{m.role?.name || <span className="muted">keine</span>}{m.overrides.length > 0 && <span className="muted small"> +{m.overrides.length} individuell</span>}</td>
                <td><Badge status={m.status} label={MEMBER_STATUS[m.status]} /></td>
                <td>{fmtDate(m.joined_team_at)}</td>
                <td className="muted">{fmtRelative(m.last_activity_at)}</td>
                <td className="num">{m.stats?.actions ?? 0}</td>
                <td className="num">{m.stats ? `${m.stats.tickets_closed}/${m.stats.tickets_handled}` : "–"}</td>
                <td className="num">{m.stats ? `${m.stats.bugs_fixed}/${m.stats.bugs_claimed}` : "–"}</td>
                <td className="num">{manage && <Button small onClick={() => setEdit(m)}>Bearbeiten</Button>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <p className="muted small" style={{ padding: "8px 14px" }}>Tickets: geschlossen/bearbeitet · Bugs: behoben/übernommen (jeweils 30 Tage)</p>
      </Panel>
      {adding && (
        <Modal title="Teammitglied hinzufügen" onClose={() => setAdding(null)} footer={<>
          <Button onClick={() => setAdding(null)}>Abbrechen</Button>
          <Button variant="primary" disabled={!adding.discord_id} onClick={async () => { const r = await act.run(() => api.post("/api/team/members", { ...adding, role_id: adding.role_id ? Number(adding.role_id) : null })); if (r) { setAdding(null); reload(); } }}>Hinzufügen</Button></>}>
          <ErrorBox error={act.error} />
          <Field label="Discord-ID" hint="Die Person meldet sich anschließend selbst per Discord an."><input value={adding.discord_id} onChange={(e) => setAdding({ ...adding, discord_id: e.target.value.trim() })} inputMode="numeric" /></Field>
          <Field label="Name (optional)"><input value={adding.username} onChange={(e) => setAdding({ ...adding, username: e.target.value })} /></Field>
          <Field label="Rolle"><select value={adding.role_id} onChange={(e) => setAdding({ ...adding, role_id: e.target.value })}><option value="">– keine –</option>{(roles || []).filter((r) => !r.is_owner || me?.is_owner).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Field>
          <Field label="Notizen"><textarea value={adding.notes} onChange={(e) => setAdding({ ...adding, notes: e.target.value })} /></Field>
        </Modal>
      )}
      {edit && <MemberEdit member={edit} roles={roles || []} perms={perms || []} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); }} />}
    </>
  );
}

function MemberEdit({ member, roles, perms, onClose, onSaved }: { member: Member; roles: Role[]; perms: Perm[]; onClose: () => void; onSaved: () => void }) {
  const { me } = useAuth();
  const [roleId, setRoleId] = useState<string>(member.role ? String(member.role.id) : "");
  const [status, setStatus] = useState(member.status);
  const [notes, setNotes] = useState(member.notes || "");
  const [ov, setOv] = useState<Record<string, boolean | undefined>>(Object.fromEntries(member.overrides.map((o) => [o.permission, o.allow])));
  const act = useAction();
  const role = roles.find((r) => String(r.id) === roleId);
  const rolePerms = new Set(role ? (role.is_owner ? perms.map((p) => p.key) : role.permissions) : []);
  const effective = (k: string) => (ov[k] === undefined ? rolePerms.has(k) : ov[k]!);
  const self = me?.member_id === member.id;
  return (
    <Modal wide title={`${member.display_name || member.username} bearbeiten`} onClose={onClose} footer={<>
      {!self && !member.role?.is_owner && <Button variant="danger" onClick={async () => { if (confirm("Teammitglied entfernen? Der Zugang zum Panel endet sofort.")) { const r = await act.run(() => api.del(`/api/team/members/${member.id}`)); if (r) onSaved(); } }}>Entfernen</Button>}
      <span className="spacer" />
      <Button onClick={onClose}>Abbrechen</Button>
      <Button variant="primary" onClick={async () => {
        const body: Record<string, unknown> = { role_id: roleId ? Number(roleId) : null, notes,
          overrides: Object.entries(ov).filter(([, v]) => v !== undefined).map(([permission, allow]) => ({ permission, allow })) };
        if (!self) body.status = status;
        const r = await act.run(() => api.put(`/api/team/members/${member.id}`, body));
        if (r) onSaved();
      }}>Speichern</Button></>}>
      <ErrorBox error={act.error} />
      <div className="grid grid-3">
        <Field label="Rolle"><select value={roleId} onChange={(e) => setRoleId(e.target.value)}><option value="">– keine –</option>{roles.filter((r) => !r.is_owner || me?.is_owner).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Field>
        <Field label="Status"><select value={status} disabled={self} onChange={(e) => setStatus(e.target.value)}>{Object.entries(MEMBER_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="Discord-ID"><input value={member.discord_id} disabled /></Field>
      </div>
      <Field label="Notizen"><textarea value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
      <h3 style={{ margin: "6px 0" }}>Individuelle Berechtigungen</h3>
      <p className="muted small">„Rolle“ übernimmt das Recht aus der Teamrolle; „Erlauben“/„Entziehen“ überschreibt es nur für diese Person.</p>
      <table className="table">
        <thead><tr><th>Berechtigung</th><th>Aus Rolle</th><th>Einstellung</th><th>Wirksam</th></tr></thead>
        <tbody>{perms.map((p) => (
          <tr key={p.key}>
            <td>{p.label}<div className="perm-key">{p.key}</div></td>
            <td>{rolePerms.has(p.key) ? "ja" : "–"}</td>
            <td><select value={ov[p.key] === undefined ? "" : ov[p.key] ? "allow" : "deny"} style={{ width: 140 }}
              onChange={(e) => setOv({ ...ov, [p.key]: e.target.value === "" ? undefined : e.target.value === "allow" })}>
              <option value="">Rolle</option><option value="allow">Erlauben</option><option value="deny">Entziehen</option></select></td>
            <td>{effective(p.key) ? <Badge status="ok" label="erlaubt" /> : <Badge status="muted" label="nein" />}</td>
          </tr>
        ))}</tbody>
      </table>
    </Modal>
  );
}

function Roles() {
  const { can } = useAuth();
  const { data, error, reload } = useApi<Role[]>("/api/team/roles", ["team"]);
  const { data: perms } = useApi<Perm[]>("/api/team/permissions");
  const [edit, setEdit] = useState<Partial<Role> | null>(null);
  const act = useAction();
  const manage = can("team.manage");
  return (
    <>
      <ErrorBox error={error} />
      <Panel className="flush" title="Rollen" actions={manage && <Button small variant="primary" onClick={() => setEdit({ name: "", permissions: [], position: 100 })}>Neue Rolle</Button>}>
        <table className="table">
          <thead><tr><th>Rolle</th><th>Beschreibung</th><th className="num">Rechte</th><th className="num">Mitglieder</th><th /></tr></thead>
          <tbody>{(data || []).map((r) => (
            <tr key={r.id}><td>{r.name}{r.is_owner && <> <Badge status="info" label="Vollzugriff" /></>}</td><td className="muted">{r.description}</td>
              <td className="num">{r.is_owner ? "alle" : r.permissions.length}</td><td className="num">{r.member_count}</td>
              <td className="num">{manage && !r.is_owner && <><Button small onClick={() => setEdit(r)}>Bearbeiten</Button>{" "}
                <Button small variant="danger" onClick={async () => { await act.run(() => api.del(`/api/team/roles/${r.id}`)); reload(); }}>Löschen</Button></>}</td></tr>
          ))}</tbody>
        </table>
        <ErrorBox error={act.error} />
      </Panel>
      {edit && (
        <Modal wide title={edit.id ? `Rolle „${edit.name}“` : "Neue Rolle"} onClose={() => setEdit(null)} footer={<>
          <Button onClick={() => setEdit(null)}>Abbrechen</Button>
          <Button variant="primary" onClick={async () => { const r = await act.run(() => edit.id ? api.put(`/api/team/roles/${edit.id}`, edit) : api.post("/api/team/roles", edit)); if (r) { setEdit(null); reload(); } }}>Speichern</Button></>}>
          <ErrorBox error={act.error} />
          <div className="grid grid-3">
            <Field label="Name"><input value={edit.name || ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Reihenfolge"><input type="number" value={edit.position ?? 100} onChange={(e) => setEdit({ ...edit, position: Number(e.target.value) })} /></Field>
            <Field label="Zugehörige Discord-Rolle (Info)"><DiscordPicker kind="role" value={edit.discord_role_id || ""} onChange={(v) => setEdit({ ...edit, discord_role_id: v })} /></Field>
          </div>
          <Field label="Beschreibung"><input value={edit.description || ""} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
          <div className="check-grid">
            {(perms || []).map((p) => (
              <label key={p.key} className="perm-row">
                <input type="checkbox" checked={(edit.permissions || []).includes(p.key)} onChange={(e) => setEdit({ ...edit, permissions: e.target.checked ? [...(edit.permissions || []), p.key] : (edit.permissions || []).filter((x) => x !== p.key) })} />
                <span>{p.label}<div className="perm-key">{p.key}</div></span>
              </label>
            ))}
          </div>
        </Modal>
      )}
    </>
  );
}
