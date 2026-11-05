import { Lock } from "lucide-react";
import { cn } from "../../lib/utils";

/**
 * A styled browser-chrome mockup that wraps a screenshot. The URL pill always
 * shows the production host, so screenshots captured on staging (or anywhere)
 * never leak the real address bar. Used across the marketing landing page.
 *
 * With `phoneSrc`, screens narrower than `sm` get a phone-shaped frame around a
 * phone-width screenshot instead: a desktop shot shrunk to a phone is unreadable.
 * `<picture>` picks the image, so a visitor downloads only the one they see.
 */
export function BrowserFrame({
  src,
  phoneSrc,
  alt,
  url = "job-radar.net",
  className,
}: {
  src: string;
  phoneSrc?: string;
  alt: string;
  url?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border bg-card shadow-2xl shadow-black/15 dark:border-white/10 dark:bg-slate-950 dark:shadow-black/40",
        phoneSrc &&
          "max-sm:mx-auto max-sm:max-w-[17.5rem] max-sm:rounded-[2rem] max-sm:border-[6px] max-sm:border-slate-800 max-sm:dark:border-slate-700",
        className,
      )}
    >
      {/* chrome bar */}
      <div
        className={cn(
          "flex items-center gap-2 border-b bg-muted/60 px-3 py-2.5 dark:border-white/10 dark:bg-white/[0.04]",
          phoneSrc && "max-sm:hidden",
        )}
      >
        <span className="flex gap-1.5">
          <span className="h-3 w-3 rounded-full bg-red-400/70" />
          <span className="h-3 w-3 rounded-full bg-amber-400/70" />
          <span className="h-3 w-3 rounded-full bg-emerald-400/70" />
        </span>
        <span className="mx-auto flex items-center gap-1.5 rounded-md bg-background px-3 py-1 text-xs text-muted-foreground dark:bg-black/40 dark:text-slate-400">
          <Lock className="h-3 w-3" />
          {url}
        </span>
      </div>
      {phoneSrc ? (
        <picture>
          {/* Matches Tailwind's sm breakpoint, where the frame switches. */}
          <source media="(min-width: 640px)" srcSet={src} />
          <img src={phoneSrc} alt={alt} loading="lazy" className="block w-full" />
        </picture>
      ) : (
        <img src={src} alt={alt} loading="lazy" className="block w-full" />
      )}
    </div>
  );
}
