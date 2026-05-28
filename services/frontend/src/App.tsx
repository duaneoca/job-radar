import { useEffect } from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Layout } from "./components/layout/Layout";
import { Toaster } from "./components/ui/toaster";
import { LoginPage } from "./pages/LoginPage";
import { SignupPage } from "./pages/SignupPage";
import { JobsPage } from "./pages/JobsPage";
import { JobDetailPage } from "./pages/JobDetailPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ProfilePage } from "./pages/ProfilePage";
import { AdminPage } from "./pages/AdminPage";
import { AddJobPage } from "./pages/AddJobPage";
import { useAuthStore } from "./store/auth";
import { authApi } from "./lib/api";
import type { User } from "./lib/types";

// Rehydrate auth from cookie on first load
function AuthProvider({ children }: { children: React.ReactNode }) {
  const { user, setUser } = useAuthStore();

  const { data } = useQuery<User>({
    queryKey: ["me"],
    queryFn: () => authApi.get("/auth/me").then((r) => r.data),
    enabled: !user,
    retry: false,
    staleTime: 5 * 60_000,
  });

  useEffect(() => {
    if (data && !user) setUser(data);
  }, [data, user, setUser]);

  return <>{children}</>;
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuthStore();
  const location = useLocation();
  if (!isAuthenticated()) return <Navigate to="/login" state={{ from: location }} replace />;
  return <>{children}</>;
}

function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { user } = useAuthStore();
  if (!user?.is_admin) return <Navigate to="/jobs" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        {/* Public */}
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />

        {/* Protected */}
        <Route
          path="/jobs"
          element={
            <RequireAuth>
              <Layout>
                <JobsPage />
              </Layout>
            </RequireAuth>
          }
        />
        <Route
          path="/jobs/add"
          element={
            <RequireAuth>
              <Layout>
                <AddJobPage />
              </Layout>
            </RequireAuth>
          }
        />
        <Route
          path="/jobs/:id"
          element={
            <RequireAuth>
              <Layout>
                <JobDetailPage />
              </Layout>
            </RequireAuth>
          }
        />
        <Route
          path="/profile"
          element={
            <RequireAuth>
              <Layout>
                <ProfilePage />
              </Layout>
            </RequireAuth>
          }
        />
        <Route
          path="/settings"
          element={
            <RequireAuth>
              <Layout>
                <SettingsPage />
              </Layout>
            </RequireAuth>
          }
        />
        <Route
          path="/admin"
          element={
            <RequireAuth>
              <RequireAdmin>
                <Layout>
                  <AdminPage />
                </Layout>
              </RequireAdmin>
            </RequireAuth>
          }
        />

        {/* Default */}
        <Route path="/" element={<Navigate to="/jobs" replace />} />
        <Route path="*" element={<Navigate to="/jobs" replace />} />
      </Routes>
      <Toaster />
    </AuthProvider>
  );
}
