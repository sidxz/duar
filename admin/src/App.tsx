import { Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthGuard } from "./components/AuthGuard";
import { Layout } from "./components/Layout";
import { useTheme } from "./lib/theme";
import { Activity } from "./pages/Activity";
import { ClientAppDetail } from "./pages/ClientAppDetail";
import { ClientApps } from "./pages/ClientApps";
import { ActionsInsightsPage } from "./pages/ActionsInsights";
import { Dashboard } from "./pages/Dashboard";
import { Insights } from "./pages/Insights";
import { ServiceAppDetail } from "./pages/ServiceAppDetail";
import { ServiceApps } from "./pages/ServiceApps";
import { Permissions } from "./pages/Permissions";
import { ServiceActions } from "./pages/ServiceActions";
import { Settings } from "./pages/Settings";
import { SystemHealth } from "./pages/SystemHealth";
import { UserDetail } from "./pages/UserDetail";
import { Users } from "./pages/Users";
import { WorkspaceDetail } from "./pages/WorkspaceDetail";
import { Workspaces } from "./pages/Workspaces";
import { Organizations } from "./pages/Organizations";
import { OrganizationDetail } from "./pages/OrganizationDetail";
import { Realms } from "./pages/Realms";
import { RealmDetail } from "./pages/RealmDetail";

export default function App() {
  const { theme } = useTheme();
  return (
    <AuthGuard>
      <Toaster theme={theme} position="bottom-right" richColors />
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/users" element={<Users />} />
          <Route path="/users/:id" element={<UserDetail />} />
          <Route path="/workspaces" element={<Workspaces />} />
          <Route path="/workspaces/:id" element={<WorkspaceDetail />} />
          <Route path="/permissions" element={<Permissions />} />
          <Route path="/service-actions" element={<ServiceActions />} />
          <Route path="/client-apps" element={<ClientApps />} />
          <Route path="/client-apps/:id" element={<ClientAppDetail />} />
          <Route path="/service-apps" element={<ServiceApps />} />
          <Route path="/service-apps/:id" element={<ServiceAppDetail />} />
          <Route path="/organizations" element={<Organizations />} />
          <Route path="/organizations/:id" element={<OrganizationDetail />} />
          <Route path="/realms" element={<Realms />} />
          <Route path="/realms/:id" element={<RealmDetail />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/insights" element={<Insights />} />
          <Route path="/usage" element={<ActionsInsightsPage />} />
          <Route path="/system" element={<SystemHealth />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </Layout>
    </AuthGuard>
  );
}
