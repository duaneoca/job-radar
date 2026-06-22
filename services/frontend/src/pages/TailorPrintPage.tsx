import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Printer, AlertTriangle } from "lucide-react";
import { Button } from "../components/ui/button";
import { jobsApi } from "../lib/api";
import { cn } from "../lib/utils";
import { effectiveResume } from "../lib/resumeEffective";
import { PagedPreview } from "../components/resume-templates/PagedPreview";
import { ResumeKnobs } from "../components/resume-templates/ResumeKnobs";
import { TEMPLATES, type TemplateId } from "../components/resume-templates/ResumeDocument";
import { loadSettings, saveSettings, type ResumeSettings } from "../lib/resumeSettings";
import type { TailorState } from "../lib/types";

// Screen chrome for the Paged.js sheets (true page boxes) + print rules. In print the
// toolbar / tip / off-screen source are hidden; Paged.js itself maps each box to a
// physical page.
const CSS = `
.print-bg{ background:#e9edf2; min-height:100vh; }
.paged-target{ padding:24px 0; }
.paged-target .pagedjs_pages{ margin:0 auto; }
.paged-target .pagedjs_page{ background:#fff; box-shadow:0 2px 14px rgba(0,0,0,.12); margin:0 auto 24px; }
.paged-source{ position:absolute; left:-10000px; top:0; width:8.5in; visibility:hidden; pointer-events:none; }
@media print{
  #print-toolbar, #print-knobs, .print-tip, .paged-source{ display:none !important; }
  .print-bg{ background:#fff !important; }
  .paged-target{ padding:0 !important; }
  .paged-target .pagedjs_page{ box-shadow:none !important; margin:0 auto !important; }
}
`;

export function TailorPrintPage() {
  const { id } = useParams<{ id: string }>();
  const [template, setTemplate] = useState<TemplateId>(
    () => (localStorage.getItem("jr-resume-template") as TemplateId) || "classic",
  );
  const [pages, setPages] = useState<number | null>(null);
  const [settings, setSettings] = useState<ResumeSettings>(() => loadSettings());

  function updateSettings(next: ResumeSettings) {
    setSettings(next);
    saveSettings(next);
  }

  const { data: state, isLoading } = useQuery<TailorState | null>({
    queryKey: ["tailor", id],
    queryFn: () => jobsApi.get(`/jobs/${id}/tailor-resume`).then((r) => r.data).catch(() => null),
    enabled: !!id,
  });

  // Stable identity so Paged.js only re-chunks when the résumé actually changes.
  const data = useMemo(() => (state ? effectiveResume(state) : null), [state]);

  function pick(t: TemplateId) {
    setTemplate(t);
    setPages(null);
    localStorage.setItem("jr-resume-template", t);
  }

  if (isLoading) {
    return <div className="flex justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>;
  }
  if (!state || !data) {
    return (
      <div className="max-w-md mx-auto py-20 text-center text-sm text-muted-foreground">
        <AlertTriangle className="h-6 w-6 mx-auto mb-2 opacity-50" />
        This job hasn't been tailored yet. Tailor it first, then come back to print.
      </div>
    );
  }

  return (
    <div className="print-bg">
      <style>{CSS}</style>

      <div id="print-toolbar" className="sticky top-0 z-10 flex flex-wrap items-center gap-3 border-b bg-background/95 backdrop-blur px-4 py-2.5">
        <span className="text-sm font-medium">Print / PDF</span>
        <div className="flex gap-1">
          {TEMPLATES.map((t) => (
            <button
              key={t.id}
              type="button"
              title={t.note}
              onClick={() => pick(t.id)}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs border transition-colors",
                template === t.id ? "bg-accent text-accent-foreground border-accent" : "hover:bg-accent/50",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
        {pages != null && (
          <span className="text-xs text-muted-foreground">{pages} page{pages === 1 ? "" : "s"}</span>
        )}
        <span className="text-xs text-muted-foreground hidden sm:inline">
          {TEMPLATES.find((t) => t.id === template)?.note}
        </span>
        <Button size="sm" className="ml-auto" onClick={() => window.print()}>
          <Printer className="h-4 w-4 mr-1" /> Download PDF
        </Button>
      </div>

      <div id="print-knobs" className="sticky top-[49px] z-10 flex items-center border-b bg-background/95 backdrop-blur px-4 py-2">
        <ResumeKnobs settings={settings} onChange={updateSettings} showMargin={template === "classic"} />
      </div>

      <p className="print-tip text-center text-xs text-muted-foreground pt-3 px-4">
        Tip: in the print dialog choose <b>Save as PDF</b>, margins <b>None</b>
        {template === "modern" && <>, and turn <b>Background graphics</b> ON for the sidebar</>}.
        {pages != null && pages > 1 && (
          <> If a blank page appears at the very end, set the print range to <b>1&ndash;{pages}</b> to skip it.</>
        )}
      </p>

      <PagedPreview template={template} data={data} settings={settings} onPages={setPages} />
    </div>
  );
}
