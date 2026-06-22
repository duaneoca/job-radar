import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { ResumeDocument, type TemplateId } from "./ResumeDocument";

// Page geometry handed to Paged.js as a top-level @page rule. The templates carry their
// own `@media print { @page {…} }`, but ours is unconditional AND comes last, so it wins
// in Paged.js's preview context either way. width:auto lets the résumé fill the page's
// content box (the sheet minus its margins) instead of its fixed screen width.
function pageCss(template: TemplateId): string {
  const margin = template === "modern" ? "0" : "0.5in"; // Modern's sidebar bleeds to the edge
  return `
    @page { size: letter; margin: ${margin}; }
    .rt-classic, .rt-modern { width: auto !important; margin: 0 !important; }
  `;
}

// Largest --scale (≤1) whose natural height fits a single printable page. Mirrors the
// validated Phase-3a binary search; only one-page templates (Modern) need it — Classic
// is single-column and simply paginates across real Paged.js pages, no shrinking.
function fitOnePageScale(src: HTMLElement): number {
  const PX = 96;
  // Modern prints margin:0 (usable page ~11in), but leave real slack under that so a
  // trailing margin / sub-pixel rounding can't tip the grid onto a blank second page.
  const budget = Math.round(10.3 * PX);
  const set = (s: number) => src.style.setProperty("--scale", String(s));
  set(1);
  if (src.scrollHeight <= budget) {
    src.style.removeProperty("--scale");
    return 1;
  }
  // Floor is low enough that even a longer résumé still lands on a single page —
  // fitting (small font) beats a broken second page. Very small = a nudge to trim.
  let lo = 0.55, hi = 1, best = 0.55;
  for (let i = 0; i < 14; i++) {
    const mid = (lo + hi) / 2;
    set(mid);
    if (src.scrollHeight <= budget) { best = mid; lo = mid; } else { hi = mid; }
  }
  src.style.removeProperty("--scale"); // restore source; scale is baked into the clone
  return best;
}

/**
 * Renders the chosen résumé template into real, paginated page boxes via Paged.js.
 * What you see on screen (the `.pagedjs_page` sheets) is exactly what prints — Paged.js
 * injects the `@media print` mapping that puts one box per physical page.
 *
 * `data` MUST be referentially stable (memoize in the parent) or the preview re-chunks
 * on every parent render.
 */
export function PagedPreview({
  template,
  data,
  onPages,
}: {
  template: TemplateId;
  data: unknown;
  onPages?: (n: number) => void;
}) {
  const sourceRef = useRef<HTMLDivElement>(null);
  const targetRef = useRef<HTMLDivElement>(null);
  const onPagesRef = useRef(onPages);
  onPagesRef.current = onPages;
  const [rendering, setRendering] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const target = targetRef.current;
    setRendering(true);
    setError(false);

    // Let React paint the hidden source + fonts settle before measuring/chunking.
    const timer = setTimeout(async () => {
      const root = sourceRef.current?.querySelector("[data-resume-doc]") as HTMLElement | null;
      if (!root || !target) return;

      const templateCss = root.querySelector("style")?.textContent ?? "";
      const onePage = root.getAttribute("data-fit") === "one-page";
      const scale = onePage ? fitOnePageScale(root) : 1;

      // Clone of the template root (.rt-classic / .rt-modern): drop the inline <style>
      // (passed as a stylesheet instead) and bake in the fitted scale.
      const inner = root.cloneNode(true) as HTMLElement;
      inner.querySelector("style")?.remove();
      inner.style.setProperty("--scale", String(scale));

      // Paged.js flows the *children* of the content root into the page area and drops
      // the root element itself. Our entire template stylesheet is scoped to
      // .rt-classic/.rt-modern and the CSS vars live on that element — so we wrap one
      // level deeper. That keeps .rt-* as a flowed child Paged.js preserves (and
      // reconstructs on every page split), so the scoped rules + vars actually apply.
      const content = document.createElement("div");
      content.appendChild(inner);

      target.innerHTML = "";
      try {
        const { Previewer } = await import("pagedjs");
        if (cancelled) return;
        const previewer = new Previewer();
        const flow = await previewer.preview(
          content,
          [{ resume: `${templateCss}\n${pageCss(template)}` }],
          target,
        );
        if (cancelled) return;
        onPagesRef.current?.(flow.total);
      } catch (e) {
        if (!cancelled) {
          console.error("Paged.js preview failed", e);
          setError(true);
        }
      } finally {
        if (!cancelled) setRendering(false);
      }
    }, 80);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [template, data]);

  return (
    <div className="paged-wrap">
      {/* Hidden source — rendered once by React, read into Paged.js. Off-screen rather
          than display:none so it keeps real layout for the one-page autofit measurement. */}
      <div ref={sourceRef} className="paged-source" aria-hidden>
        <ResumeDocument template={template} data={data} />
      </div>

      {rendering && (
        <div className="flex justify-center py-24">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}
      {error && !rendering && (
        <p className="text-center text-sm text-destructive py-10">
          Couldn't render the paged preview. Try reloading.
        </p>
      )}

      {/* Hidden via opacity (NOT display:none) while chunking — Paged.js measures the
          page boxes it creates with getBoundingClientRect, which needs real layout
          geometry. display:none would zero that out and make Paged.js throw. */}
      <div ref={targetRef} className="paged-target" style={rendering ? { opacity: 0, pointerEvents: "none" } : undefined} />
    </div>
  );
}
