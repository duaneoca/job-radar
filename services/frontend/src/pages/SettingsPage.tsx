import { useEffect, useRef, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, BookmarkIcon, BookOpen, Chrome, Eye, EyeOff, Loader2, MessageSquare, Trash2, Copy, Plus } from "lucide-react";
import { GoogleButton } from "../components/GoogleButton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Separator } from "../components/ui/separator";
import { Badge } from "../components/ui/badge";
import { Switch } from "../components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { keysApi, authApi, agentApi } from "../lib/api";
import { useAuthStore } from "../store/auth";
import { toast } from "../hooks/useToast";
import { useConfirmLinks } from "../hooks/useConfirmLinks";
import { formatDate } from "../lib/utils";
import { AgentStatsView } from "../components/AgentStatsView";
import type { APIKey, LLMProvider, AgentApiKey, AgentApiKeyCreated, AgentFolderConfig, EmailCredentialStatus, MailboxFolders, SlackStatus, SlackChannel } from "../lib/types";

// ─── Account Details tab ──────────────────────────────────────────────────────

function AccountTab() {
  const { user, setUser } = useAuthStore();
  const [searchParams] = useSearchParams();
  const forced = searchParams.get("force") === "1";
  const [confirmLinks, setConfirmLinksPref] = useConfirmLinks();

  const [nameVal, setNameVal] = useState(user?.full_name ?? "");
  const [savingName, setSavingName] = useState(false);

  async function handleSaveName(e: React.FormEvent) {
    e.preventDefault();
    setSavingName(true);
    try {
      const res = await authApi.patch("/auth/me", { full_name: nameVal });
      setUser(res.data);
      toast({ title: "Name updated" });
    } catch (err: any) {
      toast({ title: "Failed to update name", description: err?.response?.data?.detail, variant: "destructive" });
    } finally {
      setSavingName(false);
    }
  }

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [savingPw, setSavingPw] = useState(false);

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault();
    if (newPassword !== confirmPassword) {
      toast({ title: "Passwords don't match", variant: "destructive" });
      return;
    }
    setSavingPw(true);
    try {
      await authApi.post("/auth/change-password", { current_password: currentPassword, new_password: newPassword });
      const res = await authApi.get("/auth/me");
      setUser(res.data);
      setCurrentPassword(""); setNewPassword(""); setConfirmPassword("");
      toast({ title: "Password changed successfully" });
    } catch (err: any) {
      toast({ title: "Failed to change password", description: err?.response?.data?.detail ?? "Unknown error", variant: "destructive" });
    } finally {
      setSavingPw(false);
    }
  }

  return (
    <div className="max-w-md space-y-6">
      {forced && (
        <div className="p-3 rounded-lg bg-yellow-50 dark:bg-yellow-950 border border-yellow-200 dark:border-yellow-800 text-sm text-yellow-800 dark:text-yellow-200">
          Please change your temporary password before continuing.
        </div>
      )}

      <div className="flex items-center justify-between gap-4 rounded-lg border px-4 py-3">
        <div>
          <p className="text-sm font-medium">Confirm before opening links</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            Show the full URL and ask for confirmation before opening a link from an email in a new
            tab. Recommended — email links can be unsafe.
          </p>
        </div>
        <Switch checked={confirmLinks} onCheckedChange={setConfirmLinksPref} aria-label="Confirm before opening links" />
      </div>

      <form onSubmit={handleSaveName} className="space-y-4">
        <h3 className="font-medium">Account details</h3>
        <div className="space-y-1.5">
          <Label htmlFor="email-ro">Email</Label>
          <Input id="email-ro" value={user?.email ?? ""} disabled className="opacity-60" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="fullname">Display name</Label>
          <Input
            id="fullname"
            value={nameVal}
            onChange={(e) => setNameVal(e.target.value)}
            placeholder="Your name"
          />
        </div>
        <Button type="submit" disabled={savingName || nameVal === (user?.full_name ?? "")}>
          {savingName ? "Saving…" : "Save name"}
        </Button>
      </form>

      <Separator />

      <form onSubmit={handleChangePassword} className="space-y-4">
        <h3 className="font-medium">Change password</h3>
        <div className="space-y-1.5">
          <Label htmlFor="curpw">Current password</Label>
          <Input id="curpw" type="password" required value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="newpw">New password</Label>
          <Input id="newpw" type="password" required minLength={8} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="confirmpw">Confirm new password</Label>
          <Input
            id="confirmpw" type="password" required minLength={8}
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className={confirmPassword && confirmPassword !== newPassword ? "border-destructive" : ""}
          />
          {confirmPassword && confirmPassword !== newPassword && (
            <p className="text-xs text-destructive">Passwords don't match</p>
          )}
        </div>
        <Button type="submit" disabled={savingPw}>
          {savingPw ? "Updating…" : "Update password"}
        </Button>
      </form>
    </div>
  );
}

// ─── API Keys tab ─────────────────────────────────────────────────────────────

const AI_PROVIDERS: { value: LLMProvider; label: string; description: string; placeholder: string }[] = [
  { value: "anthropic", label: "Anthropic", description: "Claude models",         placeholder: "sk-ant-…" },
  { value: "openai",    label: "OpenAI",    description: "GPT models",            placeholder: "sk-…" },
  { value: "google",    label: "Google",    description: "Gemini models",         placeholder: "AIza…" },
  { value: "groq",      label: "Groq",      description: "Fast open-source LLMs", placeholder: "gsk_…" },
];

const PREFERRED_PROVIDER_KEY = "jobradar-ai-provider";

interface ProviderModel { id: string; label: string; descriptor?: string | null; }

function ModelSelector({ provider, existing, onSave }: {
  provider: LLMProvider;
  existing: APIKey;
  onSave: (model: string) => Promise<void>;
}) {
  const { data: modelList, isLoading, isError } = useQuery<ProviderModel[]>({
    queryKey: ["models", provider],
    queryFn: () => keysApi.get(`/keys/${provider}/models`).then(r => r.data),
    staleTime: 5 * 60 * 1000,   // 5 min — don't re-fetch on every expand
    retry: 1,
  });

  const currentModel = existing.preferred_model ?? "";
  const inList = modelList?.some(m => m.id === currentModel);
  const stale = currentModel && modelList && !inList;

  if (isLoading) return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground mt-2">
      <Loader2 className="h-3 w-3 animate-spin" /> Fetching available models…
    </div>
  );

  if (isError) return (
    <p className="text-xs text-destructive mt-2">Could not load model list. Check your API key.</p>
  );

  if (!modelList?.length) return null;

  return (
    <div className="space-y-1.5 mt-2">
      <label className="text-xs font-medium text-muted-foreground">Model</label>
      {stale && (
        <div className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span>
            <span className="font-mono">{currentModel}</span> is not in the current model list — it may have been retired. Please select a new model.
          </span>
        </div>
      )}
      <select
        className="w-full rounded-md border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        value={inList ? currentModel : ""}
        onChange={(e) => { if (e.target.value) onSave(e.target.value); }}
      >
        {!inList && <option value="">— choose a model —</option>}
        {modelList.map(({ id, label, descriptor }) => (
          <option key={id} value={id}>
            {label}{descriptor ? ` — ${descriptor}` : ""}
          </option>
        ))}
      </select>
    </div>
  );
}

