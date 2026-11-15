import { chromium } from "playwright-core";
const [url, outDir] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--use-angle=metal", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const logs = [];
page.on("console", (m) => logs.push(`${m.type()}: ${m.text()}`));
page.on("pageerror", (e) => logs.push(`pageerror: ${e.message}`));
await page.goto(url);
await page.waitForFunction(() => window.mars && window.mars.tilesets.length === 3 && document.querySelector("button[data-stop]"), null, { timeout: 30000 });
const vis = () => page.evaluate(() => window.mars.tilesets.map((t) => `${t.rootURL.split("/").slice(-3, -1).join("/")}:${t.visibleTiles.size}`));
const pose = (a) => page.evaluate(([lat, lon, h, az, el]) => window.mars.placeCamera({ lat, lon, height: h }, az, el), a);
const settle = async (a, ms) => { await pose(a); await page.waitForTimeout(ms); await pose(a); await page.waitForTimeout(1500); };
await page.waitForTimeout(6000);
await page.screenshot({ path: `${outDir}/08_start.png` });
console.log("start", JSON.stringify(await vis()));
const S = [18.444627, 77.450886, -4252.8];
const views = [
  ["09_l2_overview", [S[0] - 0.06, S[1], S[2] + 4000, 0, -30]],
  ["10_stop_on_l2", [S[0] - 0.0016, S[1] - 0.0004, S[2] + 35, 10, -22]],
];
for (const [name, a] of views) {
  await settle(a, 15000);
  await page.screenshot({ path: `${outDir}/${name}.png` });
  console.log(name, JSON.stringify(await vis()));
}
await page.click("button[data-stop]");
await page.waitForTimeout(4000);
for (const [name, az] of [["11_street_north", 0], ["12_street_south", 180]]) {
  await page.evaluate((az) => { const m = window.mars; m.lookPano(az - (m.pano?.az ?? 0), -8 - (m.pano?.el ?? 0)); }, az);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${outDir}/${name}.png` });
  console.log(name, await page.evaluate(() => window.mars.inPano));
}
console.log(logs.filter((l) => /error|warn/i.test(l) && !/1\.1 or higher/.test(l)).slice(0, 10).join("\n"));
await browser.close();
