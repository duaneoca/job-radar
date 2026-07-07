import { Lock } from "lucide-react";
import { cn } from "../../lib/utils";

/**
 * A styled browser-chrome mockup that wraps a screenshot. The URL pill always
 * shows the production host, so screenshots captured on staging (or anywhere)
 * never leak the real address bar. Used across the marketing landing page.
 */
export function BrowserFrame({
  src,
  alt,
  url = "job-radar.net",
  className,
}: {
  src: string;
  alt: string;
  url?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border border-white/10 bg-slate-950 shadow-2xl shadow-black/40",
        className,
      )}
    >
      {/* chrome bar */}
      <div className="flex items-center gap-2 border-b border-white/10 bg-white/[0.04] px-3 py-2.5">
        <span className="flex gap-1.5">
          <span className="h-3 w-3 rounded-full bg-red-400/70" />
          <span className="h-3 w-3 rounded-full bg-amber-400/70" />
          <span className="h-3 w-3 rounded-full bg-emerald-400/70" />
        </span>
        <span className="mx-auto flex items-center gap-1.5 rounded-md bg-black/40 px-3 py-1 text-xs text-slate-400">
          <Lock className="h-3 w-3" />
          {url}
        </span>
      </div>
      <img src={src} alt={alt} loading="lazy" className="block w-full" />
    </div>
  );
}
