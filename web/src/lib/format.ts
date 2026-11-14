// Formatierung und deutsche Bezeichnungen.

// Der Server speichert UTC ohne Zeitzonenangabe – hier explizit als UTC interpretieren.
export function toDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const s = /[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z";
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

export function fmtDateTime(iso: string | null | undefined): string {
  const d = toDate(iso);
  return d ? d.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "–";
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = iso.length === 10 ? new Date(iso + "T00:00:00") : toDate(iso);
  return d ? d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" }) : "–";
}

export function fmtRelative(iso: string | null | undefined): string {
  const d = toDate(iso);
  if (!d) return "–";
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "gerade eben";
  if (diff < 3600) return `vor ${Math.floor(diff / 60)} Min.`;
  if (diff < 86400) return `vor ${Math.floor(diff / 3600)} Std.`;
  if (diff < 86400 * 30) return `vor ${Math.floor(diff / 86400)} Tagen`;
  return fmtDate(iso);
}

export function fmtHours(h: number | null | undefined): string {
  if (h === null || h === undefined) return "–";
  if (h < 1) return `${Math.round(h * 60)} Min.`;
  if (h < 48) return `${h.toLocaleString("de-DE", { maximumFractionDigits: 1 })} Std.`;
  return `${(h / 24).toLocaleString("de-DE", { maximumFractionDigits: 1 })} Tage`;
}

export function fmtNumber(n: number | null | undefined): string {
  return n === null || n === undefined ? "–" : n.toLocaleString("de-DE");
}

export const TICKET_STATUS: Record<string, string> = { open: "Offen", in_progress: "In Bearbeitung", waiting: "Wartend", closed: "Geschlossen" };
export const TICKET_PRIORITY: Record<string, string> = { low: "Niedrig", normal: "Normal", high: "Hoch", urgent: "Dringend" };
export const BUG_STATUS: Record<string, string> = {
  new: "Neu", claimed: "Übernommen", in_progress: "In Bearbeitung", fixed: "Behoben", not_reproducible: "Nicht reproduzierbar",
};
export const BUG_PRIORITY: Record<string, string> = { low: "Niedrig", normal: "Normal", high: "Hoch", critical: "Kritisch" };
export const APP_STATUS: Record<string, string> = { new: "Neu", in_review: "In Prüfung", inquiry: "Rückfragen", accepted: "Angenommen", rejected: "Abgelehnt" };
export const TESTER_STATUS: Record<string, string> = { active: "Aktiv", inactive: "Inaktiv", blocked: "Gesperrt", retired: "Ausgeschieden" };
export const VERSION_STATUS: Record<string, string> = {
  development: "In Entwicklung", internal_test: "Interner Test", beta: "Beta", released: "Veröffentlicht", archived: "Archiviert",
};
export const MEMBER_STATUS: Record<string, string> = { active: "Aktiv", inactive: "Inaktiv", suspended: "Gesperrt" };
export const FIELD_TYPES: Record<string, string> = {
  text: "Textfeld", textarea: "Mehrzeiliges Textfeld", select: "Auswahlfeld", multiselect: "Mehrfachauswahl", yesno: "Ja/Nein-Frage",
};
export const MOD_ACTIONS: Record<string, string> = {
  warn: "Verwarnung", timeout: "Timeout", untimeout: "Timeout aufheben", kick: "Kick", ban: "Bann", unban: "Entbannung",
};

/** Tonalität für Statusanzeigen. */
export function tone(status: string): "neutral" | "info" | "warn" | "ok" | "danger" | "muted" {
  switch (status) {
    case "new":
    case "open":
    case "pending":
    case "queued":
    case "draft":
      return "info";
    case "claimed":
    case "in_progress":
    case "in_review":
    case "processing":
    case "beta":
    case "internal_test":
      return "warn";
    case "waiting":
    case "inquiry":
    case "inactive":
    case "development":
      return "neutral";
    case "fixed":
    case "accepted":
    case "active":
    case "done":
    case "sent":
    case "executed":
    case "released":
    case "ok":
      return "ok";
    case "rejected":
    case "failed":
    case "blocked":
    case "suspended":
    case "error":
    case "critical":
    case "urgent":
      return "danger";
    default:
      return "muted";
  }
}

export function discordAvatar(url: string | null | undefined): string | undefined {
  return url || undefined;
}

export const OUTBOX_STATUS: Record<string, string> = { pending: "wartend", processing: "in Arbeit", done: "erledigt", failed: "fehlgeschlagen" };
