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
          <div className="grid gap-1">
            <ToastTitle>{t.title}</ToastTitle>
            {t.description && <ToastDescription>{t.description}</ToastDescription>}
          </div>
          <ToastClose />
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
