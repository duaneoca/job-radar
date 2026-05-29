import { useState, useCallback } from "react";

export interface ToastMessage {
  id: string;
  title: string;
  description?: string;
  variant?: "default" | "destructive";
}

let listeners: Array<(toasts: ToastMessage[]) => void> = [];
let toasts: ToastMessage[] = [];

function emit(updated: ToastMessage[]) {
  toasts = updated;
  listeners.forEach((l) => l(updated));
}

export function toast(opts: Omit<ToastMessage, "id">) {
  const id = Math.random().toString(36).slice(2);
  emit([...toasts, { id, ...opts }]);
  setTimeout(() => emit(toasts.filter((t) => t.id !== id)), 8000);
}

export function useToastState() {
  const [state, setState] = useState<ToastMessage[]>(toasts);
  const subscribe = useCallback((updater: (t: ToastMessage[]) => void) => {
    listeners.push(updater);
    return () => { listeners = listeners.filter((l) => l !== updater); };
  }, []);

  useState(() => {
    const unsub = subscribe(setState);
    return unsub;
  });

  return state;
}
