import { chromium } from "playwright-core";
const [url, outDir] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--use-angle=metal", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const logs = [];
page.on("pageerror", (e) => logs.push(`pageerror: ${e.message}`));
await page.goto(url);
await page.waitForSelector("button[data-action=plan]", { timeout: 40000 });
const demo = await page.evaluate(() => window.planner.planBetween([77.45088, 18.44463, 0, 0], [77.44229, 18.43358, 0, 0]));
console.log("demo:", demo);
const go = (a) => page.evaluate(([lat, lon, h, az, el]) => window.mars.placeCamera({ lat, lon, height: h }, az, el), a);
await go([18.4475, 77.4363, -4250 + 4200, 0, -90]);
await page.waitForTimeout(12000);
await go([18.4475, 77.4363, -4250 + 4200, 0, -90]);
await page.click("button[data-action=plan]");
for (const [x, y] of [[480, 720], [1000, 420]]) {
  await page.mouse.move(x, y); await page.mouse.down(); await page.mouse.up();
  await page.waitForTimeout(1500);
  console.log("click", x, y, "->", await page.textContent(".plan-result"));
}
await page.screenshot({ path: `${outDir}/22_plan_top.png` });
await go([18.4200, 77.4363, -4250 + 2200, 10, -35]);
await page.waitForTimeout(10000);
await page.screenshot({ path: `${outDir}/23_plan_oblique.png` });
console.log(logs.join("\n") || "no page errors");
await browser.close();
