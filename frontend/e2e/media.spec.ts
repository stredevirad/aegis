import { expect, test, type Page } from "@playwright/test";

// README screenshots (docs/media/*.png) from the seeded one-command stack. Skipped unless
// E2E_MEDIA=1, so the regular E2E run is unaffected:
//   scripts/demo-up.sh
//   cd frontend && E2E_MEDIA=1 E2E_BASE_URL=http://127.0.0.1:8080 npx playwright test media
const OUT = "../docs/media";

test.skip(process.env.E2E_MEDIA !== "1", "set E2E_MEDIA=1 to regenerate the README screenshots");

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

/** The completed investigation of a seeded scenario (aegis admin seed-demo). */
async function seeded(page: Page, scenario: string): Promise<string> {
  const r = await page.request.get("/api/v1/investigations?limit=100");
  expect(r.ok()).toBe(true);
  const body = (await r.json()) as { items: Inv[] };
  const inv = body.items.find((i) => i.source.scenario === scenario && i.status === "completed");
  expect(inv, `seeded ${scenario} investigation`).toBeTruthy();
  return inv!.id;
}

/** Scrolls the verdict panel to the top, below the sticky navigation bar. */
async function scrollToVerdict(page: Page) {
  const heading = page.getByRole("heading", { name: /Deterministic verdict/ });
  await expect(heading).toBeVisible();
  await heading.evaluate((el) => {
    const panel = el.closest("section") ?? el;
    window.scrollTo(0, panel.getBoundingClientRect().top + window.scrollY - 56);
  });
}

async function shoot(page: Page, name: string) {
  await page.waitForTimeout(400); // let charts and badges settle
  await page.screenshot({ path: `${OUT}/${name}.png` });
}

test("README screenshots", async ({ page }) => {
  await signIn(page);
  await page.waitForTimeout(1000);
  await shoot(page, "dashboard");

  const confirmed = await seeded(page, "s06-feature-flag-failure");
  await page.goto(`/investigations/${confirmed}`);
  await scrollToVerdict(page);
  await shoot(page, "rca-verdict");

  await page.goto(`/investigations/${confirmed}/evidence`);
  await expect(page.getByRole("article", { name: /^Evidence / }).first()).toBeVisible();
  await shoot(page, "evidence-explorer");

  const abstained = await seeded(page, "s08-insufficient-evidence");
  await page.goto(`/investigations/${abstained}`);
  await scrollToVerdict(page);
  await shoot(page, "abstention");
});
