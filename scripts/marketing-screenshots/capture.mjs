// Captures the landing page screenshots, light and dark, from staging.
// Guide: docs/marketing-screenshots.md
//
//   node capture.mjs login              sign in by hand; saves the session only
//   node capture.mjs shots --job <id>   capture all eight into the frontend
//   node capture.mjs logout             delete the saved session
//
// The session (a login cookie) lives in the OS temp dir, never in the repo.
import { chromium } from "playwright";
import { existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const BASE = process.env.CAPTURE_BASE ?? "https://staging.job-radar.net";
const SESSION = join(tmpdir(), "job-radar-capture-session.json");
const OUT = join(dirname(fileURLToPath(import.meta.url)), "../../services/frontend/public/marketing");
// 1440×900 at 2× → 2880×1800 images: sharp on retina, and every shot the same size.
const VIEWPORT = { width: 1440, height: 900 };
const THEMES = ["light", "dark"];

const [cmd, ...rest] = process.argv.slice(2);
const arg = (name) => { const i = rest.indexOf(`--${name}`); return i >= 0 ? rest[i + 1] : undefined; };

if (cmd === "login") await login();
else if (cmd === "shots") await shots(arg("job"));
else if (cmd === "logout") { rmSync(SESSION, { force: true }); console.log("Session deleted."); }
else { console.log("Usage: node capture.mjs login | shots --job <review id> | logout"); process.exit(1); }

async function login() {
  const browser = await chromium.launch({ channel: "chrome", headless: false });
  const ctx = await browser.newContext({ viewport: VIEWPORT });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/login`);
  console.log("Sign in to the demo account in the Chrome window…");
  await page.waitForURL((u) => !u.pathname.startsWith("/login") && u.pathname !== "/", { timeout: 600_000 });
  await ctx.storageState({ path: SESSION });
  await browser.close();
  console.log(`Signed in. Session saved to ${SESSION}`);
}

async function shots(jobId) {
  if (!jobId) throw new Error("Pass --job <review id>: the job with research and a tailored résumé.");
  if (!existsSync(SESSION)) throw new Error("No saved session. Run: node capture.mjs login");
  const browser = await chromium.launch({ channel: "chrome" });
  for (const theme of THEMES) {
    const ctx = await browser.newContext({ storageState: SESSION, viewport: VIEWPORT, deviceScaleFactor: 2, colorScheme: theme });
    await ctx.addInitScript(({ theme }) => {
      localStorage.setItem("theme", theme);
      // Open jobs only: the list sorts by score, and the best scores are often expired.
      localStorage.setItem("jr-jobs-filters", JSON.stringify({ status: ["reviewed"] }));
    }, { theme });
    const page = await ctx.newPage();
    const shot = async (name) => {
      await settle(page);
      await page.screenshot({ path: join(OUT, `${name}-${theme}.png`) });
      console.log(`  ${name}-${theme}.png`);
    };

    await page.goto(`${BASE}/jobs`);
    await page.waitForSelector("table tbody tr");
    if (new URL(page.url()).pathname.startsWith("/login")) throw new Error("Session expired. Run: node capture.mjs login");
    await shot("jobs-list");

    await page.goto(`${BASE}/jobs/${jobId}`);
    await page.getByRole("tab", { name: "Research" }).click();
    await shot("job-detail");

    await page.goto(`${BASE}/jobs/${jobId}/tailor`);
    await page.getByText("Tailored", { exact: true }).first().waitFor();
    await shot("resume-tailor");

    // Add Job, filled in from another of the account's jobs — never submitted.
    // Prefer The Muse: it sends the whole posting, where Adzuna sends a snippet.
    const sample = await page.evaluate(async (skip) => {
      const get = async (q) => (await (await fetch(`/api/jobs?${q}`, { credentials: "include" })).json()).items;
      const pool = [...await get("source=the_muse&limit=20"), ...await get("status=reviewed&limit=10")]
        .filter((j) => j.id !== skip);
      for (const j of pool) {
        const full = await (await fetch(`/api/jobs/${j.id}`, { credentials: "include" })).json();
        if ((full.description ?? "").length > 800) return full;
      }
      return (await fetch(`/api/jobs/${pool[0].id}`, { credentials: "include" })).json();
    }, jobId);
    await page.goto(`${BASE}/jobs/add`);
    await page.fill("#title", sample.title);
    await page.fill("#company", sample.company);
    if (sample.url) await page.fill("#url", sample.url);
    if (sample.location) await page.fill("#location", sample.location);
    if (sample.salary_min) await page.fill("#salMin", String(sample.salary_min));
    if (sample.salary_max) await page.fill("#salMax", String(sample.salary_max));
    if (sample.description) await page.fill("#description", sample.description);
    await page.evaluate(() => document.activeElement?.blur());
    await page.locator("#description").evaluate((el) => { el.scrollTop = 0; });
    await shot("capture");

    await ctx.close();
  }
  await browser.close();
  console.log(`Done. Review the images in ${OUT}, then commit them.`);
}

// Close the release / key banners, wait for the network, and let animations end.
async function settle(page) {
  await page.waitForLoadState("networkidle");
  for (const b of await page.getByRole("button", { name: "Dismiss" }).all()) {
    if (await b.isVisible()) await b.click();
  }
  await page.mouse.move(0, VIEWPORT.height - 1);
  await page.waitForTimeout(600);
}
