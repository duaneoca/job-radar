import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookmarkIcon, Chrome, Eye, EyeOff, Loader2, Trash2 } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Separator } from "../components/ui/separator";
import { Badge } from "../components/ui/badge";
import { keysApi, authApi } from "../lib/api";
import { useAuthStore } from "../store/auth";
import { toast } from "../hooks/useToast";
import type { APIKey, LLMProvider } from "../lib/types";

// ─── Account Details tab ──────────────────────────────────────────────────────

function AccountTab() {
  const { user, setUser } = useAuthStore();
  const [searchParams] = useSearchParams();
  const forced = searchParams.get("force") === "1";

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

  async function saveKey(provider: LLMProvider, key: string) {
    try {
      await keysApi.put("/keys", { provider, api_key: key });
      qc.invalidateQueries({ queryKey: ["keys"] });
      toast({ title: "Key saved" });
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

  function pickProvider(p: LLMProvider) {
    setPreferred(p);
    localStorage.setItem(PREFERRED_PROVIDER_KEY, p);
  }

  const tavilyKey = keyMap["tavily"];

  return (
    <div className="max-w-lg space-y-6">
      <p className="text-sm text-muted-foreground">
        Keys are encrypted at rest. Only the last 4 characters are shown after saving.
      </p>

      <div className="space-y-3">
        <div>
          <h3 className="font-medium">AI model provider</h3>
          <p className="text-sm text-muted-foreground mt-0.5">
            Choose one — this is the model that scores your job matches.
          </p>
        </div>
        <div className="space-y-2">
          {AI_PROVIDERS.map(({ value, label, description, placeholder }) => {
            const isSelected = preferred === value;
            const existing = keyMap[value];
            return (
              <div
                key={value}
                className={`rounded-lg border p-3 cursor-pointer transition-colors ${
                  isSelected ? "border-primary bg-primary/5" : "hover:border-muted-foreground/50 hover:bg-muted/30"
                }`}
                onClick={() => pickProvider(value)}
              >
                <div className="flex items-center gap-3">
                  <div className={`h-4 w-4 rounded-full border-2 shrink-0 flex items-center justify-center ${
                    isSelected ? "border-primary" : "border-muted-foreground/40"
                  }`}>
                    {isSelected && <div className="h-2 w-2 rounded-full bg-primary" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{label}</span>
                      <span className="text-xs text-muted-foreground">{description}</span>
                      {existing && (
                        <Badge variant="outline" className="font-mono text-xs ml-auto">
                          ••••{existing.key_hint}
                        </Badge>
                      )}
                    </div>
                  </div>
                </div>
                {isSelected && (
                  <div onClick={(e) => e.stopPropagation()}>
                    <KeyInput
                      provider={value} placeholder={placeholder} existing={existing}
                      onSave={(k) => saveKey(value, k)}
                      onDelete={() => deleteKey(value)}
                    />
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
if(!ti){alert('Job Radar: Could not read this page.\\nNavigate to a specific LinkedIn job posting.');return;}
data={title:ti,company:co,location:lo,description:de,url:ur,external_id:id,remote:re,source:'linkedin'};
}else if(host.includes('dice.com')){
var ti=tc('[data-testid="job-detail-header-card"] h1')||tc('h1');
var co=tc('a[href*="/company-profile/"]');
var lo='';
var loSpans=document.querySelectorAll('[data-testid="job-detail-header-card"] span span');
for(var i=0;i<loSpans.length;i++){var st=(loSpans[i].textContent||'').trim();if(st.length>3&&st.indexOf('\\u2022')<0&&st.indexOf('Posted')<0&&st.indexOf('Updated')<0){lo=st;break;}}
var de=tc('[class*="jobDescription"]')||longest(['#job-description','article']);
var hdText=(document.querySelector('[data-testid="job-detail-header-card"]')||{textContent:''}).textContent;
var salMatch=hdText.match(/\\$([\\d,]+)\\s*[-\\u2013]\\s*\\$([\\d,]+)/);
var salMin=salMatch?parseInt(salMatch[1].replace(/,/g,''),10):null;
var salMax=salMatch?parseInt(salMatch[2].replace(/,/g,''),10):null;
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
var salMatch=jmiText.match(/\\$([\\d,]+)\\s*[-\\u2013]\\s*\\$([\\d,]+)/);
var salMin=salMatch?parseInt(salMatch[1].replace(/,/g,''),10):null;
var salMax=salMatch?parseInt(salMatch[2].replace(/,/g,''),10):null;
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
var salMatch=salText.match(/\\$([\\d,]+)\\s*[-\\u2013]\\s*\\$([\\d,]+)/);
var salMin=salMatch?parseInt(salMatch[1].replace(/,/g,''),10):null;
var salMax=salMatch?parseInt(salMatch[2].replace(/,/g,''),10):null;
var idMatch=ur.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
var id=idMatch?idMatch[0]:'';
var re=/remote/i.test(de);
if(!ti){alert('Job Radar: Could not read this Monster page.\\nNavigate to a specific job posting.');return;}
data={title:ti,company:co,location:lo,description:de,url:ur,external_id:id,remote:re,source:'monster',salary_min:salMin,salary_max:salMax};
}else{
alert('Job Radar: This site is not yet supported.\\nSupported: LinkedIn, Dice, BuiltIn, Monster.');
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
          <strong>Supported:</strong> LinkedIn, Dice, BuiltIn, Monster
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

// ─── Main Settings page ───────────────────────────────────────────────────────

export function SettingsPage() {
  const [searchParams] = useSearchParams();
  const defaultTab = searchParams.get("tab") ?? "account";

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Settings</h1>
      <Tabs defaultValue={defaultTab}>
        <TabsList className="flex-wrap h-auto gap-1">
          <TabsTrigger value="account">Account Details</TabsTrigger>
          <TabsTrigger value="keys">API Keys</TabsTrigger>
          <TabsTrigger value="bookmarklet">Bookmarklet</TabsTrigger>
        </TabsList>
        <TabsContent value="account"     className="mt-6"><AccountTab /></TabsContent>
        <TabsContent value="keys"        className="mt-6"><KeysTab /></TabsContent>
        <TabsContent value="bookmarklet" className="mt-6"><BookmarkletTab /></TabsContent>
      </Tabs>
    </div>
  );
}
