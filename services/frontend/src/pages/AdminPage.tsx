import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle, XCircle, ShieldCheck, ShieldOff, Loader2,
  ChevronLeft, ChevronRight, KeyRound, Trash2, RefreshCw, Brain,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Badge } from "../components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs";
import { adminApi } from "../lib/api";
import { formatDate } from "../lib/utils";
import { toast } from "../hooks/useToast";
import type { PaginatedUsers, AdminUser } from "../lib/types";

// ─── Inline password reset form ──────────────────────────────────────────────
function ResetPasswordRow({ user, onDone }: { user: AdminUser; onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pw.length < 8) {
      toast({ title: "Password must be at least 8 characters", variant: "destructive" });
      return;
    }
    setSaving(true);
    try {
      await adminApi.post(`/admin/users/${user.id}/reset-password`, { new_password: pw });
      toast({ title: `Password reset for ${user.full_name || user.email}`, description: "User will be forced to change it on next login." });
      onDone();
    } catch (err: any) {
      toast({ title: "Reset failed", description: err?.response?.data?.detail, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex items-center gap-2 mt-2 pl-8" onClick={(e) => e.stopPropagation()}>
      <Input
        type="password"
        placeholder="Temporary password (min 8 chars)"
        value={pw}
        onChange={(e) => setPw(e.target.value)}
        className="h-7 text-xs max-w-[220px]"
        autoFocus
      />
      <Button size="sm" className="h-7 text-xs" disabled={saving || !pw}>
        {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : "Set password"}
      </Button>
      <Button size="sm" variant="ghost" className="h-7 text-xs" type="button" onClick={onDone}>
        Cancel
      </Button>
    </form>
  );
}

// ─── User table ───────────────────────────────────────────────────────────────
function UserTable({ approved }: { approved: boolean }) {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [resetFor, setResetFor] = useState<string | null>(null);
  const pageSize = 20;

  const { data, isLoading } = useQuery<PaginatedUsers>({
    queryKey: ["admin-users", approved, page],
    queryFn: () =>
      adminApi
        .get("/admin/users", { params: { approved, skip: (page - 1) * pageSize, limit: pageSize } })
        .then((r) => r.data),
  });

  const approveMut = useMutation({
    mutationFn: (id: string) => adminApi.post(`/admin/users/${id}/approve`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["admin-users"] }); toast({ title: "User approved" }); },
    onError: () => toast({ title: "Failed to approve", variant: "destructive" }),
  });

  const rejectMut = useMutation({
    mutationFn: (id: string) => adminApi.post(`/admin/users/${id}/reject`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["admin-users"] }); toast({ title: "User rejected" }); },
    onError: () => toast({ title: "Failed to reject", variant: "destructive" }),
  });

  const toggleAdminMut = useMutation({
    mutationFn: (id: string) => adminApi.patch(`/admin/users/${id}/toggle-admin`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["admin-users"] }); toast({ title: "Admin role toggled" }); },
    onError: () => toast({ title: "Failed to toggle admin", variant: "destructive" }),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => adminApi.delete(`/admin/users/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["admin-users"] }); toast({ title: "User deleted" }); },
    onError: () => toast({ title: "Failed to delete user", variant: "destructive" }),
  });

  function confirmDelete(u: AdminUser) {
    if (!confirm(`Permanently delete ${u.full_name || u.email} and all their data? This cannot be undone.`)) return;
    deleteMut.mutate(u.id);
  }

  const users = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (users.length === 0) {
    return <p className="text-muted-foreground text-sm py-8">No users in this state.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/50">
              <th className="text-left px-3 py-2.5 font-medium">User</th>
              <th className="text-left px-3 py-2.5 font-medium hidden sm:table-cell">Joined</th>
              <th className="text-left px-3 py-2.5 font-medium w-20">Role</th>
              <th className="px-3 py-2.5 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <>
                <tr key={u.id} className="border-b last:border-0">
                  <td className="px-3 py-2.5">
                    <div className="font-medium">{u.full_name || u.email}</div>
                    {u.full_name && <div className="text-xs text-muted-foreground">{u.email}</div>}
                    {u.must_change_password && (
                      <span className="text-xs text-yellow-600 dark:text-yellow-400">⚠ Must change password</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 hidden sm:table-cell text-sm text-muted-foreground">
                    {formatDate(u.created_at)}
                  </td>
                  <td className="px-3 py-2.5">
                    {u.is_admin ? (
                      <Badge className="bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200 text-xs">Admin</Badge>
                    ) : (
                      <Badge variant="outline" className="text-xs">User</Badge>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1 justify-end flex-wrap">
                      {!approved && (
                        <>
                          <Button
                            size="sm" variant="ghost"
                            className="h-7 text-green-600 hover:text-green-700 hover:bg-green-50 dark:hover:bg-green-950"
                            onClick={() => approveMut.mutate(u.id)}
                            disabled={approveMut.isPending}
                          >
                            <CheckCircle className="h-4 w-4 mr-1" />Approve
                          </Button>
                          <Button
                            size="sm" variant="ghost"
                            className="h-7 text-destructive hover:bg-destructive/10"
                            onClick={() => rejectMut.mutate(u.id)}
                            disabled={rejectMut.isPending}
                          >
                            <XCircle className="h-4 w-4 mr-1" />Reject
                          </Button>
                        </>
                      )}
                      {approved && (
                        <>
                          <Button
                            size="sm" variant="ghost"
                            className="h-7 text-muted-foreground hover:text-foreground"
                            onClick={() => setResetFor(resetFor === u.id ? null : u.id)}
                            title="Reset password"
                          >
                            <KeyRound className="h-4 w-4" />
                          </Button>
                          <Button
                            size="sm" variant="ghost"
                            className="h-7 text-muted-foreground hover:text-foreground"
                            onClick={() => toggleAdminMut.mutate(u.id)}
                            disabled={toggleAdminMut.isPending}
                            title={u.is_admin ? "Remove admin" : "Make admin"}
                          >
                            {u.is_admin ? <ShieldOff className="h-4 w-4" /> : <ShieldCheck className="h-4 w-4" />}
                          </Button>
                          <Button
                            size="sm" variant="ghost"
                            className="h-7 text-destructive hover:bg-destructive/10"
                            onClick={() => confirmDelete(u)}
                            disabled={deleteMut.isPending}
                            title="Delete user"
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
                {resetFor === u.id && (
                  <tr key={`${u.id}-reset`} className="border-b bg-muted/20">
                    <td colSpan={4} className="px-3 py-2">
                      <ResetPasswordRow user={u} onDone={() => setResetFor(null)} />
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center gap-3 justify-end text-sm">
          <span className="text-muted-foreground">Page {page} of {totalPages}</span>
          <div className="flex gap-1">
            <Button variant="outline" size="icon" className="h-7 w-7" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}>
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button variant="outline" size="icon" className="h-7 w-7" onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages}>
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── System triggers ──────────────────────────────────────────────────────────
function SystemTab() {
  const [scraping, setScraping] = useState(false);
  const [evaluating, setEvaluating] = useState(false);

  async function triggerScrape() {
    setScraping(true);
    try {
      await adminApi.post("/admin/trigger-scrape");
      toast({ title: "Scrape enqueued", description: "Jobs will appear within a few minutes." });
    } catch (err: any) {
      toast({ title: "Failed to trigger scrape", description: err?.response?.data?.detail, variant: "destructive" });
    } finally {
      setScraping(false);
    }
  }

  async function triggerEvaluate() {
    setEvaluating(true);
    try {
      const res = await adminApi.post("/admin/trigger-evaluate");
      toast({ title: "Evaluation enqueued", description: res.data.detail });
    } catch (err: any) {
      toast({ title: "Failed to trigger evaluation", description: err?.response?.data?.detail, variant: "destructive" });
    } finally {
      setEvaluating(false);
    }
  }

  return (
    <div className="space-y-6 max-w-md">
      <div className="rounded-lg border p-4 space-y-3">
        <div>
          <h3 className="font-medium">Manual scrape</h3>
          <p className="text-sm text-muted-foreground mt-1">
            Trigger an immediate scrape run across all job sources. Normally runs automatically every 2 hours.
          </p>
        </div>
        <Button onClick={triggerScrape} disabled={scraping} variant="outline">
          {scraping ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <RefreshCw className="h-4 w-4 mr-2" />}
          Run scrape now
        </Button>
      </div>

      <div className="rounded-lg border p-4 space-y-3">
        <div>
          <h3 className="font-medium">Manual evaluate</h3>
          <p className="text-sm text-muted-foreground mt-1">
            Enqueue AI scoring for all jobs that haven't been evaluated yet.
          </p>
        </div>
        <Button onClick={triggerEvaluate} disabled={evaluating} variant="outline">
          {evaluating ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Brain className="h-4 w-4 mr-2" />}
          Evaluate unscored jobs
        </Button>
      </div>
    </div>
  );
}

export function AdminPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Admin</h1>
      <Tabs defaultValue="pending">
        <TabsList>
          <TabsTrigger value="pending">Pending approval</TabsTrigger>
          <TabsTrigger value="approved">Approved users</TabsTrigger>
          <TabsTrigger value="system">System</TabsTrigger>
        </TabsList>
        <TabsContent value="pending" className="mt-6">
          <UserTable approved={false} />
        </TabsContent>
        <TabsContent value="approved" className="mt-6">
          <UserTable approved={true} />
        </TabsContent>
        <TabsContent value="system" className="mt-6">
          <SystemTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
