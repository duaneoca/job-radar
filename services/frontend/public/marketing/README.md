# Marketing screenshots

These images are shown (wrapped in the `BrowserFrame` mockup) on the public landing
page (`src/pages/LandingPage.tsx`). The `.svg` files here are **placeholders** — replace
each with a real screenshot.

## How to capture

1. Log into the **demo account** (`testuser@duanesworld.org`) on staging.
2. Set the app to **dark mode** (avatar menu → theme → Dark).
3. Capture **app content only — no browser chrome** (the landing page adds its own
   framed address bar showing `job-radar.net`). On macOS: `Cmd+Shift+4` then drag, or
   `Cmd+Shift+4` then Space to grab a window without shadow.
4. Also grab a **mobile** width capture where useful (phone or a 390px browser window).

## Files to replace (keep the same names, or update the `SHOTS` paths in LandingPage.tsx)

| File | Screen to capture |
|------|-------------------|
| `jobs-list.svg` → `jobs-list.png` | The Jobs list with scored postings + a filter or the 5-dimension score detail |
| `job-detail.svg` → `job-detail.png` | A job detail page — a populated tab (Research / Application / Interview Prep) |
| `resume-tailor.svg` → `resume-tailor.png` | The résumé tailor diff review (or the templated PDF preview) |
| `capture.svg` → `capture.png` | The bookmarklet / Add-Job page (the "📡 Add to Job Radar" flow) |

After dropping in real files, update the extensions in the `SHOTS` array in
`src/pages/LandingPage.tsx` (`.svg` → `.png`).
