// Trends page: tag statistics from summaries/index.json, drawn as plain HTML (no chart library).
// Color: tag groups use the validated 8-slot categorical palette (--series-N via .g-<group>);
// the heatmap uses one sequential blue ramp (--seq-0..6). Every mark also has a text label,
// a hover/focus tooltip, and a table view.
(function () {
  "use strict";
  const { $, el, loadManifest, parseDate, fmtDuration, status, tagIndex } = window.Council;
  const tip = $("tooltip");

  const METRICS = {
    hours: { label: "Discussion time", fmt: (v) => fmtDuration(v), col: "Time" },
    meetings: { label: "Meetings", fmt: (v) => `${v} meeting${v === 1 ? "" : "s"}`, col: "Meetings" },
    topics: { label: "Topics", fmt: (v) => `${v} topic${v === 1 ? "" : "s"}`, col: "Topics" },
  };

  function showTip(e, lines) {
    tip.replaceChildren(...lines.flatMap((l, i) => (i ? [el("br"), l] : [l])));
    tip.style.display = "block";
    const r = e.currentTarget.getBoundingClientRect();
    const x = e.clientX || r.left + r.width / 2, y = e.clientY || r.top;
    tip.style.left = `${Math.min(x + 12, innerWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${Math.max(8, y - tip.offsetHeight - 10)}px`;
  }
  const hideTip = () => { tip.style.display = "none"; };
  function hover(node, lines) {
    node.tabIndex = 0;
    node.addEventListener("pointerenter", (e) => showTip(e, lines()));
    node.addEventListener("pointermove", (e) => showTip(e, lines()));
    node.addEventListener("pointerleave", hideTip);
    node.addEventListener("focus", (e) => showTip(e, lines()));
    node.addEventListener("blur", hideTip);
  }

  function table(headers, rows) {
    return el("table", { class: "data" },
      el("thead", {}, el("tr", {}, ...headers.map((h, i) => el("th", { class: i ? "num" : undefined }, h)))),
      el("tbody", {}, ...rows.map((r) => el("tr", {}, ...r.map((c, i) => el("td", { class: i ? "num" : undefined }, String(c)))))));
  }

  async function render() {
    let manifest;
    try {
      manifest = await loadManifest();
    } catch (e) {
      status(`Could not load the meeting list (${e.message}).`);
      return;
    }
    const tags = tagIndex(manifest);
    const cities = manifest.cities || [];
    const state = { city: cities[0] && cities[0].slug, metric: "hours", period: "all", group: "", order: "value", rows: "12", pair: null };
    const tablesOpen = new Set();
    const goTo = (id) => `issue.html?city=${encodeURIComponent(state.city)}&tag=${encodeURIComponent(id)}`;

    if (cities.length > 1) {
      const sel = $("city");
      for (const c of cities) sel.append(el("option", { value: c.slug }, c.name));
      $("city-picker").hidden = false;
      sel.addEventListener("change", () => { state.city = sel.value; update(); });
    }
    const years = [...new Set((manifest.meetings || []).map((m) => m.date && m.date.slice(0, 4)).filter(Boolean))].sort().reverse();
    $("period").append(el("option", { value: "all" }, "All meetings"), ...years.map((y) => el("option", { value: y }, y)));
    for (const g of tags.groups) $("group").append(el("option", { value: g.id }, g.label));
    $("legend").append(...tags.groups.map((g) => el("span", { class: `g-${g.id}` }, g.label)));
    $("legend-mix").append(...tags.groups.map((g) => el("span", { class: `g-${g.id}` }, g.label)));
    state.mix = "share";
    $("mix-mode").addEventListener("change", () => { state.mix = $("mix-mode").value; update(); });

    const bind = (id, key) => $(id).addEventListener("change", () => { state[key] = $(id).value; update(); });
    bind("metric", "metric"); bind("period", "period"); bind("group", "group");
    bind("rank-order", "order"); bind("heat-rows", "rows"); bind("pair-tag", "pair");
    for (const b of document.querySelectorAll("[data-table]")) {
      b.addEventListener("click", () => {
        const k = b.dataset.table;
        if (tablesOpen.has(k)) tablesOpen.delete(k); else tablesOpen.add(k);
        b.textContent = tablesOpen.has(k) ? "Show chart" : "Show table";
        update();
      });
    }

    function meetings() {
      return (manifest.meetings || []).filter((m) => m.city === state.city &&
        (state.period === "all" || (m.date && m.date.startsWith(state.period))));
    }
    const inGroup = (id) => !state.group || tags.group(id) === state.group;

    // Per-tag totals for the chosen metric.
    function totals(list) {
      const out = new Map();
      for (const m of list) {
        const ts = m.tag_seconds || {};
        for (const id of Object.keys(ts)) {
          const t = out.get(id) || { hours: 0, meetings: 0, topics: 0 };
          t.hours += ts[id];
          t.meetings += 1;
          t.topics += (m.topics || []).filter((x) => x.tags.includes(id)).length;
          out.set(id, t);
        }
      }
      return out;
    }

    function drawTiles(list) {
      const tagged = list.filter((m) => Object.keys(m.tag_seconds || {}).length);
      const topics = list.reduce((n, m) => n + (m.topics || []).length, 0);
      const secs = list.reduce((n, m) => n + (m.topics || []).reduce((s, t) => s + (t.start !== null ? t.end - t.start : 0), 0), 0);
      const tile = (v, k) => el("div", { class: "tile" }, el("div", { class: "v" }, v), el("div", { class: "k" }, k));
      $("tiles").replaceChildren(
        tile(String(list.length), "meetings"),
        tile(String(tagged.length), "with tags"),
        tile(String(topics), "topics"),
        tile(fmtDuration(secs), "of discussion summarized"),
      );
    }

    function drawRank(tot) {
      const metric = METRICS[state.metric];
      let ids = [...tot.keys()].filter(inGroup);
      const val = (id) => tot.get(id)[state.metric];
      const groupOrder = new Map(tags.groups.map((g, i) => [g.id, i]));
      if (state.order === "name") ids.sort((a, b) => tags.label(a).localeCompare(tags.label(b)));
      else if (state.order === "group") ids.sort((a, b) => (groupOrder.get(tags.group(a)) - groupOrder.get(tags.group(b))) || val(b) - val(a));
      else ids.sort((a, b) => val(b) - val(a));
      const box = $("rank");
      if (!ids.length) { box.replaceChildren(el("p", { class: "status" }, "No tagged meetings in this selection.")); return; }
      if (tablesOpen.has("rank")) {
        box.replaceChildren(table(["Tag", "Time", "Meetings", "Topics"],
          ids.map((id) => [tags.label(id), fmtDuration(tot.get(id).hours), tot.get(id).meetings, tot.get(id).topics])));
        return;
      }
      const max = Math.max(...ids.map(val)) || 1;
      const grid = el("div", { class: "bars", role: "list" });
      for (const id of ids) {
        const t = tot.get(id);
        const label = el("a", { class: "label", href: goTo(id), title: `Meetings tagged ${tags.label(id)}` }, tags.label(id));
        const track = el("div", { class: `track g-${tags.group(id)}`, role: "listitem",
          "aria-label": `${tags.label(id)}: ${metric.fmt(val(id))}` },
          el("div", { class: "bar", style: `width:${(100 * val(id)) / max}%` }), el("span", { class: "val" }, metric.fmt(val(id))));
        hover(track, () => [tags.label(id), `${fmtDuration(t.hours)} · ${t.meetings} meetings · ${t.topics} topics`, "Select to follow this issue"]);
        track.addEventListener("click", () => { location.href = goTo(id); });
        track.addEventListener("keydown", (e) => { if (e.key === "Enter") location.href = goTo(id); });
        grid.append(label, track);
      }
      box.replaceChildren(grid);
    }

    function drawHeat(list, tot) {
      const dated = list.filter((m) => m.date);
      const undated = list.length - dated.length;
      const months = [...new Set(dated.map((m) => m.date.slice(0, 7)))].sort();
      let ids = [...tot.keys()].filter(inGroup).sort((a, b) => tot.get(b)[state.metric] - tot.get(a)[state.metric]);
      if (state.rows !== "all") ids = ids.slice(0, Number(state.rows));
      $("heat-note").textContent = `${METRICS[state.metric].label} per month for the top tags.` +
        (undated ? ` ${undated} undated meeting${undated === 1 ? " is" : "s are"} not shown.` : "");
      const box = $("heat");
      if (!ids.length || !months.length) { box.replaceChildren(el("p", { class: "status" }, "No dated, tagged meetings in this selection.")); return; }
      // value[tag][month]
      const cell = new Map();
      for (const m of dated) {
        const mo = m.date.slice(0, 7);
        for (const id of ids) {
          const secs = (m.tag_seconds || {})[id];
          if (secs === undefined) continue;
          const k = `${id}|${mo}`;
          const c = cell.get(k) || { hours: 0, meetings: 0, topics: 0 };
          c.hours += secs; c.meetings += 1; c.topics += (m.topics || []).filter((x) => x.tags.includes(id)).length;
          cell.set(k, c);
        }
      }
      const monthLabel = (mo) => parseDate(`${mo}-01`).toLocaleDateString("en-US", { month: "short", year: "numeric" });
      // Column header: month name, with the year underneath at the first column and each January.
      const colLabel = (mo, i) => {
        const d = parseDate(`${mo}-01`), name = d.toLocaleDateString("en-US", { month: "short" });
        return i === 0 || d.getMonth() === 0 ? el("div", { class: "cl" }, name, el("br"), String(d.getFullYear())) : el("div", { class: "cl" }, name);
      };
      if (tablesOpen.has("heat")) {
        box.replaceChildren(table(["Tag", ...months.map(monthLabel)], ids.map((id) => [tags.label(id),
          ...months.map((mo) => { const c = cell.get(`${id}|${mo}`); return c ? METRICS[state.metric].fmt(c[state.metric]) : "–"; })])));
        return;
      }
      const max = Math.max(...[...cell.values()].map((c) => c[state.metric])) || 1;
      const step = (v) => (v ? 1 + Math.min(5, Math.floor((5.999 * v) / max)) : 0); // 0 = none, 1..6 = light..dark
      const grid = el("div", { class: "heat", style: `grid-template-columns: auto repeat(${months.length}, 2.3rem)` });
      grid.append(el("div"), ...months.map(colLabel));
      for (const id of ids) {
        grid.append(el("a", { class: "rl", href: goTo(id) }, tags.label(id)));
        for (const mo of months) {
          const c = cell.get(`${id}|${mo}`);
          const v = c ? c[state.metric] : 0;
          const node = el("div", { class: "cell", role: "img", style: `background: var(--seq-${step(v)})`,
            "aria-label": `${tags.label(id)}, ${monthLabel(mo)}: ${c ? METRICS[state.metric].fmt(v) : "none"}` });
          hover(node, () => [`${tags.label(id)} · ${monthLabel(mo)}`, c ? `${fmtDuration(c.hours)} · ${c.meetings} meetings · ${c.topics} topics` : "Not discussed"]);
          node.addEventListener("click", () => { location.href = goTo(id); });
          grid.append(node);
        }
      }
      const scale = el("div", { class: "scale" }, "Less", ...[0, 1, 2, 3, 4, 5, 6].map((i) => el("i", { style: `background: var(--seq-${i})` })), "More");
      box.replaceChildren(grid, scale);
    }

    // Stacked bar per month: time by tag group (segments keep the fixed group order and colors).
    function drawMix(list) {
      const dated = list.filter((m) => m.date);
      const months = [...new Set(dated.map((m) => m.date.slice(0, 7)))].sort();
      const box = $("mix");
      if (!months.length) { box.replaceChildren(el("p", { class: "status" }, "No dated, tagged meetings in this selection.")); return; }
      const data = new Map(months.map((mo) => [mo, new Map(tags.groups.map((g) => [g.id, 0]))]));
      for (const m of dated) {
        const row = data.get(m.date.slice(0, 7));
        for (const [id, secs] of Object.entries(m.tag_seconds || {})) {
          const g = tags.group(id);
          if (row.has(g)) row.set(g, row.get(g) + secs);
        }
      }
      const label = (mo) => parseDate(`${mo}-01`).toLocaleDateString("en-US", { month: "short", year: "numeric" });
      const total = (mo) => [...data.get(mo).values()].reduce((a, b) => a + b, 0);
      if (tablesOpen.has("mix")) {
        box.replaceChildren(table(["Month", ...tags.groups.map((g) => g.label), "Total"], months.map((mo) => [label(mo),
          ...tags.groups.map((g) => { const v = data.get(mo).get(g.id); return v ? (state.mix === "share" ? `${Math.round((100 * v) / total(mo))}%` : fmtDuration(v)) : "–"; }),
          fmtDuration(total(mo))])));
        return;
      }
      const max = Math.max(...months.map(total)) || 1;
      const grid = el("div", { class: "bars stack" });
      let lastYear = null;
      for (const mo of months) {
        const d = parseDate(`${mo}-01`), t = total(mo);
        const name = d.toLocaleDateString("en-US", { month: "short" }) + (d.getFullYear() !== lastYear ? ` ${d.getFullYear()}` : "");
        lastYear = d.getFullYear();
        const track = el("div", { class: "track", role: "img", "aria-label": `${label(mo)}: ${fmtDuration(t)} of tagged discussion` });
        const scale = state.mix === "share" ? (t || 1) : max;
        for (const g of tags.groups) {
          const v = data.get(mo).get(g.id);
          if (!v) continue;
          const seg = el("div", { class: `bar seg g-${g.id}`, style: `width:${(100 * v) / scale}%` });
          hover(seg, () => [`${g.label} · ${label(mo)}`, `${fmtDuration(v)} (${Math.round((100 * v) / (t || 1))}% of the month)`]);
          track.append(seg);
        }
        if (state.mix === "hours") track.append(el("span", { class: "val" }, fmtDuration(t)));
        grid.append(el("div", { class: "label plain" }, name), track);
      }
      box.replaceChildren(grid);
    }

    function drawPairs(list, tot) {
      const sel = $("pair-tag");
      const ids = [...tot.keys()].sort((a, b) => tot.get(b).topics - tot.get(a).topics);
      if (!ids.length) { $("pairs").replaceChildren(el("p", { class: "status" }, "No tagged topics in this selection.")); return; }
      if (!state.pair || !tot.has(state.pair)) state.pair = ids[0];
      sel.replaceChildren(...ids.map((id) => el("option", { value: id }, tags.label(id))));
      sel.value = state.pair;
      const counts = new Map();
      let base = 0;
      for (const m of list) {
        for (const t of m.topics || []) {
          if (!t.tags.includes(state.pair)) continue;
          base += 1;
          for (const other of t.tags) if (other !== state.pair) counts.set(other, (counts.get(other) || 0) + 1);
        }
      }
      const rows = [...counts.entries()].filter(([id]) => inGroup(id)).sort((a, b) => b[1] - a[1]).slice(0, 12);
      const box = $("pairs");
      if (!rows.length) { box.replaceChildren(el("p", { class: "status" }, `${tags.label(state.pair)} topics don't share tags with others here.`)); return; }
      if (tablesOpen.has("pairs")) {
        box.replaceChildren(table(["Tag", "Shared topics", `% of ${tags.label(state.pair)} topics`],
          rows.map(([id, n]) => [tags.label(id), n, `${Math.round((100 * n) / base)}%`])));
        return;
      }
      const grid = el("div", { class: "bars" });
      for (const [id, n] of rows) {
        const pct = Math.round((100 * n) / base);
        const track = el("div", { class: `track g-${tags.group(id)}`, "aria-label": `${tags.label(id)}: ${pct}% of ${tags.label(state.pair)} topics` },
          el("div", { class: "bar", style: `width:${pct}%` }), el("span", { class: "val" }, `${pct}%`));
        hover(track, () => [`${tags.label(state.pair)} + ${tags.label(id)}`, `${n} of ${base} topics (${pct}%)`, "Select to see these meetings"]);
        const both = `index.html?city=${encodeURIComponent(state.city)}&tags=${encodeURIComponent(state.pair)},${encodeURIComponent(id)}&match=all&sort=relevance`;
        track.addEventListener("click", () => { location.href = both; });
        track.addEventListener("keydown", (e) => { if (e.key === "Enter") location.href = both; });
        grid.append(el("a", { class: "label", href: both }, tags.label(id)), track);
      }
      box.replaceChildren(grid);
    }

    function update() {
      const list = meetings();
      const tot = totals(list);
      status(list.length ? "" : "No meetings in this selection.");
      drawTiles(list);
      drawRank(tot);
      drawMix(list);
      drawHeat(list, tot);
      drawPairs(list, tot);
    }
    update();
  }

  render();
})();
