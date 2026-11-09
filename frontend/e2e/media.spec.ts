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

/** The seeded investigation of a scenario (aegis admin seed-demo), once the worker has completed
 *  it: `up --wait` does not wait for the one-shot seed or the worker. */
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

/** Scrolls the verdict panel to the top, below the sticky navigation bar. */
async function scrollToVerdict(page: Page) {
  const heading = page.getByRole("heading", { name: /Deterministic verdict/ });
  await expect(heading).toBeVisible();
  await expect(page.locator("p.loading")).toHaveCount(0);
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

  const confirmed = await seeded(page, "s06-feature-flag-failure");
  await page.goto(`/investigations/${confirmed}`);
  await expect(page.locator("article.finding")).toContainText("Modality agreement");
  await scrollToVerdict(page);
  await shoot(page, "rca-verdict");

  const abstained = await seeded(page, "s08-insufficient-evidence");
  await page.goto(`/investigations/${abstained}`);
  await scrollToVerdict(page);
  await shoot(page, "abstention");
});
