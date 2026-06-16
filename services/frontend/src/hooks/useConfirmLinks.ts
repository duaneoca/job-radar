import { useEffect, useState } from "react";

// Whether to show a confirmation (showing the full URL) before opening an
// agent-derived link in a new tab. Default ON — these links are attacker-
// controlled (email content), so safety-first. Stored per-browser.
const KEY = "jr-confirm-external-links";
const EVENT = "jr-confirm-links-change";

export function getConfirmLinks(): boolean {
  return localStorage.getItem(KEY) !== "0"; // default (unset) = true (ask)
}

export function setConfirmLinks(value: boolean): void {
  localStorage.setItem(KEY, value ? "1" : "0");
  window.dispatchEvent(new Event(EVENT)); // notify other hook instances in this tab
}

/** Reactive preference — stays in sync between the Settings toggle and the
 *  dialog's "Don't ask again" checkbox within the same tab. */
export function useConfirmLinks(): [boolean, (v: boolean) => void] {
  const [value, setValue] = useState(getConfirmLinks);
  useEffect(() => {
    const handler = () => setValue(getConfirmLinks());
    window.addEventListener(EVENT, handler);
    return () => window.removeEventListener(EVENT, handler);
  }, []);
  return [value, setConfirmLinks];
}
