# Landing page screenshots

The public landing page (`services/frontend/src/pages/LandingPage.tsx`) shows four
screenshots of the app. Each one exists twice, light and dark, and the page shows the set
that matches the visitor's theme, including after they flip the toggle in its header. The
dark jobs list is also the link-preview image (`og:image` / `twitter:image` in
`services/frontend/index.html`).

| Image | Screen | Captured from |
|---|---|---|
| `jobs-list-{light,dark}.png` | Jobs list, Reviewed only | `/jobs` |
| `job-detail-{light,dark}.png` | A job, Research tab | `/jobs/<id>` |
| `resume-tailor-{light,dark}.png` | Tailored résumé review | `/jobs/<id>/tailor` |
| `capture-{light,dark}.png` | Add Job, filled in, not submitted | `/jobs/add` |

They live in `services/frontend/public/marketing/`. Everything in `public/` is served on
the site, so keep notes like this one out of that folder.

## Retaking them

Use the demo account on staging: Alex Rivera, a nuclear engineer in Newport News, VA.
`scripts/seed_demo.py` sets that persona up from scratch, but it rarely needs to; scraping
and scoring keep the account current.

1. **Pick a showcase job.** Sign in to staging as the demo account, filter the jobs list to
   Reviewed, and open a top-scoring job. Run **Research** and **Tailor résumé** on it. Copy
   its id from the address bar: `/jobs/<id>`.
2. **Install the script's one dependency** (first time only). It drives your installed
   Chrome, so there's no browser download.
   ```bash
   cd scripts/marketing-screenshots
   npm ci
   ```
3. **Sign in for the script.** This opens Chrome on the staging sign-in page. Sign in by
   hand. The script saves only the session cookie, to your temp folder, never the password,
   and never in the repo.
   ```bash
   node capture.mjs login
   ```
4. **Capture all eight.**
   ```bash
   node capture.mjs shots --job <id>
   ```
   Each image is 2880 × 1800: a 1440 × 900 window at 2× for sharp text on retina screens.
   The script closes the release banner, filters the list to Reviewed, opens the Research
   tab, and fills Add Job from another job on the account. It prefers The Muse, which sends
   the whole posting; Adzuna only sends a snippet.
5. **Look at every image** before committing. A blank tab, an error message or a stray
   banner is easy to miss in a file list. To see them in place, run the frontend
   (`npm run dev` in `services/frontend`), open `/` signed out, and flip the theme toggle.
6. **Delete the saved session.**
   ```bash
   node capture.mjs logout
   ```
7. **Commit the images** in a PR. Same file names, so nothing else changes.

If the script says the session expired, run `login` again.

## Changing what's shown

The screens, the window size and the job used for Add Job are all in
`scripts/marketing-screenshots/capture.mjs`. To add a screenshot, add a capture step there
and a feature (or a `SHOTS` entry) in `LandingPage.tsx`. Keep the `<name>-light.png` /
`<name>-dark.png` pair, since the page builds both paths from the name.
