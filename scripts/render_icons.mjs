// Run from the repo root: node scripts/render_icons.mjs
// Uses the lockfile's Playwright and Chrome for Testing (PW_CHROMIUM_PATH may
// select it). The committed vector has no fonts, network or platform assets.
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const require = createRequire(new URL("../src/aleph/web/frontend/package.json", import.meta.url));
const { chromium } = require("@playwright/test");
const directory = new URL("../src/aleph/web/frontend/public/icons/", import.meta.url);
const svg = await readFile(new URL("aleph.svg", directory), "utf8");
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM_PATH || undefined });
try {
  for (const [file, size] of [["icon-192.png", 192], ["icon-512.png", 512], ["icon-maskable-512.png", 512], ["apple-touch-icon.png", 180]]) {
    const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
    await page.setContent(`<style>html,body{margin:0;background:#161826}svg{width:100%;height:100%;display:block}</style>${svg}`);
    await page.screenshot({ path: fileURLToPath(new URL(file, directory)), omitBackground: false });
    await page.close();
  }
  console.log(`Rendered icons with Chromium ${browser.version()}`);
} finally {
  await browser.close();
}
