// Meeting page: topics, tags, timeline, seeking, transcript excerpt and full transcript.
import { connect, sleep, checker, LOCAL } from "./cdp.mjs";
const { check, finish } = checker("meeting");
const c = await connect();
await c.size(1100, 1200);
await c.go("index.html");
const href = await c.ev(`[...document.querySelectorAll("#list ul.meetings > li > a")].map((a) => a.getAttribute("href"))[2]`);
await c.go(href, 3500);
// Local runs fetch transcripts from the local serve_transcripts.py started by run.sh.
if (LOCAL) await c.ev(`window.COUNCIL_CONFIG.transcriptHost = "http://localhost:8921"`);

const page = await c.ev(`({title: document.getElementById("title").textContent, overview: !document.getElementById("overview").hidden, topics: document.querySelectorAll(".topic").length,
  tags: document.querySelectorAll("#meeting-tag-list .tag").length, blocks: document.querySelectorAll("#timeline .block").length, player: !!document.querySelector("iframe#player"),
  model: document.getElementById("model").textContent, copy: document.querySelectorAll("button.copy").length, neighbors: !document.getElementById("neighbors").hidden,
  excerpts: document.querySelectorAll(".excerpt-toggle").length})`);
check("title, overview and topics render", page.title && page.overview && page.topics > 0, page);
check("YouTube player loads (allowed by the CSP)", page.player);
check("tags, timeline, copy links and neighbors render", page.tags > 0 && page.blocks > 0 && page.copy > 0 && page.neighbors, page);
check("model credit is filled in", page.model && page.model !== "an AI model", page.model);

await c.click("#meeting-tag-list .tag"); await sleep(200);
const dim = await c.ev(`({dim: document.querySelectorAll(".topic.dim").length, note: document.getElementById("tag-note").textContent})`);
check("selecting a tag highlights its topics", /tagged/.test(dim.note), dim);
await c.click("#meeting-tag-list .tag");

await c.click("#timeline .block", 1); await sleep(2500);
const seek = await c.ev(`({hash: location.hash, playing: document.querySelectorAll(".topic.playing").length, on: document.querySelectorAll("#timeline .block.on").length})`);
check("timeline click seeks and marks the playing topic", /#t=\d+/.test(seek.hash) && seek.playing === 1 && seek.on === 1, seek);

if (page.excerpts) {
  await c.click(".excerpt-toggle", 1); await sleep(3500);
  const ex = await c.ev(`({paras: document.querySelectorAll(".topic")[1].querySelectorAll(".excerpt p").length, button: document.querySelectorAll(".excerpt-toggle")[1].textContent})`);
  check("Read the transcript shows the topic's captions", ex.paras > 0 && /Hide/.test(ex.button), ex);
  await c.click("#load-transcript"); await sleep(2500);
  check("full transcript loads", await c.ev(`document.querySelectorAll(".seg").length`) > 100);
} else {
  console.log("  skip transcript checks (no transcriptHost configured)");
}

await c.size(390, 1200); await c.go(href, 2500);
check("no horizontal scroll at phone width", !(await c.overflow()));
finish(c);
