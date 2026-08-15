import { AuthGuard } from "@duar-auth/react";
import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { AuthCallback } from "./pages/AuthCallback";
import { Export } from "./pages/Export";
import { Login } from "./pages/Login";
import { NoteDetail } from "./pages/NoteDetail";
import { NoteList } from "./pages/NoteList";

export default function App() {
  return (
    <Routes>
      {/* OAuth callback — outside AuthGuard since user isn't authenticated yet */}
      <Route path="/auth/callback" element={<AuthCallback />} />

      {/* Authenticated routes */}
      <Route
        path="*"
        element={
          <AuthGuard
            fallback={<Login />}
            loading={
              <div className="flex h-screen items-center justify-center bg-zinc-950">
                <div className="h-6 w-6 animate-spin rounded-full border-2 border-zinc-600 border-t-zinc-300" />
              </div>
            }
          >
            <Layout>
              <Routes>
                <Route path="/" element={<NoteList />} />
                <Route path="/notes" element={<NoteList />} />
                <Route path="/notes/export" element={<Export />} />
                <Route path="/notes/:id" element={<NoteDetail />} />
              </Routes>
            </Layout>
          </AuthGuard>
        }
      />
    </Routes>
  );
}
