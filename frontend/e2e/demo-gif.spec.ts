import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// The README GIF (docs/media/demo.gif) from the seeded one-command stack. Skipped unless
// E2E_GIF=1; needs ffmpeg on PATH:
//   scripts/demo-up.sh
//   cd frontend && E2E_GIF=1 E2E_BASE_URL=http://127.0.0.1:8080 npx playwright test demo-gif
// One browser video is recorded; the test notes when each page is fully loaded and ffmpeg keeps
// only those stretches: the s06 verdict (~3 s), one citation click, then the s08 abstention
// (~3 s). Page loads and the session splash are cut out.
const OUT = "../docs/media/demo.gif";
const WIDTH = 1100;
// The video clock and the test's wall clock differ by a few hundred milliseconds (the video
// lags). Each kept stretch starts SETTLE s after its mark, and the page stays put for PAD ms after
// each end mark, so neither end of a cut can reach a page load.
const SETTLE = 0.4;
const PAD = 1_500;

test.skip(process.env.E2E_GIF !== "1", "set E2E_GIF=1 to regenerate the README GIF");

interface Inv {
  id: string;
  status: string;
  source: { kind?: string; scenario?: string };
}

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@example.com");
  await page.getByRole("button", { name: "Sign in (development)" }).click();
  await expect(page.getByRole("link", { name: "Incidents" }).first()).toBeVisible();
}

/** The seeded investigation of a scenario, once the worker has completed it. */
async function seeded(page: Page, scenario: string): Promise<string> {
  let id = "";
  await expect(async () => {
    const r = await page.request.get("/api/v1/investigations?limit=100");
    expect(r.ok()).toBe(true);
    const body = (await r.json()) as { items: Inv[] };
    const inv = body.items.find((i) => i.source.scenario === scenario && i.status === "completed");
    expect(inv, `completed seeded ${scenario} investigation`).toBeTruthy();
    id = inv?.id ?? "";
  }).toPass({ timeout: 120_000 });
  return id;
}

/** Waits until nothing on the investigation page is still loading, then puts the verdict panel
 *  at the top, below the sticky navigation bar. */
async function showVerdict(page: Page) {
  const heading = page.getByRole("heading", { name: /Deterministic verdict/ });
  await expect(heading).toBeVisible();
  await expect(page.locator("p.loading")).toHaveCount(0);
  await page.waitForLoadState("networkidle");
  await heading.evaluate((el) => {
    const panel = el.closest("section") ?? el;
    window.scrollTo(0, panel.getBoundingClientRect().top + window.scrollY - 56);
  });
}

test("README GIF", async ({ browser, baseURL }) => {
  test.setTimeout(240_000);
  const setup = await browser.newContext({ baseURL });
  const sp = await setup.newPage();
  await signIn(sp);
  const confirmed = await seeded(sp, "s06-feature-flag-failure");
  const abstained = await seeded(sp, "s08-insufficient-evidence");
  const storageState = await setup.storageState();
  await setup.close();

  const dir = mkdtempSync(path.join(tmpdir(), "aegis-gif-"));
  const size = { width: 1366, height: 900 };
  const ctx = await browser.newContext({
    baseURL,
    storageState,
    viewport: size,
    recordVideo: { dir, size },
  });
  const page = await ctx.newPage();
  const t0 = Date.now();
  const at = () => (Date.now() - t0) / 1000;
  const cuts: [number, number][] = [];

  // 1. The s06 verdict: readable headline, checks with cited evidence.
  await page.goto(`/investigations/${confirmed}`);
  await expect(page.locator("article.finding")).toContainText("Modality agreement");
  await expect(page.getByRole("article", { name: /^Evidence / }).first()).toBeAttached();
  await showVerdict(page);
  await page.waitForTimeout(500);
  const s06Start = at() + SETTLE;
  await page.waitForTimeout(3_400);

  // 2. One citation click: the flag change behind the "Change scope" check. Videos carry no
  //    mouse pointer, so a ring marks the button for a moment before the click.
  const citation = page
    .locator("article.finding li", { hasText: "Change scope" })
    .getByRole("button", { name: /^Show evidence / })
    .first();
  const evidenceId = ((await citation.textContent()) ?? "").trim();
  expect(evidenceId).not.toBe("");
  await citation.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const ring = document.createElement("div");
    ring.id = "gif-click-ring";
    Object.assign(ring.style, {
      position: "fixed",
      left: `${r.left + r.width / 2 - 20}px`,
      top: `${r.top + r.height / 2 - 20}px`,
      width: "40px",
      height: "40px",
      borderRadius: "50%",
      border: "3px solid #d6409f",
      background: "rgba(214, 64, 159, 0.15)",
      pointerEvents: "none",
      zIndex: "9999",
    });
    document.body.appendChild(ring);
  });
  await page.waitForTimeout(1_000);
  await page.evaluate(() => document.getElementById("gif-click-ring")?.remove());
  await citation.click();
  const cited = page.getByRole("article", { name: `Evidence ${evidenceId}` }).first();
  await expect(cited).toHaveClass(/evidence-highlight/);
  await expect(cited).toBeInViewport();
  await page.waitForTimeout(2_600);
  cuts.push([s06Start, at()]);
  await page.waitForTimeout(PAD);

  // 3. The s08 abstention: no root cause, next checks instead.
  await page.goto(`/investigations/${abstained}`);
  await expect(page.getByRole("heading", { name: "Next checks" })).toBeVisible();
  await showVerdict(page);
  await page.waitForTimeout(500);
  const s08Start = at() + SETTLE;
  await page.waitForTimeout(3_400);
  cuts.push([s08Start, at()]);
  await page.waitForTimeout(PAD);

  const video = page.video();
  await ctx.close();
  expect(video).not.toBeNull();
  const webm = (await video?.path()) ?? "";
  const total = cuts.reduce((sum, [a, b]) => sum + (b - a), 0);
  expect(total).toBeLessThanOrEqual(15);

  const filter = [
    `[0:v]split=${cuts.length}${cuts.map((_, i) => `[v${i}]`).join("")}`,
    ...cuts.map(([a, b], i) => `[v${i}]trim=start=${a.toFixed(2)}:end=${b.toFixed(2)},setpts=PTS-STARTPTS[c${i}]`),
    `${cuts.map((_, i) => `[c${i}]`).join("")}concat=n=${cuts.length}:v=1:a=0,fps=10,scale=${WIDTH}:-1:flags=lanczos,split[g0][g1]`,
    "[g0]palettegen=stats_mode=diff[pal]",
    "[g1][pal]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
  ].join(";");
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-i", webm, "-filter_complex", filter, "-loop", "0", OUT], {
    stdio: "inherit",
  });
  rmSync(dir, { recursive: true, force: true });
  console.log(`demo.gif: ${total.toFixed(1)} s, ${(statSync(OUT).size / 1e6).toFixed(2)} MB, cuts ${JSON.stringify(cuts)}`);
  expect(statSync(OUT).size).toBeLessThanOrEqual(6_000_000);
});
