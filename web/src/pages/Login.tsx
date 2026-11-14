import { useSearchParams } from "react-router-dom";

const ERRORS: Record<string, string> = {
  not_authorized: "Dein Discord-Konto ist nicht als Teammitglied freigeschaltet. Bitte wende dich an die Projektleitung.",
  not_configured: "Der Discord-Login ist noch nicht eingerichtet (Client ID / Redirect URL fehlen).",
  state: "Die Anmeldung ist abgelaufen oder ungültig. Bitte erneut versuchen.",
  discord: "Discord ist derzeit nicht erreichbar oder hat die Anmeldung abgelehnt.",
  cancelled: "Die Anmeldung wurde abgebrochen.",
};

export default function Login() {
  const [params] = useSearchParams();
  const error = params.get("error");
  return (
    <div className="login-wrap">
      <div className="login-box">
        <h1>Leitstelle Bavaria Control</h1>
        <p className="muted" style={{ marginTop: 6 }}>Verwaltungs- und Controlling-Panel. Zugang nur für freigeschaltete Teammitglieder.</p>
        {error && <div className="alert alert-danger" style={{ marginTop: 14 }}>{ERRORS[error] || "Anmeldung fehlgeschlagen."}</div>}
        <a className="btn btn-primary" style={{ width: "100%", marginTop: 16 }} href="/api/auth/login">Mit Discord anmelden</a>
        <p className="muted small" style={{ marginTop: 14, marginBottom: 0 }}>
          Discord dient ausschließlich der Identifikation. Berechtigungen werden intern vergeben.
        </p>
      </div>
    </div>
  );
}
