// Wiederverwendbare, schlichte UI-Bausteine.
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { tone } from "../lib/format";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost"; small?: boolean };
export function Button({ variant = "secondary", small, className = "", ...rest }: BtnProps) {
  return <button {...rest} className={`btn btn-${variant}${small ? " btn-sm" : ""} ${className}`} />;
}

export function Badge({ status, label }: { status: string; label?: string }) {
  return <span className={`badge badge-${tone(status)}`}>{label ?? status}</span>;
}

export function Panel({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <header className="panel-head">
          <h2>{title}</h2>
          {actions && <div className="actions">{actions}</div>}
        </header>
      )}
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function ErrorBox({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return <div className="alert alert-danger" role="alert">{error}</div>;
}

export function Notice({ children, kind = "info" }: { children: ReactNode; kind?: "info" | "warn" | "ok" }) {
  return <div className={`alert alert-${kind}`}>{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading() {
  return <div className="empty muted">Wird geladen …</div>;
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Stat({ label, value, sub, to }: { label: string; value: ReactNode; sub?: ReactNode; to?: string }) {
  const inner = (
    <>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </>
  );
  return to ? <Link to={to} className="stat stat-link">{inner}</Link> : <div className="stat">{inner}</div>;
}

export function Modal({ title, onClose, children, footer, wide }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal${wide ? " modal-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Schließen">×</button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return <div className="pagination muted">{total} Einträge</div>;
  return (
    <div className="pagination">
      <span className="muted">{total} Einträge · Seite {page} von {pages}</span>
      <Button small disabled={page <= 1} onClick={() => onPage(page - 1)}>Zurück</Button>
      <Button small disabled={page >= pages} onClick={() => onPage(page + 1)}>Weiter</Button>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { key: T; label: string; hidden?: boolean }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.filter((t) => !t.hidden).map((t) => (
        <button key={t.key} role="tab" aria-selected={value === t.key} className={value === t.key ? "tab active" : "tab"} onClick={() => onChange(t.key)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Avatar({ url, name, size = 24 }: { url?: string | null; name: string; size?: number }) {
  if (url) return <img className="avatar" src={url} alt="" width={size} height={size} referrerPolicy="no-referrer" />;
  return (
    <span className="avatar avatar-fallback" style={{ width: size, height: size, fontSize: size * 0.45 }}>
      {(name || "?").slice(0, 1).toUpperCase()}
    </span>
  );
}

export function Confirm({ text, onConfirm, onCancel, danger }: { text: string; onConfirm: () => void; onCancel: () => void; danger?: boolean }) {
  return (
    <Modal title="Bitte bestätigen" onClose={onCancel} footer={<>
      <Button onClick={onCancel}>Abbrechen</Button>
      <Button variant={danger ? "danger" : "primary"} onClick={onConfirm}>Bestätigen</Button>
    </>}>
      <p>{text}</p>
    </Modal>
  );
}

export interface TeamEntry { id: number; discord_id: string; name: string; role: string | null }

export function MemberSelect({ value, onChange, placeholder = "– niemand –", filter }: { value: number | null; onChange: (id: number | null) => void; placeholder?: string; filter?: (m: TeamEntry) => boolean }) {
  const { data } = useApi<TeamEntry[]>("/api/team/directory", ["team"]);
  return (
    <select value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">{placeholder}</option>
      {(data || []).filter(filter || (() => true)).map((m) => (
        <option key={m.id} value={m.id}>{m.name}{m.role ? ` (${m.role})` : ""}</option>
      ))}
    </select>
  );
}

interface GuildInfo { name: string | null; channels: { id: string; name: string; type: string; category: string | null }[]; roles: { id: string; name: string }[] }

export function useGuild() {
  return useApi<GuildInfo>("/api/guild", ["system"]);
}

/** Auswahl eines Discord-Channels oder einer Rolle; fällt auf Freitext (ID) zurück, solange der Bot keine Daten geliefert hat. */
export function DiscordPicker({ kind, value, onChange, channelTypes }: { kind: "channel" | "role"; value: string; onChange: (v: string) => void; channelTypes?: string[] }) {
  const { data } = useGuild();
  const [manual, setManual] = useState(false);
  const items = kind === "role"
    ? data?.roles || []
    : (data?.channels || []).filter((c) => !channelTypes || channelTypes.includes(c.type));
  if (manual || items.length === 0) {
    return (
      <div className="row">
        <input value={value} onChange={(e) => onChange(e.target.value.trim())} placeholder="Discord-ID" inputMode="numeric" />
        {items.length > 0 && <Button small onClick={() => setManual(false)}>Liste</Button>}
      </div>
    );
  }
  const known = items.some((i) => i.id === value);
  return (
    <div className="row">
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">– nicht gesetzt –</option>
        {!known && value && <option value={value}>{value} (unbekannt)</option>}
        {items.map((i) => (
          <option key={i.id} value={i.id}>
            {kind === "channel" ? `#${i.name}${"category" in i && i.category ? ` (${i.category})` : ""}` : `@${i.name}`}
          </option>
        ))}
      </select>
      <Button small onClick={() => setManual(true)}>ID</Button>
    </div>
  );
}

/** Einfaches Balkendiagramm (eine Reihe, eine Farbe) mit Hover-Tooltip und Tabellenalternative. */
export function BarChart({ data, label, height = 120 }: { data: { key: string; label: string; value: number }[]; label: string; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...data.map((d) => d.value));
  const w = 100 / Math.max(1, data.length);
  return (
    <figure className="chart" aria-label={label}>
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" role="img" aria-label={label} style={{ height }}>
        <line x1="0" y1={height - 0.5} x2="100" y2={height - 0.5} className="chart-axis" vectorEffect="non-scaling-stroke" />
        {data.map((d, i) => {
          const h = (d.value / max) * (height - 8);
          return (
            <g key={d.key} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={i * w} y={0} width={w} height={height} fill="transparent" />
              {d.value > 0 && (
                <rect x={i * w + w * 0.15} y={height - h} width={w * 0.7} height={h} rx={0.6} className={hover === i ? "chart-bar active" : "chart-bar"} />
              )}
            </g>
          );
        })}
      </svg>
      {hover !== null && data[hover] && (
        <div className="chart-tip" style={{ left: `${Math.min(85, hover * w)}%` }}>
          <strong>{data[hover].value.toLocaleString("de-DE")}</strong> · {data[hover].label}
        </div>
      )}
      <figcaption className="chart-caption">
        <span>{data[0]?.label}</span>
        <span>{label} · max. {max.toLocaleString("de-DE")}</span>
        <span>{data[data.length - 1]?.label}</span>
      </figcaption>
    </figure>
  );
}

/** Horizontale Verteilungsbalken (z. B. pro Mitarbeiter), Werte als Text. */
export function HBars({ rows, unit = "" }: { rows: { label: string; value: number; sub?: string }[]; unit?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.length) return <Empty>Keine Daten im Zeitraum.</Empty>;
  return (
    <table className="hbars">
      <tbody>
        {rows.map((r) => (
          <tr key={r.label}>
            <td className="hbars-label">{r.label}</td>
            <td className="hbars-bar"><span style={{ width: `${(r.value / max) * 100}%` }} /></td>
            <td className="num">{r.value.toLocaleString("de-DE")}{unit}{r.sub && <span className="muted"> · {r.sub}</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
