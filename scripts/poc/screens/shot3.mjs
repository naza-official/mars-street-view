import { chromium } from "playwright-core";
const [url, outDir] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--use-angle=metal", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await page.goto(url);
await page.waitForFunction(() => window.mars && window.mars.tilesets.length === 2, null, { timeout: 30000 });
const go = (args) => page.evaluate(([lat, lon, h, az, el, hideL1]) => {
  const m = window.mars;
  m.tilesets[0].group.visible = !hideL1;
  m.placeCamera({ lat, lon, height: h }, az, el);
}, args);
const shots = [
  ["06_mesh_only", [18.444627 - 0.0022, 77.450886, -4252.8 + 60, 0, -25, true]],
  ["07_mesh_on_l1", [18.444627 - 0.0022, 77.450886, -4252.8 + 60, 0, -25, false]],
];
for (const [name, args] of shots) {
  await go(args);
  await page.waitForTimeout(12000);
  await go(args);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${outDir}/${name}.png` });
  console.log(name, JSON.stringify(await page.evaluate(() => window.mars.tilesets.map((t) => t.visibleTiles.size))));
}
await browser.close();
