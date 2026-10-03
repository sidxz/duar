import { createContext, useContext, useEffect, useState } from "react";
import { getAdminMe } from "../api/client";
import { Login } from "../pages/Login";

interface AdminUser {
  id: string;
  email: string;
  name: string;
}

const AdminContext = createContext<AdminUser | null>(null);

// eslint-disable-next-line react-refresh/only-export-components
export function useAdmin() {
  const ctx = useContext(AdminContext);
  if (!ctx) throw new Error("useAdmin must be used within AuthGuard");
  return ctx;
}

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const [admin, setAdmin] = useState<AdminUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getAdminMe()
      .then(setAdmin)
      .catch(() => setAdmin(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-primary" />
      </div>
    );
  }

  if (!admin) {
    return <Login />;
  }

  return <AdminContext.Provider value={admin}>{children}</AdminContext.Provider>;
}
