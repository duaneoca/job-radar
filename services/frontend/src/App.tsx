import { useEffect } from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { useDarkMode } from "./hooks/useDarkMode";
import { useQuery } from "@tanstack/react-query";
import { Layout } from "./components/layout/Layout";
import { Toaster } from "./components/ui/toaster";
import { LandingPage } from "./pages/LandingPage";
import { LoginPage } from "./pages/LoginPage";
import { SignupPage } from "./pages/SignupPage";
import { JobsPage } from "./pages/JobsPage";
import { InboxPage } from "./pages/InboxPage";
import { RecruitersPage } from "./pages/RecruitersPage";
import { JobDetailPage } from "./pages/JobDetailPage";
import { TailorReviewPage } from "./pages/TailorReviewPage";
import { TailorPrintPage } from "./pages/TailorPrintPage";
import { SettingsPage } from "./pages/SettingsPage";
import { LocalAgentSetupPage } from "./pages/LocalAgentSetupPage";
import { ProfilePage } from "./pages/ProfilePage";
import { AdminPage } from "./pages/AdminPage";
import { AddJobPage } from "./pages/AddJobPage";
import { HelpPage } from "./pages/HelpPage";
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

// Email agent is a global admin toggle — when off, agent pages bounce to /jobs.
function RequireAgentEnabled({ children }: { children: React.ReactNode }) {
  const { user } = useAuthStore();
  if (!user?.email_agent_enabled) return <Navigate to="/jobs" replace />;
  return <>{children}</>;
}

// Root: public marketing page for logged-out visitors, jobs for logged-in users.
// Validate the session with the server rather than trusting a possibly-stale
// persisted user, so an expired/absent session correctly shows the landing page
// (not a bounce to /login).
function RootRoute() {
  const setUser = useAuthStore((s) => s.setUser);
  const { data, isLoading, isError } = useQuery<User>({
    queryKey: ["me"],
    queryFn: () => authApi.get("/auth/me").then((r) => r.data),
    retry: false,
    staleTime: 5 * 60_000,
  });

  useEffect(() => {
    if (data) setUser(data);
    else if (isError) setUser(null); // clear a stale persisted session
  }, [data, isError, setUser]);

  if (isLoading) return null; // brief — avoids flashing the wrong view
  return data ? <Navigate to="/jobs" replace /> : <LandingPage />;
}

export default function App() {
  useDarkMode(); // Apply system/saved theme on every page including login

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
          path="/inbox"
          element={
            <RequireAuth>
              <RequireAgentEnabled>
                <Layout>
                  <InboxPage />
                </Layout>
              </RequireAgentEnabled>
            </RequireAuth>
          }
        />
        <Route
          path="/recruiters"
          element={
            <RequireAuth>
              <Layout>
                <RecruitersPage />
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
          path="/jobs/:id/tailor/print"
          element={
            <RequireAuth>
              <TailorPrintPage />
            </RequireAuth>
          }
        />
        <Route
          path="/jobs/:id/tailor"
          element={
            <RequireAuth>
              <TailorReviewPage />
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
          path="/settings/agent-setup"
          element={
            <RequireAuth>
              <Layout>
                <LocalAgentSetupPage />
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

        <Route
          path="/help"
          element={
            <RequireAuth>
              <Layout>
                <HelpPage />
              </Layout>
            </RequireAuth>
          }
        />

        {/* Default */}
        <Route path="/" element={<RootRoute />} />
        <Route path="*" element={<Navigate to="/jobs" replace />} />
      </Routes>
      <Toaster />
    </AuthProvider>
  );
}
