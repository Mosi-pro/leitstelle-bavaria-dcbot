import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import Layout, { NAV } from "./components/Layout";
import { Empty, Loading } from "./components/ui";
import { useAuth } from "./lib/auth";
import Applications, { ApplicationDetail } from "./pages/Applications";
import Audit from "./pages/Audit";
import Beta, { TesterDetail } from "./pages/Beta";
import Bugs, { BugDetail } from "./pages/Bugs";
import Community from "./pages/Community";
import Controlling from "./pages/Controlling";
import Dashboard from "./pages/Dashboard";
import Development, { VersionDetail } from "./pages/Development";
import Login from "./pages/Login";
import Settings from "./pages/Settings";
import Team from "./pages/Team";
import Tickets, { TicketDetail } from "./pages/Tickets";

function Guard({ perm, children }: { perm: string; children: ReactNode }) {
  const { can } = useAuth();
  if (!can(perm)) return <Empty>Für diesen Bereich fehlt dir die Berechtigung <span className="mono">{perm}</span>.</Empty>;
  return <>{children}</>;
}

function Home() {
  const { can } = useAuth();
  if (can("dashboard.view")) return <Dashboard />;
  const first = NAV.find((n) => can(n.perm));
  return first ? <Navigate to={first.to} replace /> : <Empty>Deinem Konto sind noch keine Berechtigungen zugewiesen.</Empty>;
}

export default function App() {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Loading />;
  if (!me) {
    if (location.pathname !== "/login") return <Navigate to="/login" replace />;
    return <Login />;
  }
  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="tickets" element={<Guard perm="tickets.view"><Tickets /></Guard>} />
        <Route path="tickets/:id" element={<Guard perm="tickets.view"><TicketDetail /></Guard>} />
        <Route path="applications" element={<Guard perm="applications.view"><Applications /></Guard>} />
        <Route path="applications/:id" element={<Guard perm="applications.view"><ApplicationDetail /></Guard>} />
        <Route path="beta" element={<Guard perm="beta.view"><Beta /></Guard>} />
        <Route path="beta/:id" element={<Guard perm="beta.view"><TesterDetail /></Guard>} />
        <Route path="bugs" element={<Guard perm="bugs.view"><Bugs /></Guard>} />
        <Route path="bugs/:id" element={<Guard perm="bugs.view"><BugDetail /></Guard>} />
        <Route path="development" element={<Guard perm="versions.view"><Development /></Guard>} />
        <Route path="development/:id" element={<Guard perm="versions.view"><VersionDetail /></Guard>} />
        <Route path="community" element={<Guard perm="community.view"><Community /></Guard>} />
        <Route path="team" element={<Guard perm="team.view"><Team /></Guard>} />
        <Route path="controlling" element={<Guard perm="controlling.view"><Controlling /></Guard>} />
        <Route path="audit" element={<Guard perm="logs.view"><Audit /></Guard>} />
        <Route path="settings" element={<Guard perm="settings.manage"><Settings /></Guard>} />
        <Route path="*" element={<Empty>Seite nicht gefunden.</Empty>} />
      </Route>
    </Routes>
  );
}
