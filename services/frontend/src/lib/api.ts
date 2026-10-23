import axios from "axios";
import { useAuthStore } from "../store/auth";
import { loginUrl } from "./loginRedirect";

// Single axios instance — all backend calls share the same base URL & cookie
const client = axios.create({
  // /api = same-origin prefix (nginx strips /api/ and proxies to tracker-api in k8s)
  // Set VITE_API_URL in .env.local for local dev (e.g. http://localhost:8000)
  baseURL: import.meta.env.VITE_API_URL ?? "/api",
  withCredentials: true, // send httpOnly JWT cookie on every request
});

// Redirect to /login on 401 — but NOT for the `/auth/me` session probe (a 401
// there just means "logged out", a valid answer the app handles), and NOT while
// on a public route (landing / signup / login), so logged-out visitors see the
// marketing page instead of being bounced to /login.
const PUBLIC_PATHS = new Set(["/", "/login", "/signup"]);
client.interceptors.response.use(
  (r) => r,
  (err) => {
    const url: string = err.config?.url ?? "";
    const isAuthProbe = url.includes("/auth/me");
    if (
      err.response?.status === 401 &&
      !isAuthProbe &&
      !PUBLIC_PATHS.has(window.location.pathname)
    ) {
      // The session is gone: forget the stored user too, or every guarded page
      // (and the OAuth consent page) keeps believing it's signed in — a Back
      // press or another tab would bounce through more 401s. Then sign in and
      // come back here (lib/loginRedirect.ts).
      useAuthStore.getState().setUser(null);
      window.location.href = loginUrl(window.location);
    }
    return Promise.reject(err);
  }
);

export default client;

// Re-export the same instance under domain-specific names for readability.
// All pages call e.g. `authApi.post("/auth/login", body)` which is just
// `client.post("/auth/login", body)`.
export const authApi = client;
export const jobsApi = client;
export const criteriaApi = client;
export const profileApi = client;
export const keysApi = client;
export const connectionsApi = client;
export const recruitersApi = client;
export const adminApi = client;
export const agentApi = client;
