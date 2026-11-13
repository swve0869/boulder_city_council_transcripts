// Trends, Issues and About pages.
import { connect, sleep, checker } from "./cdp.mjs";
const { check, finish } = checker("trends");
const c = await connect();
await c.size(1100, 2400);
await c.go("trends.html");

const t = await c.ev(`({tiles: document.querySelectorAll(".tile").length, bars: document.querySelectorAll("#rank .track").length, mix: document.querySelectorAll("#mix .seg").length,
  heat: document.querySelectorAll("#heat .cell").length, pairs: document.querySelectorAll("#pairs .track").length, change: document.querySelectorAll("#change .track").length,
  changeHidden: document.getElementById("change-section").hidden, link: document.querySelector("#rank .label")?.getAttribute("href")})`);
check("headline tiles and ranked bars render", t.tiles === 4 && t.bars > 5, t);
check("monthly mix, heatmap and co-occurrence render", t.mix > 0 && t.heat > 0 && t.pairs > 0, t);
check("year comparison renders when two years exist", t.changeHidden || t.change > 0, t);
check("bars link to the issue page", /^issue\.html\?/.test(t.link || ""), t.link);

for (const key of ["rank", "mix", "heat", "pairs"]) {
  await c.ev(`document.querySelector('[data-table="${key}"]').click()`); await sleep(150);
  check(`${key}: table view`, await c.ev(`document.querySelectorAll("#${key} table tbody tr").length`) > 0);
  await c.ev(`document.querySelector('[data-table="${key}"]').click()`);
}
await c.ev(`document.getElementById("metric").value = "meetings"; document.getElementById("metric").dispatchEvent(new Event("change"))`); await sleep(150);
check("measure switch redraws", await c.ev(`/meeting/.test(document.querySelector("#rank .val").textContent)`));

await c.go("issue.html?tag=police");
const i = await c.ev(`({title: document.getElementById("title").textContent, cols: document.querySelectorAll(".col").length, items: document.querySelectorAll(".discussion").length,
  text: [...document.querySelectorAll(".discussion")].filter((d) => d.querySelector("p:not(.meta)")).length, link: document.querySelector(".discussion .stamp")?.getAttribute("href"),
  related: document.querySelectorAll("#related .tag").length})`);
check("issue page lists every discussion with summaries", i.title === "Police" && i.items > 0 && i.text === i.items, i);
check("issue page charts months and links to moments", i.cols > 0 && /#t=\d+/.test(i.link || ""), i);
await c.ev(`document.getElementById("tag").value = "flock"; document.getElementById("tag").dispatchEvent(new Event("change"))`); await sleep(200);
check("switching issue updates the page and URL", await c.ev(`document.getElementById("title").textContent === "Flock cameras" && location.search.includes("tag=flock")`));

await c.go("about.html");
check("about page renders", await c.ev(`document.querySelectorAll("article h2").length`) >= 4);

await c.size(390, 2000);
for (const p of ["trends.html", "issue.html?tag=police", "about.html"]) { await c.go(p); check(`${p}: no horizontal scroll at phone width`, !(await c.overflow())); }
await c.size(1100, 1200, true); await c.go("trends.html");
check("dark mode renders charts", await c.ev(`document.querySelectorAll("#rank .track").length`) > 0);

// Theme switch: system (dark here) -> light -> dark, remembered across pages.
const bg = () => c.ev(`getComputedStyle(document.body).backgroundColor`);
const systemDark = await bg();
await c.click("button.theme"); await sleep(100);
const light = await bg();
check("theme button overrides a dark system setting with light", systemDark !== light && (await c.ev(`document.documentElement.dataset.theme`)) === "light", { systemDark, light });
await c.go("index.html");
check("chosen theme persists on other pages", (await bg()) === light && /light/.test(await c.ev(`document.querySelector("button.theme").textContent`)));
await c.click("button.theme"); await sleep(100);
check("dark choice applies", (await bg()) === systemDark);
await c.click("button.theme"); // back to system for later runs
check("returns to system", (await c.ev(`document.documentElement.dataset.theme`)) === undefined);
finish(c);
