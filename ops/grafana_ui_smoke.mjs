import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(
  new URL("../cloud_agent/front/cloud_agent/package.json", import.meta.url),
);
const { chromium } = require("@playwright/test");

const args = process.argv.slice(2);
const option = (name, fallback = "") => {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] || "" : fallback;
};

const grafanaUrl = option("--grafana-url", "http://127.0.0.1:3000").replace(/\/$/, "");
const dashboardUid = option("--dashboard-uid", "cloud-agent-overview");
const outputDir = option("--output-dir", ".codex-run/grafana-ui");
const user = option("--user", process.env.GRAFANA_USER || "");
const password = option("--password", process.env.GRAFANA_PASSWORD || "");

if (!user || !password) {
  console.error(JSON.stringify({ status: "failed", error: "MissingGrafanaCredentials" }));
  process.exit(2);
}

const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
const screenshotPath = path.resolve(
  outputDir,
  `grafana-${dashboardUid}-${timestamp}.png`,
);
const dashboardUrl = `${grafanaUrl}/d/${encodeURIComponent(dashboardUid)}`;

async function loginIfNeeded(page) {
  await page.waitForTimeout(2500);
  if (!(await page.locator('input[name="user"]').count())) {
    return;
  }

  await page.locator('input[name="user"]').fill(user);
  await page.locator('input[name="password"]').fill(password);
  await page.locator('button[type="submit"]').click();
  await page.waitForTimeout(2000);

  const skip = page.getByText("Skip", { exact: true });
  if (await skip.count()) {
    await skip.click();
  }
}

async function run() {
  await mkdir(path.dirname(screenshotPath), { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 1,
  });

  try {
    await page.goto(dashboardUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
    await loginIfNeeded(page);
    await page.goto(dashboardUrl, { waitUntil: "domcontentloaded", timeout: 30000 });

    await page.getByText("Cloud Agent Overview", { exact: true }).waitFor({ timeout: 30000 });
    const llmCalls = page.getByText("LLM calls", { exact: true });
    await llmCalls.waitFor({ timeout: 30000 });
    await page.getByText("MCP tool calls", { exact: true }).waitFor({ timeout: 30000 });
    await llmCalls.scrollIntoViewIfNeeded();
    await page.waitForTimeout(5000);
    await page.screenshot({ path: screenshotPath });

    console.log(
      JSON.stringify({
        status: "pass",
        dashboard_uid: dashboardUid,
        screenshot: path.relative(process.cwd(), screenshotPath),
      }),
    );
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(JSON.stringify({ status: "failed", error: error?.name || "Error" }));
  process.exit(1);
});