function KeyInput({
  provider, placeholder, existing, onSave, onDelete,
}: {
  provider: string;
  placeholder: string;
  existing?: APIKey;
  onSave: (key: string) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [val, setVal] = useState("");
  const [show, setShow] = useState(false);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!val) return;
    setSaving(true);
    try {
      await onSave(val);
      setVal("");
    } catch {
      // onSave shows its own toast
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-2 space-y-2">
      {existing && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">
            Saved key: <span className="font-mono">••••{existing.key_hint}</span>
          </span>
          <Button variant="ghost" size="sm" className="h-7 text-destructive hover:bg-destructive/10 px-2" onClick={onDelete}>
            <Trash2 className="h-3.5 w-3.5 mr-1" />
            Remove
          </Button>
        </div>
      )}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Input
            type={show ? "text" : "password"}
            placeholder={existing ? `Replace (${placeholder})` : placeholder}
            value={val}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSave()}
            className="pr-9"
          />
          <button
            type="button"
            className="absolute right-2 top-2.5 text-muted-foreground hover:text-foreground"
            onClick={() => setShow((s) => !s)}
          >
            {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
        <Button size="sm" disabled={!val || saving} onClick={handleSave}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
        </Button>
      </div>
    </div>
  );
}

function AdzunaKeyInput({
  existing, onSave, onDelete,
}: {
  existing?: APIKey;
  onSave: (appId: string, appKey: string) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [appId, setAppId] = useState("");
  const [appKey, setAppKey] = useState("");
  const [show, setShow] = useState(false);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!appId || !appKey) return;
    setSaving(true);
    try {
      await onSave(appId, appKey);
      setAppId("");
      setAppKey("");
    } catch {
      // onSave shows its own toast
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-2 space-y-2">
      {existing && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">
            Saved key: <span className="font-mono">••••{existing.key_hint}</span>
          </span>
          <Button variant="ghost" size="sm" className="h-7 text-destructive hover:bg-destructive/10 px-2" onClick={onDelete}>
            <Trash2 className="h-3.5 w-3.5 mr-1" />
            Remove
          </Button>
        </div>
      )}
      <Input
        placeholder={existing ? "Replace App ID" : "App ID"}
        value={appId}
        onChange={(e) => setAppId(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleSave()}
      />
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Input
            type={show ? "text" : "password"}
            placeholder={existing ? "Replace App Key" : "App Key"}
            value={appKey}
            onChange={(e) => setAppKey(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSave()}
            className="pr-9"
          />
          <button
            type="button"
            className="absolute right-2 top-2.5 text-muted-foreground hover:text-foreground"
            onClick={() => setShow((s) => !s)}
          >
            {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
        <Button size="sm" disabled={!appId || !appKey || saving} onClick={handleSave}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
        </Button>
      </div>
    </div>
  );
}

function KeysTab() {
  const qc = useQueryClient();
  const { data: keys = [] } = useQuery<APIKey[]>({
    queryKey: ["keys"],
    queryFn: () => keysApi.get("/keys").then((r) => r.data),
  });

  const [preferred, setPreferred] = useState<LLMProvider>(
    () => (localStorage.getItem(PREFERRED_PROVIDER_KEY) as LLMProvider) ?? "anthropic"
  );

  const keyMap: Record<string, APIKey> = Object.fromEntries(keys.map((k) => [k.provider, k]));

  async function saveKey(provider: LLMProvider, key: string, preferred_model?: string) {
    try {
      await keysApi.put("/keys", { provider, api_key: key, preferred_model });
      qc.invalidateQueries({ queryKey: ["keys"] });
      toast({ title: "Key saved" });
    } catch (err: any) {
      toast({ title: "Failed to save key", description: err?.response?.data?.detail, variant: "destructive" });
      throw err;
    }
  }

  async function saveModel(provider: LLMProvider, preferred_model: string) {
    try {
      await keysApi.patch(`/keys/${provider}`, { preferred_model });
      qc.invalidateQueries({ queryKey: ["keys"] });
      toast({ title: "Model preference saved" });
    } catch (err: any) {
      toast({ title: "Failed to save model", description: err?.response?.data?.detail, variant: "destructive" });
    }
  }

  async function saveAdzuna(app_id: string, app_key: string) {
    try {
      await keysApi.put("/keys", { provider: "adzuna", app_id, app_key });
      qc.invalidateQueries({ queryKey: ["keys"] });
      toast({ title: "Adzuna credentials saved" });
    } catch (err: any) {
      toast({ title: "Failed to save key", description: err?.response?.data?.detail, variant: "destructive" });
      throw err;
    }
  }

  async function deleteKey(provider: LLMProvider) {
    try {
      await keysApi.delete(`/keys/${provider}`);
      qc.invalidateQueries({ queryKey: ["keys"] });
      toast({ title: "Key removed" });
    } catch (err: any) {
      toast({ title: "Failed to remove key", description: err?.response?.data?.detail, variant: "destructive" });
    }
  }

  async function setActive(provider: LLMProvider) {
    try {
      await keysApi.put("/keys/active", { provider });
      qc.invalidateQueries({ queryKey: ["keys"] });
      toast({ title: "Active model updated" });
    } catch (err: any) {
      toast({ title: "Couldn't set active model", description: err?.response?.data?.detail, variant: "destructive" });
    }
  }

  function pickProvider(p: LLMProvider) {
    setPreferred(p);
    localStorage.setItem(PREFERRED_PROVIDER_KEY, p);
  }

  const tavilyKey = keyMap["tavily"];
  const adzunaKey = keyMap["adzuna"];

  return (
    <div className="max-w-lg space-y-6">
      <p className="text-sm text-muted-foreground">
        Keys are encrypted at rest. Only the last 4 characters are shown after saving.
      </p>

      <div className="space-y-3">
        <div>
          <h3 className="font-medium">Job source — Adzuna</h3>
          <p className="text-sm text-muted-foreground mt-0.5">
            Your own Adzuna API credentials search job boards against your criteria.
            Without them you only get jobs from the public sources. Register a free app at{" "}
            <a href="https://developer.adzuna.com/" target="_blank" rel="noreferrer" className="underline">
              developer.adzuna.com
            </a>{" "}
            to get an App ID and App Key.
          </p>
        </div>
        <AdzunaKeyInput
          existing={adzunaKey}
          onSave={(id, key) => saveAdzuna(id, key)}
          onDelete={() => deleteKey("adzuna")}
        />
      </div>

      <Separator />

      <div className="space-y-3">
        <div>
          <h3 className="font-medium">AI model provider</h3>
          <p className="text-sm text-muted-foreground mt-0.5">
            The <span className="font-medium">active</span> key is used by job scoring, research,
            and the email agent. Click the dot to switch. If you don't pick one, it falls back to
            Anthropic → OpenAI → Google → Groq. Click a row to add or edit its key.
          </p>
        </div>
        <div className="space-y-2">
          {AI_PROVIDERS.map(({ value, label, description, placeholder }) => {
            const isExpanded = preferred === value;
            const existing = keyMap[value];
            const isActive = existing?.active ?? false;
            return (
              <div
                key={value}
                className={`rounded-lg border p-3 cursor-pointer transition-colors ${
                  isActive ? "border-primary bg-primary/5"
                  : isExpanded ? "border-muted-foreground/50"
                  : "hover:border-muted-foreground/50 hover:bg-muted/30"
                }`}
                onClick={() => pickProvider(value)}
              >
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    aria-label={isActive ? `${label} is the active model` : `Set ${label} as active`}
                    title={existing ? "Use this key for scoring, research, and the email agent" : "Add a key first"}
                    disabled={!existing}
                    onClick={(e) => { e.stopPropagation(); if (existing) setActive(value); }}
                    className={`h-4 w-4 rounded-full border-2 shrink-0 flex items-center justify-center ${
                      isActive ? "border-primary" : "border-muted-foreground/40"
                    } ${existing ? "cursor-pointer" : "cursor-not-allowed opacity-40"}`}
                  >
                    {isActive && <div className="h-2 w-2 rounded-full bg-primary" />}
                  </button>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{label}</span>
                      <span className="text-xs text-muted-foreground">{description}</span>
                      {isActive && <Badge className="text-xs">Active</Badge>}
                      {existing && (
                        <Badge variant="outline" className="font-mono text-xs ml-auto">
                          ••••{existing.key_hint}
                        </Badge>
                      )}
                    </div>
                  </div>
                </div>
                {isExpanded && (
                  <div onClick={(e) => e.stopPropagation()}>
                    <KeyInput
                      provider={value} placeholder={placeholder} existing={existing}
                      onSave={(k) => saveKey(value, k)}
                      onDelete={() => deleteKey(value)}
                    />
                    {existing && (
                      <ModelSelector
                        provider={value}
                        existing={existing}
                        onSave={(m) => saveModel(value, m)}
                      />
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <Separator />

      <div className="space-y-3">
        <div>
          <h3 className="font-medium">Web search</h3>
          <p className="text-sm text-muted-foreground mt-0.5">
            Tavily gives the AI up-to-date info about companies and roles. Optional but recommended.
          </p>
        </div>
        <KeyInput
          provider="tavily" placeholder="tvly-…" existing={tavilyKey}
          onSave={(k) => saveKey("tavily", k)}
          onDelete={() => deleteKey("tavily")}
        />
      </div>
    </div>
  );
}

// ─── Bookmarklet tab ──────────────────────────────────────────────────────────

function buildBookmarklet(appOrigin: string): string {
  const js = `(function(){
function tc(s){try{var e=document.querySelector(s);return e?(e.textContent||'').replace(/\\s+/g,' ').trim():''}catch(x){return '';}}
function longest(ss){return ss.reduce(function(a,s){var t=tc(s);return t.length>a.length?t:a;},'');}
var host=window.location.hostname;
var ur=window.location.href.split('?')[0];
var data=null;
if(host.includes('linkedin.com')){
if(!ur.includes('/jobs/view/')){alert('Job Radar: Please open the specific job posting first.\\nClick the job title to open it on its own page (URL must contain /jobs/view/).\\nSearch results and job list pages are not supported.');return;}
var raw=document.title.replace(/^\\(\\d+\\)\\s*/,'');
var main=raw.split(' | ')[0].trim();
var atIdx=main.lastIndexOf(' at ');
var ti=atIdx>-1?main.substring(0,atIdx).trim():main;
var co='';
var ariaEl=document.querySelector('[aria-label^="Company,"]');
if(ariaEl)co=ariaEl.getAttribute('aria-label').replace(/^Company,\\s*/,'').replace(/\\.$/,'').trim();
if(!co){var cl=document.querySelector('a[href*="/company/"]');if(cl)co=(cl.textContent||'').trim();}
if(!co&&atIdx>-1)co=main.substring(atIdx+4).trim();
var lo='';
var allP=document.querySelectorAll('p');
for(var i=0;i<allP.length;i++){var pt=(allP[i].textContent||'').replace(/\\s+/g,' ').trim();if(pt.includes('\\u00b7')&&pt.length<400){var sp=allP[i].querySelector('span');if(sp&&sp.textContent&&sp.textContent.trim().length>1){lo=sp.textContent.trim();break;}}}
var de=longest(['[data-testid="expandable-text-box"]','[class*="show-more-less-html"]','#job-details','.jobs-description__content','article']);
var parts=ur.split('/view/');var id=parts.length>1?parts[1].replace(/[^0-9]/g,''):'';
var re=/remote/i.test(lo)||/remote/i.test(de.substring(0,300));
var salMin=null,salMax=null;
var salText='';
var liInsights=document.querySelectorAll('[class*="job-insight"],[class*="salary"],[class*="compensation"]');
for(var si=0;si<liInsights.length;si++){var st=(liInsights[si].textContent||'').replace(/\\s+/g,' ').trim();if(st.indexOf('$')>-1&&st.length<300){salText=st;break;}}
if(!salText){var topCard=document.querySelector('[class*="jobs-unified-top-card"],[class*="job-details-jobs-unified-top-card"]');if(topCard){var tcSpans=topCard.querySelectorAll('span,li');for(var si=0;si<tcSpans.length;si++){var st=(tcSpans[si].textContent||'').trim();if(st.indexOf('$')>-1&&st.length<200){salText=st;break;}}}}
var salRx=salText.match(/\\$([0-9,]+(?:\\.[0-9]+)?)(K?)\\s*[-\\u2013\\u2014]\\s*\\$([0-9,]+(?:\\.[0-9]+)?)(K?)/i);
if(!salRx&&de){salRx=de.match(/\\$([0-9,]+(?:\\.[0-9]+)?)(K?)\\s*[-\\u2013\\u2014]\\s*\\$([0-9,]+(?:\\.[0-9]+)?)(K?)/i);}
if(salRx){salMin=Math.round(parseFloat(salRx[1].replace(/,/g,''))*(salRx[2].toUpperCase()==='K'?1000:1));salMax=Math.round(parseFloat(salRx[3].replace(/,/g,''))*(salRx[4].toUpperCase()==='K'?1000:1));}
if(!ti){alert('Job Radar: Could not read this page.\\nNavigate to a specific LinkedIn job posting.');return;}
data={title:ti,company:co,location:lo,description:de,url:ur,external_id:id,remote:re,source:'linkedin',salary_min:salMin,salary_max:salMax};
}else if(host.includes('dice.com')){
var ti=tc('[data-testid="job-detail-header-card"] h1')||tc('h1');
var co=tc('a[href*="/company-profile/"]');
var lo='';
var loSpans=document.querySelectorAll('[data-testid="job-detail-header-card"] span span');
for(var i=0;i<loSpans.length;i++){var st=(loSpans[i].textContent||'').trim();if(st.length>3&&st.indexOf('\\u2022')<0&&st.indexOf('Posted')<0&&st.indexOf('Updated')<0){lo=st;break;}}
var de=tc('[class*="jobDescription"]')||longest(['#job-description','article']);
var hdText=(document.querySelector('[data-testid="job-detail-header-card"]')||{textContent:''}).textContent;
var salMatch=hdText.match(/\\$([\\d,]+(?:\\.[\\d]+)?)(K?)\\s*[-\\u2013\\u2014]\\s*\\$([\\d,]+(?:\\.[\\d]+)?)(K?)/i);
var salMin=salMatch?Math.round(parseFloat(salMatch[1].replace(/,/g,''))*(salMatch[2].toUpperCase()==='K'?1000:1)):null;
var salMax=salMatch?Math.round(parseFloat(salMatch[3].replace(/,/g,''))*(salMatch[4].toUpperCase()==='K'?1000:1)):null;
var dparts=ur.split('/job-detail/');var id=dparts.length>1?dparts[1].split('/')[0]:'';
var re=/remote/i.test(lo)||/remote/i.test(de.substring(0,300));
if(!ti){alert('Job Radar: Could not read this Dice page.\\nNavigate to a specific job posting.');return;}
data={title:ti,company:co,location:lo,description:de,url:ur,external_id:id,remote:re,source:'dice',salary_min:salMin,salary_max:salMax};
}else if(host.includes('builtin.com')){
var id=new URLSearchParams(window.location.search).get('jobid')||'';
var jmi=id?document.getElementById(id):document.querySelector('.job-match-item');
if(!jmi){alert('Job Radar: Could not find the current job.\\nNavigate to a specific BuiltIn job posting.');return;}
var tiEl=jmi.querySelector('h3 span')||jmi.querySelector('h3');
var ti=tiEl?(tiEl.textContent||'').replace(/\\s+/g,' ').trim():'';
var coEl=jmi.querySelector('a[href^="/company/"] h2')||jmi.querySelector('a[href^="/company/"]');
var co=coEl?(coEl.textContent||'').replace(/\\s+/g,' ').trim():'';
var loSpan=jmi.querySelector('[data-bs-original-title]');
var loHtml=loSpan?loSpan.getAttribute('data-bs-original-title'):'';
var loRx=(loHtml||'').match(/>([^<]+)</g)||[];
var lo=loRx.map(function(m){return m.slice(1,-1).trim();}).filter(Boolean).join(', ');
if(!lo&&loSpan){lo=(loSpan.textContent||'').trim();}
var deEl=jmi.querySelector('.html-parsed-content')||jmi.querySelector('[id^="match-body-"]');
var de=deEl?(deEl.textContent||'').replace(/\\s+/g,' ').trim():'';
var jmiText=(jmi.textContent||'');
var salMatch=jmiText.match(/\\$([\\d,]+(?:\\.[\\d]+)?)(K?)\\s*[-\\u2013\\u2014]\\s*\\$([\\d,]+(?:\\.[\\d]+)?)(K?)/i);
var salMin=salMatch?Math.round(parseFloat(salMatch[1].replace(/,/g,''))*(salMatch[2].toUpperCase()==='K'?1000:1)):null;
var salMax=salMatch?Math.round(parseFloat(salMatch[3].replace(/,/g,''))*(salMatch[4].toUpperCase()==='K'?1000:1)):null;
var re=/remote/i.test(jmiText);
var buUrl=window.location.href;
if(!ti){alert('Job Radar: Could not read this BuiltIn page.\\nNavigate to a specific job posting.');return;}
data={title:ti,company:co,location:lo,description:de,url:buUrl,external_id:id,remote:re,source:'builtin',salary_min:salMin,salary_max:salMax};
}else if(host.includes('monster.com')){
var ti=tc('[data-testid="jobTitle"]');
var co=tc('[data-testid="company"]');
var lo=tc('#job-view-header [data-testid="jobDetailLocation"]')||tc('[data-testid="jobDetailLocation"]');
var de=tc('[data-testid="svx-description-container-inner"]');
var salText=tc('[data-testid="svx-jobview-salary-value"]');
var salMatch=salText.match(/\\$([\\d,]+(?:\\.[\\d]+)?)(K?)\\s*[-\\u2013\\u2014]\\s*\\$([\\d,]+(?:\\.[\\d]+)?)(K?)/i);
var salMin=salMatch?Math.round(parseFloat(salMatch[1].replace(/,/g,''))*(salMatch[2].toUpperCase()==='K'?1000:1)):null;
var salMax=salMatch?Math.round(parseFloat(salMatch[3].replace(/,/g,''))*(salMatch[4].toUpperCase()==='K'?1000:1)):null;
var idMatch=ur.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
var id=idMatch?idMatch[0]:'';
var re=/remote/i.test(de);
if(!ti){alert('Job Radar: Could not read this Monster page.\\nNavigate to a specific job posting.');return;}
data={title:ti,company:co,location:lo,description:de,url:ur,external_id:id,remote:re,source:'monster',salary_min:salMin,salary_max:salMax};
}else if(host.includes('ziprecruiter.com')){
var id=new URLSearchParams(window.location.search).get('lk')||'';
var card=id?document.getElementById('job-card-'+id):null;
if(!card){alert('Job Radar: Could not find the current job.\\nOpen a specific job on ZipRecruiter first.');return;}
var tiEl=card.querySelector('h2[aria-label]');
var ti=tiEl?tiEl.getAttribute('aria-label').replace(/\\s+/g,' ').trim():'';
var coEl=card.querySelector('[data-testid="job-card-company"]');
var co=coEl?(coEl.textContent||'').trim():'';
var loEl=card.querySelector('[data-testid="job-card-location"]');
var lo=loEl?(loEl.textContent||'').trim():'';
var salText='';
var pEls=card.querySelectorAll('p');
for(var k=0;k<pEls.length;k++){if((pEls[k].textContent||'').indexOf('$')>-1){salText=(pEls[k].textContent||'').trim();break;}}
var salMin=null,salMax=null;
var srm=salText.match(/\\$([\\d.]+)(K?)\\s*[-\\u2013\\u2014]\\s*\\$([\\d.]+)(K?)/i);
if(srm){salMin=Math.round(parseFloat(srm[1])*(srm[2].toUpperCase()==='K'?1000:1));salMax=Math.round(parseFloat(srm[3])*(srm[4].toUpperCase()==='K'?1000:1));}
else{var ssm=salText.match(/\\$([\\d.]+)(K?)/i);if(ssm){salMin=Math.round(parseFloat(ssm[1])*(ssm[2].toUpperCase()==='K'?1000:1));}}
var re=/remote/i.test(lo)||/remote/i.test(card.textContent||'');
var zrUrl='https://www.ziprecruiter.com/jobseeker/home?lk='+id;
if(!ti){alert('Job Radar: Could not read this ZipRecruiter page.\\nNavigate to a specific job posting.');return;}
data={title:ti,company:co,location:lo,description:'',url:zrUrl,external_id:id,remote:re,source:'ziprecruiter',salary_min:salMin,salary_max:salMax};
}else if(host.includes('indeed.com')){
var id=new URLSearchParams(window.location.search).get('vjk')||new URLSearchParams(window.location.search).get('jk')||'';
if(!id){alert('Job Radar: Could not find a job ID.\\nNavigate to a specific Indeed job posting.');return;}
var tiEl=document.querySelector('[data-testid="jobsearch-JobInfoHeader-title"]');
var ti=tiEl?(tiEl.textContent||'').replace(/\\s*-\\s*job post\\s*$/i,'').trim():'';
var coLinkEl=document.querySelector('[data-company-name="true"] a');
var co=coLinkEl?(coLinkEl.getAttribute('aria-label')||'').replace(/\\s*\\(opens in a new tab\\)\\s*/i,'').trim():'';
if(!co&&coLinkEl){co=(coLinkEl.textContent||'').replace(/\\s+/g,' ').trim();}
var loEl=document.querySelector('[data-testid="inlineHeader-companyLocation"]');
var lo=loEl?(loEl.textContent||'').replace(/[\\u2022\\u00b7]/g,' · ').replace(/\\s+/g,' ').trim():'';
var de=tc('#jobDescriptionText');
var hdEl=document.querySelector('.jobsearch-HeaderContainer');
var hdText=hdEl?(hdEl.textContent||''):'';
var salMatch=hdText.match(/\\$([\\d,]+(?:\\.[\\d]+)?)(K?)\\s*[-\\u2013\\u2014]\\s*\\$([\\d,]+(?:\\.[\\d]+)?)(K?)/i);
var salMin=salMatch?Math.round(parseFloat(salMatch[1].replace(/,/g,''))*(salMatch[2].toUpperCase()==='K'?1000:1)):null;
var salMax=salMatch?Math.round(parseFloat(salMatch[3].replace(/,/g,''))*(salMatch[4].toUpperCase()==='K'?1000:1)):null;
var re=/remote/i.test(lo)||/remote/i.test(de.substring(0,500));
var indeedUrl='https://www.indeed.com/viewjob?jk='+id;
if(!ti){alert('Job Radar: Could not read this Indeed page.\\nNavigate to a specific job posting.');return;}
data={title:ti,company:co,location:lo,description:de,url:indeedUrl,external_id:id,remote:re,source:'indeed',salary_min:salMin,salary_max:salMax};
}else{
alert('Job Radar: This site is not yet supported.\\nSupported: LinkedIn, Dice, BuiltIn, Monster, ZipRecruiter, Indeed.');
return;
}
window.open('${appOrigin}/jobs/add#'+btoa(unescape(encodeURIComponent(JSON.stringify(data)))),'_blank');
})();`;
  return "javascript:" + js.replace(/\n/g, "");
}

function buildDebugBookmarklet(): string {
  const js = `(function(){
function tc(s){try{var e=document.querySelector(s);return e?(e.textContent||'').replace(/\\s+/g,' ').trim().substring(0,80):'(none)'}catch(x){return '(err)';}}
function len(s){try{var e=document.querySelector(s);return e?(e.textContent||'').trim().length:'(none)'}catch(x){return '(err)';}}
var ariaEl=document.querySelector('[aria-label^="Company,"]');
var co=ariaEl?ariaEl.getAttribute('aria-label'):'(none)';
var cl=document.querySelector('a[href*="/company/"]');
var coLink=cl?(cl.textContent||'').trim():'(none)';
var lo='(none)';
var allP=document.querySelectorAll('p');
for(var i=0;i<allP.length;i++){var pt=(allP[i].textContent||'').replace(/\\s+/g,' ').trim();if(pt.includes('\\u00b7')&&pt.length<400){var sp=allP[i].querySelector('span');if(sp&&sp.textContent&&sp.textContent.trim().length>1){lo=sp.textContent.trim();break;}}}
var info=[
  'TITLE: '+document.title.substring(0,80),
  'COMPANY aria-label: '+co,
  'COMPANY link: '+coLink,
  'LOCATION (first \\u00b7-para span): '+lo,
  'DESC [data-testid=expandable-text-box]: '+len('[data-testid="expandable-text-box"]'),
  'DESC #job-details: '+len('#job-details'),
  'DESC article: '+len('article'),
].join('\\n');
alert(info);
})();`;
  return "javascript:" + js.replace(/\n/g, "");
}

function BookmarkletTab() {
  const appOrigin = window.location.origin;
  const bookmarkletUrl = buildBookmarklet(appOrigin);
  const debugUrl = buildDebugBookmarklet();
  const linkRef = useRef<HTMLAnchorElement>(null);
  const debugRef = useRef<HTMLAnchorElement>(null);

  // Set href via ref to avoid React's javascript: href sanitization warning
  useEffect(() => {
    if (linkRef.current) linkRef.current.setAttribute("href", bookmarkletUrl);
    if (debugRef.current) debugRef.current.setAttribute("href", debugUrl);
  }, [bookmarkletUrl, debugUrl]);

  return (
    <div className="max-w-lg space-y-6">
      <div className="space-y-2">
        <h3 className="font-medium">Job Radar Bookmarklet</h3>
        <p className="text-sm text-muted-foreground">
          Capture a job posting in one click while browsing a supported job site. Drag the
          button below to your browser's bookmarks bar, then click it on any supported job
          page to add it to Job Radar instantly.
        </p>
        <p className="text-sm text-muted-foreground">
          <strong>Supported:</strong> LinkedIn, Dice, BuiltIn, Monster, ZipRecruiter, Indeed
        </p>
      </div>

      {/* Draggable bookmarklet link */}
      <div className="flex flex-col items-center gap-3 p-6 rounded-xl border-2 border-dashed border-primary/40 bg-primary/5">
        <p className="text-xs text-muted-foreground text-center">
          Drag this button to your bookmarks bar
        </p>
        {/* eslint-disable-next-line jsx-a11y/anchor-is-valid */}
        <a
          ref={linkRef}
          href="#"
          draggable
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-primary text-primary-foreground font-semibold text-sm shadow hover:opacity-90 transition-opacity cursor-grab active:cursor-grabbing select-none"
          onClick={(e) => {
            // Allow drag; prevent accidental navigation on click
            e.preventDefault();
            alert("Drag this button to your bookmarks bar, then click it on any supported job page (LinkedIn, Dice).");
          }}
        >
          <BookmarkIcon className="h-4 w-4" />
          📡 Add to Job Radar
        </a>
        <p className="text-xs text-muted-foreground text-center">
          ↑ Drag me to your bookmarks bar
        </p>
      </div>

      <Separator />

      {/* Installation instructions */}
      <div className="space-y-4">
        <h3 className="font-medium text-sm">Installation instructions</h3>

        <div className="space-y-3 text-sm">
          <div className="space-y-1">
            <p className="font-medium flex items-center gap-1.5">
              <Chrome className="h-3.5 w-3.5" />
              Chrome / Chromium / Edge / Brave
            </p>
            <ol className="list-decimal list-inside space-y-0.5 text-muted-foreground text-xs leading-relaxed ml-5">
              <li>Make sure the bookmarks bar is visible — <kbd className="bg-muted px-1 rounded text-foreground">Ctrl/Cmd + Shift + B</kbd></li>
              <li>Drag the "Add to Job Radar" button to the bookmarks bar</li>
              <li>Done! Navigate to any LinkedIn job and click the bookmark</li>
            </ol>
          </div>

          <div className="space-y-1">
            <p className="font-medium text-sm">Firefox</p>
            <ol className="list-decimal list-inside space-y-0.5 text-muted-foreground text-xs leading-relaxed ml-5">
              <li>Show the bookmarks toolbar — <kbd className="bg-muted px-1 rounded text-foreground">Ctrl/Cmd + Shift + B</kbd></li>
              <li>Drag the "Add to Job Radar" button to the toolbar</li>
              <li>Done!</li>
            </ol>
          </div>

          <div className="space-y-1">
            <p className="font-medium text-sm">Safari</p>
            <ol className="list-decimal list-inside space-y-0.5 text-muted-foreground text-xs leading-relaxed ml-5">
              <li>Show the bookmarks bar — <kbd className="bg-muted px-1 rounded text-foreground">⌘ Shift B</kbd></li>
              <li>Drag the button to the bar (you may need to enable "Show Favorites Bar" first)</li>
              <li>If drag doesn't work: right-click the button → Bookmark This Link</li>
            </ol>
          </div>
        </div>
      </div>

      <Separator />

      <div className="space-y-2">
        <h3 className="font-medium text-sm">What gets captured</h3>
        <ul className="text-xs text-muted-foreground space-y-1">
          <li>✓ Job title and company name</li>
          <li>✓ Location (and detects Remote automatically)</li>
          <li>✓ Full job description</li>
          <li>✓ LinkedIn job URL and job ID</li>
          <li>✗ Salary (LinkedIn rarely shows it — you can add it on the next screen)</li>
        </ul>
        <p className="text-xs text-muted-foreground mt-2">
          The bookmarklet runs entirely in your browser and never contacts Job Radar directly from
          LinkedIn — it just opens a new tab with the data in the URL.
        </p>
      </div>

      <Separator />

      {/* Debug tool */}
      <div className="space-y-2">
        <h3 className="font-medium text-sm">Troubleshooting</h3>
        <p className="text-xs text-muted-foreground">
          If fields are missing, drag this debug bookmark to your bar and click it on a LinkedIn job page.
          It will show a diagnostic alert with what Job Radar can see in the page DOM — paste the output here so we can fix the selectors.
        </p>
        <a
          ref={debugRef}
          href="#"
          draggable
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded border text-xs text-muted-foreground hover:text-foreground hover:border-foreground/50 transition-colors cursor-grab active:cursor-grabbing select-none"
          onClick={(e) => {
            e.preventDefault();
            alert("Drag this to your bookmarks bar, then click it on a LinkedIn job page to see diagnostic info.");
          }}
        >
          🔍 Debug: LinkedIn DOM
        </a>
      </div>
    </div>
  );
}

// ─── Email Agent tab ──────────────────────────────────────────────────────────

function AgentKeySection() {
  const qc = useQueryClient();
  const { data: keys = [], isLoading } = useQuery<AgentApiKey[]>({
    queryKey: ["agent-keys"],
    queryFn: () => agentApi.get("/agent/keys").then((r) => r.data),
  });
  const [newKey, setNewKey] = useState<AgentApiKeyCreated | null>(null);
  const [generating, setGenerating] = useState(false);

  async function generate() {
    setGenerating(true);
    try {
      const { data } = await agentApi.post("/agent/keys");
      setNewKey(data);
      qc.invalidateQueries({ queryKey: ["agent-keys"] });
    } catch (err: any) {
      toast({ title: "Failed to generate key", description: err?.response?.data?.detail, variant: "destructive" });
    } finally {
      setGenerating(false);
    }
  }

  async function revoke(id: string) {
    try {
      await agentApi.delete(`/agent/keys/${id}`);
      qc.invalidateQueries({ queryKey: ["agent-keys"] });
      toast({ title: "Key revoked" });
    } catch (err: any) {
      toast({ title: "Failed to revoke", description: err?.response?.data?.detail, variant: "destructive" });
    }
  }

  async function copyKey(k: string) {
    try { await navigator.clipboard.writeText(k); toast({ title: "Copied to clipboard" }); }
    catch { /* clipboard may be unavailable */ }
  }

  const active = keys.filter((k) => !k.revoked);

  return (
    <div className="space-y-3">
      <div>
        <h3 className="font-medium">Agent key</h3>
        <p className="text-sm text-muted-foreground mt-0.5">
          The Email Agent authenticates to Job Radar with this key (sent as <code>X-Agent-Key</code>).
          Generate one and paste it into your agent's config. Treat it like a password.
        </p>
      </div>

      {newKey && (
        <div className="rounded-lg border border-primary/30 bg-primary/5 p-3 space-y-2">
          <p className="text-sm font-medium">Copy your new key now — it won't be shown again:</p>
          <div className="flex gap-2">
            <code className="flex-1 text-xs font-mono bg-background border rounded px-2 py-1.5 break-all select-all">
              {newKey.raw_key}
            </code>
            <Button size="sm" variant="outline" onClick={() => copyKey(newKey.raw_key)} aria-label="Copy key">
              <Copy className="h-4 w-4" />
            </Button>
          </div>
          <Button size="sm" variant="ghost" onClick={() => setNewKey(null)}>Done</Button>
        </div>
      )}

      {isLoading ? (
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      ) : active.length === 0 ? (
        <p className="text-sm text-muted-foreground">No active keys.</p>
      ) : (
        <div className="space-y-2">
          {active.map((k) => (
            <div key={k.id} className="flex items-center justify-between gap-2 text-sm border rounded-md px-3 py-2">
              <div className="min-w-0">
                <span className="font-mono">••••{k.key_hint}</span>
                <span className="text-xs text-muted-foreground ml-2">
                  created {formatDate(k.created_at)} · {k.last_used_at ? `last used ${formatDate(k.last_used_at)}` : "never used"}
                </span>
              </div>
              <Button
                size="sm" variant="ghost"
                className="h-7 shrink-0 text-destructive hover:bg-destructive/10 px-2"
                onClick={() => revoke(k.id)}
              >
                <Trash2 className="h-3.5 w-3.5 mr-1" /> Revoke
              </Button>
            </div>
          ))}
        </div>
      )}

      <Button size="sm" disabled={generating} onClick={generate}>
        {generating ? <Loader2 className="h-4 w-4 animate-spin" />
                    : <><Plus className="h-4 w-4 mr-1" /> Generate key</>}
      </Button>
    </div>
  );
}

const LABEL_FIELDS: { key: keyof AgentFolderConfig; label: string; hint: string }[] = [
  { key: "root",        label: "Root label",  hint: "parent label, e.g. Hire Duane" },
  { key: "interaction", label: "Interaction", hint: "recruiter replies, scheduling" },
  { key: "postings",    label: "Postings",    hint: "job-posting emails" },
  { key: "social",      label: "Social",      hint: "LinkedIn / network noise" },
  { key: "unprocessed", label: "Unprocessed", hint: "not yet handled" },
];

const EMPTY_FOLDERS: AgentFolderConfig = {
  root: "", interaction: "", postings: "", social: "", unprocessed: "",
};

const PROVIDERS = [
  { value: "proton", label: "Proton" },
  { value: "gmail",  label: "Gmail" },
  { value: "other",  label: "Other" },
] as const;
type ProviderKey = (typeof PROVIDERS)[number]["value"];

function ProviderSelector({ value, onChange }: { value: ProviderKey; onChange: (v: ProviderKey) => void }) {
  return (
    <div className="inline-flex rounded-lg border bg-muted/30 p-1">
      {PROVIDERS.map((p) => (
        <button
          key={p.value} type="button" onClick={() => onChange(p.value)}
          className={`px-4 py-1.5 text-sm rounded-md transition-colors ${
            value === p.value ? "bg-background shadow-sm font-medium" : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}

// Shared cloud-mailbox config (Gmail + IMAP, once connected): enable toggle
// (saves instantly), labels/folders, and disconnect.
function MailboxConfig({ status, connectedLabel, noun }: {
  status: EmailCredentialStatus;
  connectedLabel: string;
  noun: "label" | "folder";
}) {
  const qc = useQueryClient();
  const [folders, setFolders] = useState<AgentFolderConfig>(EMPTY_FOLDERS);
  const [enabled, setEnabled] = useState(status.enabled);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setFolders({
      root: status.folders.root ?? "", interaction: status.folders.interaction ?? "",
      postings: status.folders.postings ?? "", social: status.folders.social ?? "",
      unprocessed: status.folders.unprocessed ?? "",
    });
    setEnabled(status.enabled);
  }, [status]);

  // Live folder/label list from the server → exact-name picker (avoids the typed-name
  // mismatch where a leaf like "Postings" never matches the server's "Folders/Postings").
  const { data: avail } = useQuery<MailboxFolders>({
    queryKey: ["agent-mailbox-folders", status.provider],
    queryFn: () => agentApi.get("/agent/email-credentials/folders").then((r) => r.data),
    enabled: status.connected,
    retry: false,
    staleTime: 60_000,
  });
  const delim = avail?.delimiter || "/";
  const allFolders = avail?.folders ?? [];
  const usePicker = allFolders.length > 0;   // fall back to free-text if the list can't load
  const SUB_KEYS = ["interaction", "postings", "social", "unprocessed"] as const;

  const root = folders.root ?? "";
  const isChildOfRoot = (n: string) => !!root && n !== root && n.startsWith(root + delim);
  const childrenOfRoot = allFolders.filter(isChildOfRoot);

  // Changing the root invalidates any sub that no longer lives under it.
  function setRootFolder(v: string) {
    setFolders((prev) => {
      const next = { ...prev, root: v };
      for (const k of SUB_KEYS) {
        const val = prev[k];
        if (val && !(val !== v && val.startsWith(v + delim))) next[k] = "";
      }
      return next;
    });
  }

  const norm = (s: string | null) => (!s || s.trim() === "" ? null : s.trim());

  async function persist(nextEnabled: boolean, announce?: string) {
    await agentApi.put("/agent/email-credentials", {
      folders: {
        root: norm(folders.root), interaction: norm(folders.interaction),
        postings: norm(folders.postings), social: norm(folders.social),
        unprocessed: norm(folders.unprocessed),
      },
      enabled: nextEnabled,
    });
    qc.invalidateQueries({ queryKey: ["agent-email-credentials"] });
    if (announce) toast({ title: announce });
  }

  async function toggleEnabled(v: boolean) {
    setEnabled(v);
    try { await persist(v, v ? "Agent enabled" : "Agent paused"); }
    catch (err: any) { setEnabled(!v); toast({ title: "Failed to update", description: err?.response?.data?.detail, variant: "destructive" }); }
  }

  async function saveFolders() {
    setSaving(true);
    try { await persist(enabled, `${noun === "label" ? "Labels" : "Folders"} saved`); }
    catch (err: any) { toast({ title: "Failed to save", description: err?.response?.data?.detail, variant: "destructive" }); }
    finally { setSaving(false); }
  }

  async function disconnect() {
    try {
      await agentApi.delete("/agent/email-credentials");
      qc.invalidateQueries({ queryKey: ["agent-email-credentials"] });
      toast({ title: "Mailbox disconnected" });
    } catch (err: any) {
      toast({ title: "Failed to disconnect", description: err?.response?.data?.detail, variant: "destructive" });
    }
  }

  const nounCap = noun === "label" ? "Labels" : "Folders";
  const foldersComplete = LABEL_FIELDS.every((ff) => (folders[ff.key] ?? "").trim() !== "");

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 border rounded-md px-3 py-2">
        <Badge variant="secondary">{connectedLabel}</Badge>
        <Button
          size="sm" variant="ghost"
          className="h-7 shrink-0 text-destructive hover:bg-destructive/10 px-2"
          onClick={disconnect}
        >
          <Trash2 className="h-3.5 w-3.5 mr-1" /> Disconnect
        </Button>
      </div>

      <div className="flex items-center justify-between border rounded-md px-3 py-2">
        <div>
          <p className="text-sm font-medium">Agent enabled</p>
          <p className="text-xs text-muted-foreground">
            {!enabled && !foldersComplete
              ? `Set all five ${noun}s below to enable. The agent won't run until then.`
              : "Pause to stop the hosted agent processing this mailbox."}
          </p>
        </div>
        <Switch
          checked={enabled}
          onCheckedChange={toggleEnabled}
          disabled={!enabled && !foldersComplete}
          aria-label="Agent enabled"
        />
      </div>

      <div className="space-y-3">
        <div>
          <h4 className="text-sm font-medium">{nounCap}</h4>
          <p className="text-xs text-muted-foreground mt-0.5">
            The agent files mail under these {noun}s. <strong>Create the {noun}s yourself first</strong> — the agent never creates {noun}s.
            {usePicker
              ? <> Pick the <strong>root</strong> {noun} first; the rest must live under it.</>
              : noun === "label" && <> Use the nested name (e.g. <code>Hire Duane/Interaction</code>).</>}
          </p>
        </div>

        {usePicker ? (
          <>
            {/* Root first */}
            <div className="space-y-1">
              <Label htmlFor="folder-root" className="text-xs">{LABEL_FIELDS[0].label}</Label>
              <select
                id="folder-root"
                className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
                value={root}
                onChange={(e) => setRootFolder(e.target.value)}
              >
                <option value="">— select root {noun} —</option>
                {allFolders.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
            {/* Subs — only folders under the chosen root */}
            {LABEL_FIELDS.slice(1).map((f) => {
              const current = folders[f.key] ?? "";
              const opts = current && !childrenOfRoot.includes(current)
                ? [current, ...childrenOfRoot] : childrenOfRoot;   // keep a stale value visible
              return (
                <div key={f.key} className="space-y-1">
                  <Label htmlFor={`folder-${f.key}`} className="text-xs">{f.label}</Label>
                  <select
                    id={`folder-${f.key}`}
                    disabled={!root}
                    className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
                    value={current}
                    onChange={(e) => setFolders((prev) => ({ ...prev, [f.key]: e.target.value }))}
                  >
                    <option value="">{root ? `— select ${f.label} —` : "select a root first"}</option>
                    {opts.map((n) => (
                      <option key={n} value={n}>{n.startsWith(root + delim) ? n.slice(root.length + delim.length) : n}</option>
                    ))}
                  </select>
                </div>
              );
            })}
          </>
        ) : (
          LABEL_FIELDS.map((f) => (
            <div key={f.key} className="space-y-1">
              <Label htmlFor={`folder-${f.key}`} className="text-xs">{f.label}</Label>
              <Input
                id={`folder-${f.key}`}
                value={folders[f.key] ?? ""}
                placeholder={f.hint}
                onChange={(e) => setFolders((prev) => ({ ...prev, [f.key]: e.target.value }))}
              />
            </div>
          ))
        )}
      </div>

      <Button size="sm" disabled={saving} onClick={saveFolders}>
        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : `Save ${noun}s`}
      </Button>
    </div>
  );
}

function useMailboxStatus() {
  return useQuery<EmailCredentialStatus>({
    queryKey: ["agent-email-credentials"],
    queryFn: () => agentApi.get("/agent/email-credentials").then((r) => r.data),
  });
}

function ProtonPanel() {
  return (
    <div className="space-y-6">
      <AgentKeySection />
      <div className="rounded-lg border bg-muted/30 p-3 space-y-2">
        <p className="text-sm text-muted-foreground">
          The Proton agent runs on your own machine (Proton Bridge is local-only) and reads its config
          from a local <code>.env</code> — your mailbox credentials never touch Job Radar.
        </p>
        <Link to="/settings/agent-setup" className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline">
          <BookOpen className="h-4 w-4" /> Local agent setup guide
        </Link>
      </div>
    </div>
  );
}

function GmailPanel() {
  const { data: status, isLoading } = useMailboxStatus();
  const [connecting, setConnecting] = useState(false);

  async function connect() {
    setConnecting(true);
    try {
      const { data } = await agentApi.get("/agent/oauth/start");
      window.location.href = data.authorization_url;
    } catch (err: any) {
      toast({ title: "Couldn't start Gmail connect", description: err?.response?.data?.detail, variant: "destructive" });
      setConnecting(false);
    }
  }

  if (isLoading) return <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />;
  const connected = status?.connected && status.provider === "gmail";

  if (!connected) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Connect your Gmail so Job Radar's hosted agent can read and label it.
        </p>
        <GoogleButton onClick={connect} loading={connecting} />
      </div>
    );
  }
  return <MailboxConfig status={status!} connectedLabel="Gmail connected" noun="label" />;
}

function ImapPanel() {
  const qc = useQueryClient();
  const { data: status, isLoading } = useMailboxStatus();
  const [host, setHost] = useState("");
  const [port, setPort] = useState("993");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [useSsl, setUseSsl] = useState(true);
  const [saving, setSaving] = useState(false);

  async function saveImap() {
    setSaving(true);
    try {
      // Connect verifies the login only; folders are chosen next (you can't list
      // folders until the mailbox is stored).
      await agentApi.put("/agent/email-credentials/imap", {
        host: host.trim(), port: Number(port) || 993, username: username.trim(), password, use_ssl: useSsl,
      });
      setPassword("");
      qc.invalidateQueries({ queryKey: ["agent-email-credentials"] });
      toast({ title: "Mailbox connected — now pick your folders below" });
    } catch (err: any) {
      toast({
        title: "Couldn't connect to that mailbox",
        description: err?.response?.data?.detail ?? "Check the host, port, and credentials.",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  }

  if (isLoading) return <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />;
  const connected = status?.connected && status.provider === "imap";

  if (connected) {
    return (
      <MailboxConfig
        status={status!}
        connectedLabel={`IMAP: ${status!.imap_username ?? status!.imap_host ?? "connected"}`}
        noun="folder"
      />
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Connect any IMAP mailbox; Job Radar's hosted agent reads and files it. We test the connection,
        store your credentials encrypted, then you pick your folders from the mailbox (no typing names).
      </p>
      <div className="space-y-3">
        <div className="grid grid-cols-3 gap-2">
          <div className="col-span-2 space-y-1">
            <Label htmlFor="imap-host" className="text-xs">IMAP host</Label>
            <Input id="imap-host" value={host} placeholder="imap.fastmail.com" onChange={(e) => setHost(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="imap-port" className="text-xs">Port</Label>
            <Input id="imap-port" value={port} onChange={(e) => setPort(e.target.value)} />
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="imap-user" className="text-xs">Username</Label>
          <Input id="imap-user" value={username} placeholder="you@example.com" onChange={(e) => setUsername(e.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="imap-pass" className="text-xs">Password</Label>
          <Input id="imap-pass" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <div className="flex items-center gap-2">
          <Switch id="imap-ssl" checked={useSsl} onCheckedChange={setUseSsl} />
          <Label htmlFor="imap-ssl" className="text-sm">Use SSL/TLS</Label>
        </div>

        <Button size="sm" disabled={saving || !host || !username || !password} onClick={saveImap}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Connect mailbox"}
        </Button>
        <p className="text-xs text-muted-foreground">
          Next: pick your <strong>root</strong> folder and the four sub-folders from your mailbox. Create them yourself first — the agent never creates folders.
        </p>
      </div>
    </div>
  );
}

// Per-user Slack notifications (JR-6). "Add to Slack" installs the app into the
// user's own workspace; they pick a public channel the agent posts to.
function SlackNotificationsSection() {
  const qc = useQueryClient();
  const { data: status, isLoading } = useQuery<SlackStatus>({
    queryKey: ["agent-slack-status"],
    queryFn: () => agentApi.get("/agent/slack/status").then((r) => r.data),
  });
  const { data: channels = [], isLoading: channelsLoading, isError: channelsError, refetch: refetchChannels } = useQuery<SlackChannel[]>({
    queryKey: ["agent-slack-channels"],
    queryFn: () => agentApi.get("/agent/slack/channels").then((r) => r.data),
    enabled: !!status?.connected,
  });
  const [connecting, setConnecting] = useState(false);
  const [pendingChannel, setPendingChannel] = useState<string | undefined>(undefined);
  const [savingChannel, setSavingChannel] = useState(false);

  // Keep the dropdown in sync with what's actually saved.
  useEffect(() => { setPendingChannel(status?.channel_id ?? undefined); }, [status?.channel_id]);

  async function connect() {
    setConnecting(true);
    try {
      const { data } = await agentApi.get("/agent/slack/oauth/start");
      window.location.href = data.authorization_url;
    } catch (err: any) {
      toast({ title: "Couldn't start Slack connect", description: err?.response?.data?.detail, variant: "destructive" });
      setConnecting(false);
    }
  }

  async function saveChannel() {
    if (!pendingChannel) return;
    const ch = channels.find((c) => c.id === pendingChannel);
    setSavingChannel(true);
    try {
      await agentApi.put("/agent/slack/channel", { channel_id: pendingChannel, channel_name: ch?.name ?? null });
      qc.invalidateQueries({ queryKey: ["agent-slack-status"] });
      toast({ title: `Notifications will post to #${ch?.name ?? pendingChannel}` });
    } catch (err: any) {
      toast({ title: "Failed to set channel", description: err?.response?.data?.detail, variant: "destructive" });
    } finally {
      setSavingChannel(false);
    }
  }

  async function disconnect() {
    try {
      await agentApi.delete("/agent/slack");
      qc.invalidateQueries({ queryKey: ["agent-slack-status"] });
      qc.invalidateQueries({ queryKey: ["agent-slack-channels"] });
      toast({ title: "Slack disconnected" });
    } catch (err: any) {
      toast({ title: "Failed to disconnect", description: err?.response?.data?.detail, variant: "destructive" });
    }
  }

  return (
    <div className="space-y-3">
      <div>
        <h3 className="font-medium">Notifications</h3>
        <p className="text-sm text-muted-foreground mt-0.5">
          Connect Slack so the agent can post job updates to a channel in your own workspace.
        </p>
      </div>

      {isLoading ? (
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      ) : !status?.connected ? (
        <Button size="sm" disabled={connecting} onClick={connect}>
          {connecting ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <><MessageSquare className="h-4 w-4 mr-1" /> Add to Slack</>}
        </Button>
      ) : (
        <div className="space-y-4">
          <div className="flex items-center justify-between gap-2 border rounded-md px-3 py-2">
            <Badge variant="secondary">Connected{status.team_name ? `: ${status.team_name}` : ""}</Badge>
            <Button
              size="sm" variant="ghost"
              className="h-7 shrink-0 text-destructive hover:bg-destructive/10 px-2"
              onClick={disconnect}
            >
              <Trash2 className="h-3.5 w-3.5 mr-1" /> Disconnect
            </Button>
          </div>

          <div className="space-y-1">
            <Label className="text-xs">Post notifications to</Label>
            {channelsError ? (
              <p className="text-sm text-muted-foreground">
                Couldn't load channels — the bot may need re-installing (try Disconnect, then Add to Slack again).
              </p>
            ) : (
              <div className="flex gap-2">
                <Select
                  value={pendingChannel}
                  onValueChange={setPendingChannel}
                  onOpenChange={(open) => { if (open) refetchChannels(); }}
                  disabled={channelsLoading}
                >
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder={channelsLoading ? "Loading channels…" : "Choose a channel"} />
                  </SelectTrigger>
                  <SelectContent>
                    {channels.map((c) => (
                      <SelectItem key={c.id} value={c.id}>#{c.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  size="sm"
                  disabled={savingChannel || !pendingChannel || pendingChannel === status.channel_id}
                  onClick={saveChannel}
                >
                  {savingChannel ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
                </Button>
              </div>
            )}
            <p className="text-xs text-muted-foreground">
              {status.channel_name && <>Currently posting to <span className="font-medium">#{status.channel_name}</span>. </>}
              Public channels only — pick one and hit <span className="font-medium">Save</span>. The agent uses your workspace's own bot.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function EmailAgentTab() {
  const { data: status } = useMailboxStatus();
  const [provider, setProvider] = useState<ProviderKey>("proton");

  // Default the selector to the user's current connection (once status loads).
  const synced = useRef(false);
  useEffect(() => {
    if (synced.current || !status) return;
    synced.current = true;
    if (status.provider === "gmail") setProvider("gmail");
    else if (status.provider === "imap") setProvider("other");
  }, [status]);

  return (
    <div className="max-w-lg space-y-8">
      <div className="space-y-3">
        <div>
          <h3 className="font-medium">Email provider</h3>
          <p className="text-sm text-muted-foreground mt-0.5">
            How the agent reaches your mailbox. Proton runs locally; Gmail and Other are processed by the hosted agent.
          </p>
        </div>
        <ProviderSelector value={provider} onChange={setProvider} />
        <div className="pt-2">
          {provider === "proton" && <ProtonPanel />}
          {provider === "gmail" && <GmailPanel />}
          {provider === "other" && <ImapPanel />}
        </div>
      </div>

      <Separator />
      <SlackNotificationsSection />

      <Separator />
      <div className="space-y-3">
        <div>
          <h3 className="font-medium">Status &amp; stats</h3>
          <p className="text-sm text-muted-foreground mt-0.5">
            Your agent's recent activity. Detailed LLM traces live in Langfuse.
          </p>
        </div>
        <AgentStatsView scope="me" />
      </div>
    </div>
  );
}

// ─── Main Settings page ───────────────────────────────────────────────────────

export function SettingsPage() {
  const [searchParams] = useSearchParams();
  const gmail = searchParams.get("gmail");
  const slack = searchParams.get("slack");
  const defaultTab = (gmail || slack) ? "agent" : (searchParams.get("tab") ?? "account");

  useEffect(() => {
    if (!gmail) return;
    if (gmail === "connected") {
      toast({ title: "Gmail connected", description: "Your mailbox is linked — set your labels below." });
    } else if (gmail === "norefresh") {
      toast({
        title: "Gmail didn't return a refresh token",
        description: "Remove Job Radar under myaccount.google.com → Security → Third-party access, then reconnect.",
        variant: "destructive",
      });
    } else {
      toast({ title: "Gmail connection failed", description: "Please try connecting again.", variant: "destructive" });
    }
  }, [gmail]);

  useEffect(() => {
    if (!slack) return;
    if (slack === "connected") {
      toast({ title: "Slack connected", description: "Now pick a channel below for notifications." });
    } else {
      toast({ title: "Slack connection failed", description: "Please try Add to Slack again.", variant: "destructive" });
    }
  }, [slack]);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Settings</h1>
      <Tabs defaultValue={defaultTab}>
        <TabsList className="flex-wrap h-auto gap-1">
          <TabsTrigger value="account">Account Details</TabsTrigger>
          <TabsTrigger value="keys">API Keys</TabsTrigger>
          <TabsTrigger value="agent">Email Agent</TabsTrigger>
          <TabsTrigger value="bookmarklet">Bookmarklet</TabsTrigger>
        </TabsList>
        <TabsContent value="account"     className="mt-6"><AccountTab /></TabsContent>
        <TabsContent value="keys"        className="mt-6"><KeysTab /></TabsContent>
        <TabsContent value="agent"       className="mt-6"><EmailAgentTab /></TabsContent>
        <TabsContent value="bookmarklet" className="mt-6"><BookmarkletTab /></TabsContent>
      </Tabs>
    </div>
  );
}
