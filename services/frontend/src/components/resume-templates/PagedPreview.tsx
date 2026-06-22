import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { ResumeDocument, type TemplateId } from "./ResumeDocument";
import { DENSITY, type ResumeSettings } from "../../lib/resumeSettings";

// Page geometry handed to Paged.js as a top-level @page rule. The templates carry their
// own `@media print { @page {…} }`, but ours is unconditional AND comes last, so it wins
// in Paged.js's preview context either way. width:auto lets the résumé fill the page's
// content box (the sheet minus its margins) instead of its fixed screen width.
function pageCss(template: TemplateId, marginIn: number): string {
  // Modern's sidebar bleeds to the edge — always full-bleed, ignore the margin knob.
  const margin = template === "modern" ? "0" : `${marginIn}in`;
  return `
    @page { size: letter; margin: ${margin}; }
    .rt-classic, .rt-modern { width: auto !important; margin: 0 !important; }
  `;
}

// Apply the user knobs as CSS variables on the template root. Set on the SOURCE before
// autofit so the one-page measurement reflects the chosen font; the clone inherits them.
function applySettings(el: HTMLElement, s: ResumeSettings): void {
  const { line, gap } = DENSITY[s.density];
  el.style.setProperty("--rt-font", `${s.fontPt}pt`);
  el.style.setProperty("--rt-line", String(line));
  el.style.setProperty("--rt-gap", String(gap));
  if (s.accent) el.style.setProperty("--rt-accent", s.accent);
  else el.style.removeProperty("--rt-accent"); // null → template's own default(s)
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

// Remove genuinely-empty trailing page boxes Paged.js may have appended. Walks from
// the last page back, dropping any whose content area has no text and no media, and
// stops at the first real page. Returns how many were removed (to fix the page count).
function stripTrailingBlankPages(target: HTMLElement): number {
  const pages = Array.from(target.querySelectorAll<HTMLElement>(".pagedjs_page"));
  let removed = 0;
  for (let i = pages.length - 1; i > 0; i--) {
    const area = pages[i].querySelector(".pagedjs_page_content");
    const hasText = (area?.textContent ?? "").trim().length > 0;
    const hasMedia = !!area?.querySelector("img, svg, canvas");
    if (hasText || hasMedia) break;
    pages[i].remove();
    removed++;
  }
  return removed;
}

// Keep only the first page box, removing any others. Used to enforce the one-page
// contract for templates whose grid can't fragment cleanly (Modern's sidebar).
function removeAfterFirstPage(target: HTMLElement): void {
  Array.from(target.querySelectorAll<HTMLElement>(".pagedjs_page"))
    .slice(1)
    .forEach((p) => p.remove());
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
  settings,
  onPages,
}: {
  template: TemplateId;
  data: unknown;
  settings: ResumeSettings;
  onPages?: (n: number) => void;
}) {
  const sourceRef = useRef<HTMLDivElement>(null);
  const targetRef = useRef<HTMLDivElement>(null);
  const onPagesRef = useRef(onPages);
  onPagesRef.current = onPages;
  const [rendering, setRendering] = useState(true);
  const [error, setError] = useState(false);
  const [overflow, setOverflow] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const target = targetRef.current;
    setRendering(true);
    setError(false);
    setOverflow(false);

    // Let React paint the hidden source + fonts settle before measuring/chunking.
    const timer = setTimeout(async () => {
      const root = sourceRef.current?.querySelector("[data-resume-doc]") as HTMLElement | null;
      if (!root || !target) return;

      // Strip the template's own @page rule. Paged.js's print-media handler hoists
      // rules out of @media print to the END of the sheet, so the template's @page would
      // land AFTER our pageCss @page and override the margin knob. Removing it makes our
      // pageCss the sole @page authority, so the margin slider actually takes effect.
      const templateCss = (root.querySelector("style")?.textContent ?? "").replace(
        /@page[^{]*\{[^}]*\}/g,
        "",
      );
      applySettings(root, settings); // knob vars on the source, before measuring
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
          [{ resume: `${templateCss}\n${pageCss(template, settings.marginIn)}` }],
          target,
        );
        if (cancelled) return;
        // Paged.js appends an empty trailing page when content ends near a page
        // boundary (a known behaviour, independent of template/content). Strip any
        // genuinely-blank trailing pages so the preview and the PDF match the content.
        let kept = flow.total - stripTrailingBlankPages(target);
        // One-page templates (Modern) must not fragment — extra pages mean the grid
        // spilled and the sidebar/columns break. Keep page 1, drop the rest, and warn.
        if (onePage && kept > 1) {
          removeAfterFirstPage(target);
          kept = 1;
          setOverflow(true);
        }
        onPagesRef.current?.(Math.max(1, kept));
      } catch (e) {
        if (!cancelled) {
          console.error("Paged.js preview failed", e);
          setError(true);
        }
      } finally {
        if (!cancelled) setRendering(false);
      }
    }, 120); // debounce: live knob drags re-chunk only after a brief pause

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [template, data, settings]);

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
      {overflow && !rendering && (
        <div className="mx-auto mt-4 max-w-xl rounded-md border border-amber-300 bg-amber-50 px-4 py-2.5 text-xs text-amber-900 print:hidden">
          This résumé is too long for the one-page layout at these settings — the
          overflow was trimmed. Lower the <b>font size</b> or <b>density</b>, shorten
          the content, or switch to the <b>Classic</b> template (which spans multiple pages).
        </div>
      )}

      {/* Hidden via opacity (NOT display:none) while chunking — Paged.js measures the
          page boxes it creates with getBoundingClientRect, which needs real layout
          geometry. display:none would zero that out and make Paged.js throw. */}
      <div ref={targetRef} className="paged-target" style={rendering ? { opacity: 0, pointerEvents: "none" } : undefined} />
    </div>
  );
}
