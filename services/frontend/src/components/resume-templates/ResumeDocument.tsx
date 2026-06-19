import { useEffect, useRef } from "react";
import { ClassicTemplate } from "./ClassicTemplate";
import { ModernTemplate } from "./ModernTemplate";

export type TemplateId = "classic" | "modern";

export const TEMPLATES: { id: TemplateId; label: string; note: string }[] = [
  { id: "classic", label: "Classic", note: "Single column · most ATS-safe · any length" },
  { id: "modern", label: "Modern", note: "Sidebar · one page" },
];

// Binary-search the largest --scale (font + em spacing) that fits the page budget,
// then apply it. Deterministic for the same content+template. (Validated approach;
// Paged.js for surgical pagination is Phase 3b.)
function autofit(el: HTMLElement) {
  const PX = 96;
  const onePage = el.getAttribute("data-fit") === "one-page";
  const FLOOR = onePage ? 0.74 : 0.85;
  const set = (s: number) => el.style.setProperty("--scale", String(s));

  set(1);
  const budget = onePage
    ? Math.round(10.4 * PX)
    : Math.max(1, Math.round(el.scrollHeight / (10 * PX))) * 10 * PX * 0.985;
  if (el.scrollHeight <= budget) return;

  let lo = FLOOR, hi = 1, best = FLOOR;
  for (let i = 0; i < 14; i++) {
    const mid = (lo + hi) / 2;
    set(mid);
    if (el.scrollHeight <= budget) { best = mid; lo = mid; } else { hi = mid; }
  }
  set(best);
}

export function ResumeDocument({ template, data }: { template: TemplateId; data: any }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current?.querySelector("[data-resume-doc]") as HTMLElement | null;
    if (el) {
      // Let layout settle (fonts/reflow) before measuring.
      const t = setTimeout(() => autofit(el), 60);
      return () => clearTimeout(t);
    }
  }, [template, data]);

  return (
    <div ref={ref}>
      {template === "modern" ? <ModernTemplate data={data} /> : <ClassicTemplate data={data} />}
    </div>
  );
}
