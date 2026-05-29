import { useState } from "react";
import { NavLink, useNavigate, Link } from "react-router-dom";
import { Menu, Briefcase, Radar, Settings, Users, LogOut, X, Sun, Moon, Monitor, User, CircleHelp } from "lucide-react";
import { Button } from "../ui/button";
import { Separator } from "../ui/separator";
import { useDarkMode, type ThemeMode } from "../../hooks/useDarkMode";
import { useAuthStore } from "../../store/auth";
import { authApi } from "../../lib/api";

interface LayoutProps {
  children: React.ReactNode;
}

const navItems = [
  { to: "/jobs",     label: "Jobs",     icon: Briefcase },
  { to: "/profile",  label: "Profile",  icon: User },
  { to: "/settings", label: "Settings", icon: Settings },
];

const themeOptions: { value: ThemeMode; label: string; icon: typeof Sun }[] = [
  { value: "light",  label: "Light",  icon: Sun },
  { value: "system", label: "System", icon: Monitor },
  { value: "dark",   label: "Dark",   icon: Moon },
];

export function Layout({ children }: LayoutProps) {
  const { mode, setMode } = useDarkMode();
  const { user, setUser } = useAuthStore();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  async function handleLogout() {
    setOpen(false);
    try { await authApi.post("/auth/logout"); } catch { /* ignore */ }
    setUser(null);
    navigate("/login");
  }

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Top bar */}
      <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="flex h-14 items-center gap-3 px-4">
          <Button variant="ghost" size="icon" onClick={() => setOpen(true)} aria-label="Open menu">
            <Menu className="h-5 w-5" />
          </Button>
          <NavLink to="/jobs" className="flex items-center gap-2 font-bold text-primary flex-1 hover:opacity-80 transition-opacity">
            <Radar className="h-5 w-5" />
            <span>Job Radar</span>
          </NavLink>
          {/* Help link */}
          <Link to="/help" aria-label="Help" className="text-muted-foreground hover:text-foreground transition-colors">
            <CircleHelp className="h-5 w-5" />
          </Link>

          {/* User name / initials */}
          {user && (
            <button
              onClick={() => setOpen(true)}
              className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              <div className="h-7 w-7 rounded-full bg-primary/15 text-primary flex items-center justify-center text-xs font-semibold shrink-0">
                {(user.full_name || user.email).charAt(0).toUpperCase()}
              </div>
              <span className="hidden sm:block max-w-[120px] truncate">
                {user.full_name || user.email.split("@")[0]}
              </span>
            </button>
          )}
        </div>
      </header>

      {/* Overlay */}
      {open && (
        <div className="fixed inset-0 z-50 bg-black/50" onClick={() => setOpen(false)} />
      )}

      {/* Slide-out nav drawer */}
      <aside
        className={`fixed top-0 left-0 z-50 h-full w-64 bg-background border-r shadow-xl flex flex-col transition-transform duration-300 ease-in-out ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        {/* Drawer header */}
        <div className="flex items-center justify-between h-14 px-4 border-b shrink-0">
          <div className="flex items-center gap-2 font-bold text-primary">
            <Radar className="h-5 w-5" />
            Job Radar
          </div>
          <Button variant="ghost" size="icon" onClick={() => setOpen(false)} aria-label="Close menu">
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* User info */}
        <div className="px-4 py-3 border-b">
          <p className="text-xs text-muted-foreground">Signed in as</p>
          <p className="text-sm font-medium truncate">{user?.full_name || user?.email}</p>
          {user?.full_name && (
            <p className="text-xs text-muted-foreground truncate">{user.email}</p>
          )}
        </div>

        {/* Nav links */}
        <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-1">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
                }`
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </NavLink>
          ))}

          {user?.is_admin && (
            <>
              <Separator className="my-2" />
              <p className="px-3 py-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                Admin
              </p>
              <NavLink
                to="/admin"
                onClick={() => setOpen(false)}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                    isActive
                      ? "bg-accent text-accent-foreground"
                      : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
                  }`
                }
              >
                <Users className="h-4 w-4 shrink-0" />
                User Management
              </NavLink>
            </>
          )}
        </nav>

        {/* Bottom: theme picker + sign out */}
        <div className="px-4 py-4 border-t space-y-3 shrink-0">
          {/* Three-way theme toggle */}
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground font-medium">Theme</p>
            <div className="flex rounded-lg border overflow-hidden">
              {themeOptions.map(({ value, label, icon: Icon }) => (
                <button
                  key={value}
                  onClick={() => setMode(value)}
                  className={`flex-1 flex flex-col items-center gap-0.5 py-1.5 text-xs transition-colors ${
                    mode === value
                      ? "bg-accent text-accent-foreground font-medium"
                      : "text-muted-foreground hover:text-foreground hover:bg-accent/40"
                  }`}
                  aria-label={label}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                </button>
              ))}
            </div>
          </div>

          <button
            onClick={handleLogout}
            className="flex items-center gap-3 w-full px-3 py-2 rounded-md text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
          >
            <LogOut className="h-4 w-4 shrink-0" />
            Sign out
          </button>
        </div>
      </aside>

      {/* Page content */}
      <main className="flex-1 container px-4 py-6">{children}</main>
    </div>
  );
}
