import axios from "axios";

// Single axios instance — all backend calls share the same base URL & cookie
const client = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? "http://localhost:8000",
  withCredentials: true, // send httpOnly JWT cookie on every request
});

// Redirect to /login on 401 (except when already on /login)
client.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401 && window.location.pathname !== "/login") {
      window.location.href = "/login";
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
export const adminApi = client;
