import { useState } from "react";
import { ToastProvider, ToastViewport, Toast, ToastTitle, ToastDescription, ToastClose } from "./toast";
import { dismissAllToasts, dismissToast, useToastState } from "../../hooks/useToast";

export function Toaster() {
  const toasts = useToastState();
  return (
    <ToastProvider>
      {toasts.map((t) => (
        <Toast
          key={t.id}
          variant={t.variant}
          // Radix closes on Esc and on swipe as well as on the button; route all
          // of them through the same removal so a dismissed toast can't linger
          // invisibly in state and count against the cap.
          onOpenChange={(open) => { if (!open) dismissToast(t.id); }}
        >
          {/* Selectable. Radix starts a swipe-to-dismiss on pointerdown anywhere
              in the toast, which turned every drag across the text into a swipe;
              stopping the event here leaves swiping to the toast's edges. */}
          <div className="grid gap-1 select-text cursor-text" onPointerDown={(e) => e.stopPropagation()}>
            <ToastTitle>{t.title}</ToastTitle>
            {t.description && <ToastDescription>{t.description}</ToastDescription>}
          </div>
          <div className="flex shrink-0 gap-1.5">
            {/* Errors get a Copy button: pasting text into a support thread or a
                coding session beats a screenshot of it. */}
            {t.variant === "destructive" && <CopyToastButton text={t.description ? `${t.title}: ${t.description}` : t.title} />}
            <ToastClose />
          </div>
        </Toast>
      ))}
      {/* Toasts portal themselves into the viewport's <ol>; this button is a
          real child of it. The viewport is flex-col-reverse, so it renders above
          the stack — verified in the browser, not assumed. */}
      <ToastViewport>
        {toasts.length > 1 && (
          // Only offered once they stack. Nothing expires on its own now, so
          // clearing several one at a time would be tedious.
          <button
            type="button"
            onClick={dismissAllToasts}
            className="pointer-events-auto self-end rounded-md border bg-background px-3 py-1 text-xs font-medium text-muted-foreground shadow-sm hover:bg-muted"
          >
            Dismiss all ({toasts.length})
          </button>
        )}
      </ToastViewport>
    </ToastProvider>
  );
}

function CopyToastButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try { await navigator.clipboard.writeText(text); setCopied(true); } catch { /* text is still selectable */ }
      }}
      className="rounded-md border border-destructive-foreground/40 px-3 py-1 text-xs font-medium transition-colors hover:bg-destructive-foreground/10 focus:outline-none focus:ring-2 focus:ring-ring"
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
