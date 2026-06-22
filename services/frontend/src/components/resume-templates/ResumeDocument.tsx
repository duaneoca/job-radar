import { ClassicTemplate } from "./ClassicTemplate";
import { ModernTemplate } from "./ModernTemplate";

export type TemplateId = "classic" | "modern";

export const TEMPLATES: { id: TemplateId; label: string; note: string }[] = [
  { id: "classic", label: "Classic", note: "Single column · most ATS-safe · any length" },
  { id: "modern", label: "Modern", note: "Sidebar · one page" },
];

// Pure template chooser. Pagination, page-boxes, @page margins and keep-together are
// handled deterministically by Paged.js in <PagedPreview> (Phase 3b) — this just emits
// the raw template DOM (with its own <style>), which PagedPreview reads and chunks.
export function ResumeDocument({ template, data }: { template: TemplateId; data: any }) {
  return template === "modern" ? <ModernTemplate data={data} /> : <ClassicTemplate data={data} />;
}
