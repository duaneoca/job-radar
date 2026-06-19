import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Printer, AlertTriangle } from "lucide-react";
import { Button } from "../components/ui/button";
import { jobsApi } from "../lib/api";
import { cn } from "../lib/utils";
import { effectiveResume } from "../lib/resumeEffective";
import { ResumeDocument, TEMPLATES, type TemplateId } from "../components/resume-templates/ResumeDocument";
import type { TailorState } from "../lib/types";

const CSS = `
.print-bg{ background:#e9edf2; min-height:100vh; }
.print-sheet{ background:#fff; margin:24px auto; box-shadow:0 2px 14px rgba(0,0,0,.12); width:8.5in; }
.print-sheet.pad{ padding:.5in; }
@media print{
  #print-toolbar{ display:none !important; }
  .print-bg{ background:#fff !important; }
  .print-sheet{ margin:0 !important; box-shadow:none !important; width:auto !important; }
  .print-sheet.pad{ padding:0 !important; }
}
`;

export function TailorPrintPage() {
  const { id } = useParams<{ id: string }>();
  const [template, setTemplate] = useState<TemplateId>(
    () => (localStorage.getItem("jr-resume-template") as TemplateId) || "classic",
  );

  const { data: state, isLoading } = useQuery<TailorState | null>({
    queryKey: ["tailor", id],
    queryFn: () => jobsApi.get(`/jobs/${id}/tailor-resume`).then((r) => r.data).catch(() => null),
    enabled: !!id,
  });

  function pick(t: TemplateId) {
    setTemplate(t);
    localStorage.setItem("jr-resume-template", t);
  }

  if (isLoading) {
    return <div className="flex justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>;
  }
  if (!state) {
    return (
      <div className="max-w-md mx-auto py-20 text-center text-sm text-muted-foreground">
        <AlertTriangle className="h-6 w-6 mx-auto mb-2 opacity-50" />
        This job hasn't been tailored yet. Tailor it first, then come back to print.
      </div>
    );
  }

  const data = effectiveResume(state);

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
        <span className="text-xs text-muted-foreground hidden sm:inline">
          {TEMPLATES.find((t) => t.id === template)?.note}
        </span>
        <Button size="sm" className="ml-auto" onClick={() => window.print()}>
          <Printer className="h-4 w-4 mr-1" /> Download PDF
        </Button>
      </div>

      <p className="text-center text-xs text-muted-foreground pt-3 px-4 print:hidden">
        Tip: in the print dialog choose <b>Save as PDF</b>, margins <b>None</b>
        {template === "modern" && <>, and turn <b>Background graphics</b> ON for the sidebar</>}.
      </p>

      <div className={cn("print-sheet", template === "classic" && "pad")}>
        <ResumeDocument template={template} data={data} />
      </div>
    </div>
  );
}
