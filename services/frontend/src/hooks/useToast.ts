import { useCallback, useEffect, useState } from "react";

export interface ToastMessage {
  id: string;
  title: string;
  description?: string;
  variant?: "default" | "destructive";
}

// Toasts stay until dismissed — see toaster.tsx. That makes an upper bound
// necessary: without auto-dismiss, a handful of actions would otherwise fill the
// screen. Oldest goes first, so the message you just triggered is always visible.
const MAX_TOASTS = 5;

let listeners: Array<(toasts: ToastMessage[]) => void> = [];
let toasts: ToastMessage[] = [];

function emit(updated: ToastMessage[]) {
  toasts = updated;
  listeners.forEach((l) => l(updated));
}

export function toast(opts: Omit<ToastMessage, "id">) {
  const id = Math.random().toString(36).slice(2);
  emit([...toasts, { id, ...opts }].slice(-MAX_TOASTS));
}

/** Remove one toast. The only way a toast goes away. */
export function dismissToast(id: string) {
  emit(toasts.filter((t) => t.id !== id));
}

export function dismissAllToasts() {
  emit([]);
}

export function useToastState() {
  const [state, setState] = useState<ToastMessage[]>(toasts);
  const subscribe = useCallback((updater: (t: ToastMessage[]) => void) => {
    listeners.push(updater);
    return () => { listeners = listeners.filter((l) => l !== updater); };
  }, []);

  useEffect(() => subscribe(setState), [subscribe]);

  return state;
}
