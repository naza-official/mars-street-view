import { chromium } from "playwright-core";
const [url, outDir] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true,
  args: ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const logs = [];
page.on("console", (m) => logs.push(`${m.type()}: ${m.text()}`));
page.on("pageerror", (e) => logs.push(`pageerror: ${e.message}`));
await page.goto(url);
await page.waitForTimeout(4000);
await page.screenshot({ path: `${outDir}/01_globe.png` });
const state = async () => page.evaluate(() => {
  const m = window.mars; const c = m.cameraCartographic();
  return { cam: c, tiles: m.tilesets.map((t) => ({ url: t.rootURL, visible: t.visibleTiles.size, loaded: t.stats?.loaded ?? null })),
    layers: [...document.querySelectorAll(".layers li")].map((li) => li.textContent) };
});
console.log("globe", JSON.stringify(await state()));
for (const [id, wait, name] of [["jezero", 9000, "02_jezero"], ["oeb", 9000, "03_oeb"]]) {
  await page.click(`button[data-site=${id}]`);
  await page.waitForTimeout(wait);
  await page.mouse.move(700, 450);
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${outDir}/${name}.png` });
  console.log(id, JSON.stringify(await state()), "readout:", await page.textContent(".readout"));
}
console.log(logs.filter((l) => !l.startsWith("debug")).slice(0, 20).join("\n"));
await browser.close();
