// Issue page: everything the council said about one tag, over time.
// Data: summaries/index.json (topics, tags, times) + summaries/search.json (topic summary text).
(function () {
  "use strict";
  const { $, el, getJSON, loadManifest, parseDate, fmtTime, fmtDuration, status, tagIndex, tagChip } = window.Council;
  const tip = $("tooltip");

  async function render() {
    let manifest, text;
    try {
      [manifest, text] = await Promise.all([loadManifest(), getJSON("summaries/search.json", { cache: "no-cache" }).catch(() => ({}))]);
    } catch (e) {
      status(`Could not load the meeting list (${e.message}).`);
      return;
    }
    const tags = tagIndex(manifest);
    const cities = manifest.cities || [];
    const params = new URLSearchParams(location.search);
    const state = {
      city: cities.some((c) => c.slug === params.get("city")) ? params.get("city") : cities[0] && cities[0].slug,
      tag: tags.byId.has(params.get("tag")) ? params.get("tag") : null,
      order: "newest", table: false,
    };

    const tagSel = $("tag");
    for (const g of tags.groups) {
      const group = el("optgroup", { label: g.label });
      for (const t of tags.byId.values()) if (t.group === g.id) group.append(el("option", { value: t.id }, t.label));
      tagSel.append(group);
    }
    if (cities.length > 1) {
      for (const c of cities) $("city").append(el("option", { value: c.slug }, c.name));
      $("city").value = state.city;
      $("city-picker").hidden = false;
      $("city").addEventListener("change", () => { state.city = $("city").value; update(); });
    }
    tagSel.addEventListener("change", () => { state.tag = tagSel.value; update(); });
    $("order").addEventListener("change", () => { state.order = $("order").value; update(); });
    $("toggle-table").addEventListener("click", () => { state.table = !state.table; $("toggle-table").textContent = state.table ? "Show chart" : "Show table"; update(); });

    // Share of a topic's time credited to this tag: same order weighting as build_site.credit_tags.
    function share(topic, tag) {
      const i = topic.tags.indexOf(tag);
      if (i < 0 || topic.start === null) return 0;
      const total = topic.tags.reduce((s, _, k) => s + 1 / (k + 1), 0);
      return ((topic.end - topic.start) * (1 / (i + 1))) / total;
    }

    function update() {
      const meetings = (manifest.meetings || []).filter((m) => m.city === state.city);
      if (!state.tag) { // default: the most discussed tag
        const totals = new Map();
        for (const m of meetings) for (const [k, v] of Object.entries(m.tag_seconds || {})) totals.set(k, (totals.get(k) || 0) + v);
        state.tag = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k)[0] || [...tags.byId.keys()][0];
      }
      const tag = state.tag;
      tagSel.value = tag;
      history.replaceState(null, "", `?city=${encodeURIComponent(state.city)}&tag=${encodeURIComponent(tag)}`);
      const info = tags.byId.get(tag) || { label: tag };
      document.title = `${info.label}: council discussions`;
      $("title").replaceChildren(info.label);
      $("desc").textContent = info.description || "";

      // Every topic with this tag.
      const items = [];
      for (const m of meetings) {
        (m.topics || []).forEach((t, i) => {
          if (t.tags.includes(tag)) items.push({ m, t, i, secs: share(t, tag), summary: (text[`${m.city}/${m.id}`] || [])[i] || "" });
        });
      }
      const withTag = new Set(items.map((x) => x.m.id));
      const secs = items.reduce((s, x) => s + x.secs, 0);
      const dates = items.map((x) => x.m.date).filter(Boolean).sort();
      const tile = (v, k) => el("div", { class: "tile" }, el("div", { class: "v" }, v), el("div", { class: "k" }, k));
      const fmtDate = (d) => parseDate(d).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
      $("tiles").replaceChildren(
        tile(String(items.length), `discussion${items.length === 1 ? "" : "s"}`),
        tile(`${withTag.size} of ${meetings.length}`, "meetings"),
        tile(fmtDuration(secs), "of discussion (est.)"),
        tile(dates.length ? parseDate(dates[dates.length - 1]).toLocaleDateString("en-US", { month: "short", year: "numeric" }) : "–",
          dates.length ? `most recent (${fmtDate(dates[dates.length - 1])})` : "most recent"),
      );
      status(items.length ? "" : `No discussions tagged ${info.label} yet.`);

      drawMonths(meetings, items, info);
      drawRelated(items, tag);
      drawList(items, tag);
    }

    // Column chart: estimated minutes on this issue in each month that had a meeting.
    function drawMonths(meetings, items, info) {
      const months = [...new Set(meetings.map((m) => m.date && m.date.slice(0, 7)).filter(Boolean))].sort();
      const by = new Map(months.map((mo) => [mo, { secs: 0, n: 0 }]));
      for (const x of items) {
        const c = x.m.date && by.get(x.m.date.slice(0, 7));
        if (c) { c.secs += x.secs; c.n += 1; }
      }
      const label = (mo) => parseDate(`${mo}-01`).toLocaleDateString("en-US", { month: "short", year: "numeric" });
      $("chart-note").textContent = `Estimated time on ${info.label} in each month that had a meeting.`;
      const box = $("months");
      if (!months.length) { box.replaceChildren(); return; }
      if (state.table) {
        box.replaceChildren(el("table", { class: "data" },
          el("thead", {}, el("tr", {}, el("th", {}, "Month"), el("th", { class: "num" }, "Time"), el("th", { class: "num" }, "Discussions"))),
          el("tbody", {}, ...months.map((mo) => el("tr", {}, el("td", {}, label(mo)), el("td", { class: "num" }, by.get(mo).secs ? fmtDuration(by.get(mo).secs) : "–"), el("td", { class: "num" }, String(by.get(mo).n)))))));
        return;
      }
      const max = Math.max(...months.map((mo) => by.get(mo).secs)) || 1;
      const cols = el("div", { class: `cols g-${tags.group(state.tag)}`, role: "list" });
      let lastYear = null, prev = null;
      months.forEach((mo) => {
        const c = by.get(mo), d = parseDate(`${mo}-01`), year = d.getFullYear();
        // Mark a break in the axis where months without any meeting are skipped.
        const index = year * 12 + d.getMonth();
        if (prev !== null && index - prev > 1) {
          cols.append(el("div", { class: "colgap", title: `${index - prev - 1} months with no meetings on this site`, "aria-hidden": "true" }, "⋯"));
          lastYear = null;
        }
        prev = index;
        const col = el("div", { class: "col", role: "listitem", tabindex: "0", "aria-label": `${label(mo)}: ${c.secs ? fmtDuration(c.secs) : "none"}` },
          el("div", { class: "colbar", style: `height:${(100 * c.secs) / max}%` }),
          el("div", { class: "collabel" }, d.toLocaleDateString("en-US", { month: "short" }), el("br"), year !== lastYear ? String(year) : " "));
        lastYear = year;
        const show = (e) => {
          tip.replaceChildren(label(mo), el("br"), c.secs ? `${fmtDuration(c.secs)} · ${c.n} discussion${c.n === 1 ? "" : "s"}` : "Not discussed");
          tip.style.display = "block";
          const r = col.getBoundingClientRect();
          tip.style.left = `${Math.max(8, Math.min((e.clientX || r.left) + 12, innerWidth - tip.offsetWidth - 8))}px`;
          tip.style.top = `${Math.max(8, (e.clientY || r.top) - tip.offsetHeight - 10)}px`;
        };
        const hide = () => { tip.style.display = "none"; };
        col.addEventListener("pointerenter", show); col.addEventListener("pointermove", show); col.addEventListener("pointerleave", hide);
        col.addEventListener("focus", show); col.addEventListener("blur", hide);
        cols.append(col);
      });
      box.replaceChildren(cols);
    }

    function drawRelated(items, tag) {
      const counts = new Map();
      for (const x of items) for (const g of x.t.tags) if (g !== tag) counts.set(g, (counts.get(g) || 0) + 1);
      const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
      $("related-section").hidden = !top.length;
      $("related").replaceChildren(...top.map(([g, n]) => tagChip(tags, g, {
        href: `issue.html?city=${encodeURIComponent(state.city)}&tag=${encodeURIComponent(g)}`, count: n,
        title: `${n} shared discussion${n === 1 ? "" : "s"} · open ${tags.label(g)}`,
      })));
    }

    function drawList(items, tag) {
      const byDate = (a, b) => (b.m.date || "").localeCompare(a.m.date || "") || (a.t.start || 0) - (b.t.start || 0);
      const list = [...items];
      if (state.order === "oldest") list.sort((a, b) => -byDate(a, b) || (a.t.start || 0) - (b.t.start || 0));
      else if (state.order === "longest") list.sort((a, b) => b.secs - a.secs);
      else list.sort(byDate);
      const box = $("discussions");
      box.replaceChildren(...list.map((x) => {
        const href = `meeting.html?city=${encodeURIComponent(x.m.city)}&id=${encodeURIComponent(x.m.id)}`;
        const d = parseDate(x.m.date);
        const head = el("p", { class: "meta" },
          d ? d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" }) : "Undated", " · ",
          el("a", { href }, x.m.title || x.m.id));
        const title = el("h3", {}, x.t.title);
        const row = el("div", { class: "stamps" });
        if (x.t.start !== null) {
          row.append(el("a", { class: "stamp", href: `${href}#t=${Math.floor(x.t.start)}`, title: "Open the meeting at this moment" },
            `▶ ${fmtTime(x.t.start)}`), el("span", { class: "tag-note" }, `about ${fmtDuration(x.t.end - x.t.start)}`));
        }
        const others = x.t.tags.filter((g) => g !== tag);
        const chips = others.length ? el("div", { class: "tags" }, ...others.map((g) => tagChip(tags, g, {
          href: `issue.html?city=${encodeURIComponent(state.city)}&tag=${encodeURIComponent(g)}`, title: `Open ${tags.label(g)}` }))) : null;
        return el("article", { class: "discussion" }, head, title, x.summary ? el("p", {}, x.summary) : null, row, chips);
      }));
    }

    update();
  }

  render();
})();
