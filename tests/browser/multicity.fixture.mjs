// Two-city fixture (built by run.sh): city pickers on every page, and links that stay within a city.
import { connect, sleep, checker } from "./cdp.mjs";
const { check, finish } = checker("multi-city");
const c = await connect(); await c.size(1100, 1200);
await c.ev(`try { localStorage.removeItem("city") } catch (e) {}`);
await c.go("index.html");
const a = await c.ev(`({picker: !document.getElementById("city-picker").hidden, options: [...document.getElementById("city").options].map(o => o.textContent), n: document.querySelectorAll("#list ul.meetings > li").length})`);
check("index shows a city picker with both cities", a.picker && a.options.length === 2, a);
await c.ev(`document.getElementById("city").value = "fort-collins"; document.getElementById("city").dispatchEvent(new Event("change"))`); await sleep(300);
const b = await c.ev(`({n: document.querySelectorAll("#list ul.meetings > li").length, url: location.search, href: document.querySelector("#list ul.meetings > li > a").getAttribute("href"), years: document.getElementById("year").options.length})`);
check("switching city lists only that city's meetings", b.n === 3 && b.url.includes("city=fort-collins") && b.href.includes("city=fort-collins"), b);
check("year options follow the city", b.years === 2, b); // "All" + the fixture city's single year
await c.go(b.href, 2500);
const m = await c.ev(`({meta: document.getElementById("meta").textContent, back: document.getElementById("back").getAttribute("href"), topics: document.querySelectorAll(".topic").length, chip: document.querySelector(".topic .tags a")?.getAttribute("href"), neighbors: document.getElementById("neighbors").innerText.length > 0})`);
check("meeting page shows the city and stays within it", /Fort Collins/.test(m.meta) && m.back.includes("city=fort-collins") && m.topics > 0 && (m.chip || "").includes("city=fort-collins"), m);
await c.go("trends.html");
const t = await c.ev(`({picker: !document.getElementById("city-picker").hidden, tile: document.querySelector(".tile").innerText.replace(/\\n/g," ")})`);
await c.ev(`document.getElementById("city").value = "fort-collins"; document.getElementById("city").dispatchEvent(new Event("change"))`); await sleep(300);
const t2 = await c.ev(`document.querySelector(".tile").innerText.replace(/\\n/g," ")`);
check("trends recomputes per city", t.picker && t.tile !== t2 && /^3 /.test(t2), { before: t.tile, after: t2 });
await c.go("issue.html?city=fort-collins&tag=budget");
const i = await c.ev(`({picker: !document.getElementById("city-picker").hidden, city: document.getElementById("city").value, tiles: document.querySelectorAll(".tile")[1].innerText.replace(/\\n/g," "), items: document.querySelectorAll(".discussion").length})`);
check("issue page respects the city", i.picker && i.city === "fort-collins" && /of 3/.test(i.tiles) && i.items > 0, i);
await c.go("index.html");
check("chosen city is remembered on return", await c.ev(`document.getElementById("city").value`) === "fort-collins");
await c.ev(`try { localStorage.removeItem("city") } catch (e) {}`);
finish(c);
