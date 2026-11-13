// Meetings page: list, tag filter, search, sorting, previews, shareable URL state.
import { connect, sleep, checker } from "./cdp.mjs";
const { check, finish } = checker("index");
const c = await connect();
await c.size(1100, 1300);
await c.go("index.html");

const first = await c.ev(`({meetings: document.querySelectorAll("#list ul.meetings > li").length, chips: document.querySelectorAll("#tag-groups .tag").length,
  rowChips: document.querySelectorAll("#list .tags .tag").length, meta: document.querySelector(".row-meta")?.textContent, years: document.querySelectorAll("#list h2").length})`);
check("lists meetings grouped by year", first.meetings > 0 && first.years > 0, first);
check("tag panel and row tags render", first.chips > 10 && first.rowChips > 0, first);
check("rows show topic count", /topic/.test(first.meta || ""), first.meta);

await c.click(".row-meta button"); await sleep(900);
const open = await c.ev(`({items: document.querySelectorAll("ol.preview li").length, text: document.querySelectorAll("ol.preview p").length})`);
check("Show topics previews topics with summaries", open.items > 0 && open.text > 0, open);
await c.click(".row-meta button");

await c.ev(`[...document.querySelectorAll("#tag-groups .tag")].find((b) => b.textContent.startsWith("Police")).click()`); await sleep(300);
const filtered = await c.ev(`({url: location.search, shown: document.querySelectorAll("#list ul.meetings > li").length, segs: document.querySelectorAll(".segments > li").length,
  href: document.querySelector(".segments a")?.getAttribute("href"), active: document.getElementById("active").textContent})`);
check("tag filter narrows the list and updates the URL", filtered.shown > 0 && filtered.shown < first.meetings && filtered.url.includes("tags=police"), filtered);
check("filtered rows link to matching segments", filtered.segs > 0 && /meeting\.html\?city=.*#t=\d+/.test(filtered.href || ""), filtered.href);

await c.ev(`document.getElementById("sort").value = "relevance"; document.getElementById("sort").dispatchEvent(new Event("change"))`); await sleep(200);
check("relevance sort produces a flat list", await c.ev(`document.querySelectorAll("#list h2").length`) === 0);

await c.go("index.html");
await c.type("#q", "flock"); await sleep(1000);
const search = await c.ev(`({shown: document.querySelectorAll("#list ul.meetings > li").length, marks: document.querySelectorAll("mark").length})`);
check("search matches summary text and highlights it", search.shown > 0 && search.marks > 0, search);
await c.type("#q", "zzzznotaword"); await sleep(400);
check("search with no match shows an empty state", await c.ev(`document.querySelectorAll("#list ul.meetings > li").length`) === 0);

await c.go("index.html?tags=budget&q=wildfire", 2500);
const deep = await c.ev(`document.getElementById("active").textContent`);
check("filters restore from the URL", /Budget/.test(deep) && /wildfire/.test(deep), deep);

await c.size(390, 1200); await c.go("index.html");
check("no horizontal scroll at phone width", !(await c.overflow()));
finish(c);
