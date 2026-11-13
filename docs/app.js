// Renders the index and meeting pages from summaries/index.json and the summary JSON files.
// Summaries come from an LLM, so every string is inserted with textContent, never innerHTML.
// Shared helpers are exported as window.Council for trends.js.
(function () {
  "use strict";

  const CONFIG = window.COUNCIL_CONFIG || {};
  const CITY_RE = /^[a-z0-9-]+$/;
  const ID_RE = /^(\d{4}-\d{2}-\d{2}|undated)_([A-Za-z0-9_-]+)$/;

  const $ = (id) => document.getElementById(id);

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") node.className = v;
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children) if (c !== null && c !== undefined) node.append(c);
    return node;
  }

  async function getJSON(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
    return res.json();
  }

  const loadManifest = () => getJSON("summaries/index.json", { cache: "no-cache" });

  function parseDate(s) {
    return s ? new Date(`${s}T00:00:00`) : null;
  }

  function fmtTime(seconds) {
    const s = Math.floor(seconds);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    const mm = String(m).padStart(h ? 2 : 1, "0"), ss = String(sec).padStart(2, "0");
    return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
  }

  function fmtDuration(seconds) {
    const m = Math.round(seconds / 60);
    return m < 60 ? `${m} min` : `${(m / 60).toFixed(m < 600 ? 1 : 0)} h`;
  }

  function status(msg) {
    const s = $("status");
    s.textContent = msg || "";
    s.hidden = !msg;
  }

  // ---- Tags ---------------------------------------------------------------------------

  // Index of the tag vocabulary carried in the manifest: id -> {id, label, group}.
  function tagIndex(manifest) {
    const byId = new Map((manifest.tags || []).map((t) => [t.id, t]));
    return {
      byId,
      groups: manifest.tag_groups || [],
      label: (id) => (byId.get(id) || { label: id }).label,
      group: (id) => (byId.get(id) || {}).group || "",
    };
  }

  // A tag chip. As a button it toggles (aria-pressed); with href it's a link.
  function tagChip(tags, id, { pressed, count, href, onClick, title } = {}) {
    const attrs = { class: `tag g-${tags.group(id)}`, title };
    let chip;
    if (href) chip = el("a", { ...attrs, href }, tags.label(id));
    else chip = el("button", { ...attrs, type: "button", "aria-pressed": pressed ? "true" : "false" }, tags.label(id));
    if (count !== undefined) chip.append(el("span", { class: "n" }, String(count)));
    if (onClick) chip.addEventListener("click", (e) => onClick(id, e));
    return chip;
  }

  // Topics is a list of objects keyed "Topic1", "Topic2", ...; accept one key per object or many.
  function topicsOf(summary) {
    const out = [];
    const num = (k) => parseInt(k.replace(/\D/g, ""), 10) || 0;
    for (const entry of Array.isArray(summary.Topics) ? summary.Topics : []) {
      if (!entry || typeof entry !== "object") continue;
      for (const key of Object.keys(entry).sort((a, b) => num(a) - num(b))) {
        const t = entry[key];
        if (!t || typeof t !== "object") continue;
        const stamps = (Array.isArray(t.related_timestamps) ? t.related_timestamps : [])
          .map(Number).filter((n) => Number.isFinite(n) && n >= 0);
        out.push({
          title: String(t.Title || key),
          summary: String(t.Summary || ""),
          stamps: [...new Set(stamps)].sort((a, b) => a - b),
          tags: (Array.isArray(t.Tags) ? t.Tags : []).filter((g) => typeof g === "string"),
        });
      }
    }
    return out;
  }

  // ---- Index page ---------------------------------------------------------------------

  // How strongly a meeting matches the selected tags, for the "Most time on selected tags" sort.
  // tag_seconds is the estimated video time spent on topics carrying each tag.
  function relevanceScore(meeting, selected) {
    let score = 0;
    for (const tag of selected) score += (meeting.tag_seconds || {})[tag] || 0;
    return score;
  }

  function meetingTags(m) {
    return Object.keys(m.tag_seconds || {}); // every tag on any topic, most discussed first
  }

  function readState() {
    const p = new URLSearchParams(location.search);
    return {
      city: p.get("city"),
      tags: (p.get("tags") || "").split(",").filter(Boolean),
      match: p.get("match") === "all" ? "all" : "any",
      sort: p.get("sort") || "newest",
      q: p.get("q") || "",
    };
  }

  function writeState(state) {
    const p = new URLSearchParams();
    if (state.city) p.set("city", state.city);
    if (state.tags.length) p.set("tags", state.tags.join(","));
    if (state.match === "all") p.set("match", "all");
    if (state.sort !== "newest") p.set("sort", state.sort);
    if (state.q) p.set("q", state.q);
    const qs = p.toString();
    history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
  }

  async function renderIndex() {
    let manifest;
    try {
      manifest = await loadManifest();
    } catch (e) {
      status(`Could not load the meeting list (${e.message}).`);
      return;
    }
    const cities = manifest.cities || [];
    const all = manifest.meetings || [];
    if (!all.length) {
      status("No meeting summaries yet.");
      return;
    }
    status("");
    const tags = tagIndex(manifest);
    const state = readState();
    try { state.city = state.city || localStorage.getItem("city"); } catch (_) { /* storage unavailable */ }
    if (!cities.some((c) => c.slug === state.city)) state.city = cities[0] && cities[0].slug;
    state.tags = state.tags.filter((t) => tags.byId.has(t));

    const select = $("city");
    if (cities.length > 1) {
      for (const c of cities) select.append(el("option", { value: c.slug }, c.name));
      select.value = state.city;
      $("city-picker").hidden = false;
      select.addEventListener("change", () => {
        state.city = select.value;
        try { localStorage.setItem("city", select.value); } catch (_) { /* ignore */ }
        update();
      });
    }
    if (state.tags.length) $("tag-panel").open = true; // arriving from a tag link: show what's selected
    $("q").value = state.q;
    $("sort").value = state.sort;
    $("match").value = state.match;
    // Topic summary text for full-text search: {"<city>/<id>": [summary per topic]}, fetched on first use.
    let searchText = null, searchRequested = false;
    function ensureSearchText() {
      if (searchRequested) return;
      searchRequested = true;
      getJSON("summaries/search.json", { cache: "no-cache" }).then((data) => { searchText = data; update(); })
        .catch(() => { /* titles-only search still works */ });
    }
    const words = () => state.q.toLowerCase().split(/\s+/).filter(Boolean);
    const summaryOf = (m, i) => (searchText && (searchText[`${m.city}/${m.id}`] || [])[i]) || "";
    // Topics of a meeting that mention any search word, with where the first match is.
    function searchHits(m) {
      const ws = words();
      if (!ws.length) return [];
      return (m.topics || []).map((t, i) => ({ t, summary: summaryOf(m, i) }))
        .filter(({ t, summary }) => ws.some((w) => t.title.toLowerCase().includes(w) || summary.toLowerCase().includes(w)));
    }
    // "…text around the first match…" with matches wrapped in <mark>.
    function snippet(text, ws) {
      const lower = text.toLowerCase();
      const first = Math.min(...ws.map((w) => lower.indexOf(w)).filter((i) => i >= 0));
      if (!Number.isFinite(first)) return null;
      const from = Math.max(0, first - 60), to = Math.min(text.length, first + 110);
      const piece = (from ? "…" : "") + text.slice(from, to) + (to < text.length ? "…" : "");
      const out = el("span", { class: "snippet" });
      const re = new RegExp(`(${ws.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "ig");
      for (const part of piece.split(re)) {
        if (!part) continue;
        out.append(ws.includes(part.toLowerCase()) ? el("mark", {}, part) : part);
      }
      return out;
    }

    $("q").addEventListener("input", () => { state.q = $("q").value.trim(); if (state.q) ensureSearchText(); update(); });
    if (state.q) ensureSearchText();
    $("sort").addEventListener("change", () => { state.sort = $("sort").value; update(); });
    $("match").addEventListener("change", () => { state.match = $("match").value; update(); });

    function toggleTag(id) {
      const i = state.tags.indexOf(id);
      if (i >= 0) state.tags.splice(i, 1); else state.tags.push(id);
      update();
    }

    function filtered() {
      const q = state.q.toLowerCase();
      return all.filter((m) => {
        if (m.city !== state.city) return false;
        if (state.tags.length) {
          const have = new Set(meetingTags(m));
          const hit = state.match === "all" ? state.tags.every((t) => have.has(t)) : state.tags.some((t) => have.has(t));
          if (!hit) return false;
        }
        if (q) {
          // Every word must appear somewhere in the meeting: its title, topic titles or summaries.
          const text = [m.title, ...(m.topics || []).map((t, i) => `${t.title} ${summaryOf(m, i)}`)].join(" ").toLowerCase();
          if (!words().every((w) => text.includes(w))) return false;
        }
        return true;
      });
    }

    function sorted(list) {
      const byDate = (a, b) => (b.date || "").localeCompare(a.date || "") || b.id.localeCompare(a.id);
      const out = [...list];
      if (state.sort === "oldest") out.sort((a, b) => -byDate(a, b));
      else if (state.sort === "relevance" && state.tags.length)
        out.sort((a, b) => relevanceScore(b, state.tags) - relevanceScore(a, state.tags) || byDate(a, b));
      else if (state.sort === "topics") out.sort((a, b) => (b.topics || []).length - (a.topics || []).length || byDate(a, b));
      else out.sort(byDate);
      return out;
    }

    function drawTagPanel(cityMeetings) {
      const counts = new Map();
      for (const m of cityMeetings) for (const t of meetingTags(m)) counts.set(t, (counts.get(t) || 0) + 1);
      const box = $("tag-groups");
      box.replaceChildren();
      for (const g of tags.groups) {
        const ids = [...tags.byId.values()].filter((t) => t.group === g.id && counts.get(t.id)).map((t) => t.id)
          .sort((a, b) => counts.get(b) - counts.get(a));
        if (!ids.length) continue;
        box.append(el("div", { class: "tag-group" }, el("h4", {}, g.label),
          el("div", { class: "tags" }, ...ids.map((id) => tagChip(tags, id, {
            pressed: state.tags.includes(id), count: counts.get(id), onClick: toggleTag,
            title: `${counts.get(id)} meeting${counts.get(id) === 1 ? "" : "s"}`,
          })))));
      }
      if (!box.children.length) box.append(el("p", { class: "tag-note" }, "No tagged meetings yet."));
      const top = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a)).slice(0, 4).map(tags.label);
      $("tag-summary").textContent = state.tags.length
        ? `Filter by tag (${state.tags.length} selected)`
        : `Filter by tag: ${counts.size} tags such as ${top.join(", ")}…`;
    }

    function drawActive(shown, total) {
      const p = $("active");
      const bits = [];
      if (state.tags.length) bits.push(`tagged ${state.tags.map(tags.label).join(state.match === "all" ? " and " : " or ")}`);
      if (state.q) bits.push(`matching “${state.q}”`);
      p.hidden = !bits.length;
      if (!bits.length) return;
      const clear = el("button", { type: "button", class: "link" }, "Clear filters");
      clear.addEventListener("click", () => { state.tags = []; state.q = ""; $("q").value = ""; update(); });
      p.replaceChildren(`Showing ${shown} of ${total} meetings ${bits.join(", ")}.`, clear);
    }

    function meetingItem(m) {
      const d = parseDate(m.date);
      const label = d ? d.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(state.sort === "relevance" || state.sort === "topics" ? { year: "numeric" } : {}) }) : "Undated";
      const href = `meeting.html?city=${encodeURIComponent(m.city)}&id=${encodeURIComponent(m.id)}`;
      const li = el("li", {}, el("span", { class: "date" }, label), el("a", { href }, m.title || m.id));
      const top = meetingTags(m).slice(0, 6);
      if (top.length) {
        li.append(el("div", { class: "tags" }, ...top.map((id) => tagChip(tags, id, {
          pressed: state.tags.includes(id), onClick: toggleTag,
          title: `about ${fmtDuration(m.tag_seconds[id])} of discussion · select to filter`,
        }))));
      }
      // Jump links to the topics that match: by search words if searching, else by selected tags.
      const ws = words();
      let hits = [];
      if (ws.length) {
        hits = searchHits(m).filter(({ t }) => !state.tags.length || t.tags.some((g) => state.tags.includes(g)));
        if (!hits.length) hits = searchHits(m);
      } else if (state.tags.length) {
        hits = (m.topics || []).filter((t) => t.tags.some((g) => state.tags.includes(g))).map((t) => ({ t, summary: "" }));
      }
      hits = hits.filter(({ t }) => t.start !== null);
      if (hits.length) {
        li.append(el("ul", { class: "segments", "aria-label": "Matching segments" }, ...hits.map(({ t, summary }) => {
          const body = el("span", {}, t.title);
          const snip = ws.length && summary ? snippet(summary, ws) : null;
          if (snip) body.append(el("br"), snip);
          return el("li", {}, el("a", { class: "stamp", href: `${href}#t=${Math.floor(t.start)}` }, fmtTime(t.start)), body);
        })));
      }
      return li;
    }

    function update() {
      $("sort").value = state.sort;
      writeState(state);
      const cityMeetings = all.filter((m) => m.city === state.city);
      drawTagPanel(cityMeetings);
      const list = sorted(filtered());
      drawActive(list.length, cityMeetings.length);
      const box = $("list");
      box.replaceChildren();
      if (!list.length) {
        box.append(el("p", { class: "status" }, "No meetings match these filters."));
        return;
      }
      if (state.sort === "newest" || state.sort === "oldest") {
        const groups = new Map();
        for (const m of list) {
          const year = m.date ? m.date.slice(0, 4) : "Undated";
          if (!groups.has(year)) groups.set(year, []);
          groups.get(year).push(m);
        }
        for (const [year, items] of groups) box.append(el("h2", {}, year), el("ul", { class: "meetings" }, ...items.map(meetingItem)));
      } else {
        box.append(el("ul", { class: "meetings" }, ...list.map(meetingItem)));
      }
    }
    update();
  }

  // ---- Meeting page: YouTube player -----------------------------------------------------

  const video = { id: null, player: null, ready: false, fallback: null, pending: null };

  function loadPlayer(videoId) {
    video.id = videoId;
    window.onYouTubeIframeAPIReady = () => {
      if (video.fallback) return;
      video.player = new YT.Player("player", {
        videoId,
        playerVars: { playsinline: 1, rel: 0 },
        events: {
          onReady: () => {
            video.ready = true;
            if (video.pending !== null) seek(video.pending, false);
          },
        },
      });
    };
    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    script.onerror = useFallbackPlayer;
    document.head.append(script);
    setTimeout(() => { if (!video.player) useFallbackPlayer(); }, 8000);
  }

  // Plain embed if the IFrame API can't load (blocked scripts etc.): seek by reloading at ?start=.
  function useFallbackPlayer() {
    if (video.fallback || video.player) return;
    video.fallback = el("iframe", {
      src: embedUrl(video.pending || 0, false),
      title: "Meeting video",
      allow: "autoplay; encrypted-media; picture-in-picture",
      allowfullscreen: "",
    });
    $("player").replaceWith(video.fallback);
  }

  function embedUrl(t, autoplay) {
    return `https://www.youtube.com/embed/${encodeURIComponent(video.id)}?start=${Math.floor(t)}` +
      `&playsinline=1&rel=0${autoplay ? "&autoplay=1" : ""}`;
  }

  function watchUrl(t) {
    return `https://www.youtube.com/watch?v=${encodeURIComponent(video.id)}&t=${Math.floor(t)}s`;
  }

  function seek(t, play = true) {
    if (video.ready) {
      video.player.seekTo(t, true);
      if (play) video.player.playVideo();
    } else if (video.fallback) {
      video.fallback.src = embedUrl(t, play);
    } else {
      video.pending = t;
    }
    history.replaceState(null, "", `${location.search}#t=${Math.floor(t)}`);
    highlightTranscript(t);
  }

  function currentTime() {
    try { return video.ready ? video.player.getCurrentTime() : null; } catch (_) { return null; }
  }

  // ---- Meeting page: topic timeline -----------------------------------------------------

  // A strip under the video: one block per topic, placed by its start/end and colored by the
  // group of its first tag. Overlapping topics (e.g. interleaved public comment) go on
  // separate rows. Returns a function that moves the playhead and marks the playing topic.
  function drawTimeline(spans, tags, sections) {
    const box = $("timeline");
    const timed = spans.map((t, i) => ({ ...t, i })).filter((t) => t.start !== null && t.end > t.start);
    if (timed.length < 2) return () => {};
    const total = Math.max(...timed.map((t) => t.end));
    const rows = []; // greedy lane assignment
    for (const t of [...timed].sort((a, b) => a.start - b.start)) {
      let lane = rows.findIndex((end) => end <= t.start);
      if (lane < 0) { lane = rows.length; rows.push(0); }
      rows[lane] = t.end;
      t.lane = lane;
    }
    const lanes = Math.min(rows.length, 3);
    box.style.setProperty("--lanes", lanes);
    const tip = $("tooltip");
    for (const t of timed) {
      const block = el("button", {
        type: "button", class: `block g-${tags.group(t.tags[0] || "")}`,
        style: `left:${(100 * t.start) / total}%;width:${Math.max(0.6, (100 * (t.end - t.start)) / total)}%;--lane:${Math.min(t.lane, lanes - 1)}`,
        "aria-label": `${t.title}, starts at ${fmtTime(t.start)}`,
      });
      const show = (e) => {
        tip.replaceChildren(t.title, el("br"), `${fmtTime(t.start)} – ${fmtTime(t.end)}` +
          (t.tags.length ? ` · ${t.tags.map(tags.label).join(", ")}` : ""));
        tip.style.display = "block";
        const r = block.getBoundingClientRect();
        tip.style.left = `${Math.max(8, Math.min((e.clientX || r.left) + 12, innerWidth - tip.offsetWidth - 8))}px`;
        tip.style.top = `${Math.max(8, r.top - tip.offsetHeight - 8)}px`;
      };
      const hide = () => { tip.style.display = "none"; };
      block.addEventListener("pointerenter", show);
      block.addEventListener("pointermove", show);
      block.addEventListener("pointerleave", hide);
      block.addEventListener("focus", show);
      block.addEventListener("blur", hide);
      block.addEventListener("click", () => {
        seek(t.start);
        sections[t.i].section.scrollIntoView({ behavior: "smooth", block: "center" });
      });
      t.block = block;
      box.append(block);
    }
    const head = el("div", { class: "playhead", hidden: true });
    box.append(head);
    box.hidden = false;

    let playing = -1;
    return (time) => {
      if (time === null) return;
      head.hidden = false;
      head.style.left = `${Math.min(100, (100 * time) / total)}%`;
      // The playing topic: the latest-starting topic that contains the time.
      let now = -1;
      for (const t of timed) if (t.start <= time && time < t.end && (now < 0 || t.start >= spans[now].start)) now = t.i;
      if (now === playing) return;
      playing = now;
      for (const t of timed) t.block.classList.toggle("on", t.i === now);
      sections.forEach((s, i) => s.section.classList.toggle("playing", i === now));
    };
  }

  function stampLink(t) {
    const a = el("a", { class: "stamp", href: watchUrl(t), title: "Jump to this point in the video" }, fmtTime(t));
    a.addEventListener("click", (e) => {
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return; // let new-tab clicks through
      e.preventDefault();
      seek(t);
    });
    return a;
  }

  // ---- Meeting page: full transcript from the host machine ------------------------------

  const transcript = { starts: [], rows: [], current: null };

  async function loadTranscript(city, id) {
    const box = $("transcript"), button = $("load-transcript");
    button.disabled = true;
    button.textContent = "Loading…";
    const host = CONFIG.transcriptHost.replace(/\/+$/, "");
    let data;
    try {
      data = await getJSON(`${host}/${city}/${id}.json`);
    } catch (e) {
      button.disabled = false;
      button.textContent = "Load full transcript";
      status(`The full transcript is unavailable right now (${e.message}).`);
      return;
    }
    status("");
    const frag = document.createDocumentFragment();
    for (const seg of data.segments || []) {
      const start = Number(seg.start);
      if (!Number.isFinite(start)) continue;
      const row = el("div", { class: "seg" }, stampLink(start), el("span", {}, String(seg.text || "")));
      transcript.starts.push(start);
      transcript.rows.push(row);
      frag.append(row);
    }
    box.replaceChildren(frag);
    box.hidden = false;
    button.hidden = true;
    const m = /#t=(\d+)/.exec(location.hash);
    if (m) highlightTranscript(Number(m[1]));
  }

  function highlightTranscript(t) {
    const { starts, rows } = transcript;
    if (!starts.length) return;
    let lo = 0, hi = starts.length - 1; // last segment starting at or before t
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (starts[mid] <= t + 0.01) lo = mid; else hi = mid - 1;
    }
    if (transcript.current) transcript.current.classList.remove("current");
    const row = rows[lo];
    row.classList.add("current");
    transcript.current = row;
    const box = $("transcript");
    box.scrollTop = row.offsetTop - box.offsetTop - box.clientHeight / 3;
  }

  // ---- Meeting page ---------------------------------------------------------------------

  async function renderMeeting() {
    const params = new URLSearchParams(location.search);
    const city = params.get("city") || "", id = params.get("id") || "";
    const idMatch = ID_RE.exec(id);
    if (!CITY_RE.test(city) || !idMatch) {
      $("title").textContent = "Meeting not found";
      return;
    }
    $("back").href = `index.html?city=${encodeURIComponent(city)}`;
    const [, date, videoId] = idMatch;
    loadPlayer(videoId);

    let summary, manifest;
    try {
      [summary, manifest] = await Promise.all([
        getJSON(`summaries/${city}/${id}.json`),
        loadManifest().catch(() => ({})),
      ]);
    } catch (e) {
      $("title").textContent = "Meeting not found";
      status(`Could not load this summary (${e.message}).`);
      return;
    }
    const tags = tagIndex(manifest);

    const title = String(summary.Title || id);
    $("title").textContent = title;
    document.title = title;
    const cityInfo = (manifest.cities || []).find((c) => c.slug === city);
    const entry = (manifest.meetings || []).find((m) => m.city === city && m.id === id);
    if (entry && entry.model) $("model").textContent = entry.model;
    const d = parseDate(date === "undated" ? null : date);
    const metaParts = [cityInfo ? cityInfo.name : city];
    if (d) metaParts.push(d.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" }));
    $("meta").replaceChildren(
      metaParts.join(" · ") + " · ",
      el("a", { href: `https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}` }, "Watch on YouTube"),
    );

    const topics = topicsOf(summary);
    const container = $("topics");
    if (!topics.length) status("This summary has no topics.");
    const sections = topics.map((t) => {
      const section = el("section", { class: "topic", id: `topic-${container.children.length + 1}` }, el("h3", {}, t.title));
      if (t.tags.length) {
        section.append(el("div", { class: "tags" }, ...t.tags.map((g) => tagChip(tags, g, {
          href: `index.html?city=${encodeURIComponent(city)}&tags=${encodeURIComponent(g)}`,
          title: `All meetings tagged ${tags.label(g)}`,
        }))));
      }
      section.append(el("p", {}, t.summary));
      if (t.stamps.length) section.append(el("div", { class: "stamps" }, ...t.stamps.map(stampLink)));
      container.append(section);
      return { section, tags: t.tags };
    });

    // Meeting-level tags, most discussed first; selecting one dims topics without it.
    const order = entry && entry.tag_seconds ? Object.keys(entry.tag_seconds)
      : [...new Set(topics.flatMap((t) => t.tags))];
    if (order.length) {
      let selected = null;
      const list = $("meeting-tag-list"), note = $("tag-note");
      const draw = () => {
        list.replaceChildren(...order.map((g) => tagChip(tags, g, {
          pressed: selected === g,
          title: entry && entry.tag_seconds ? `about ${fmtDuration(entry.tag_seconds[g])} of discussion` : undefined,
          onClick: (id) => { selected = selected === id ? null : id; draw(); },
        })));
        for (const s of sections) s.section.classList.toggle("dim", !!selected && !s.tags.includes(selected));
        note.replaceChildren();
        if (selected) {
          const n = sections.filter((s) => s.tags.includes(selected)).length;
          note.append(`${n} topic${n === 1 ? "" : "s"} tagged ${tags.label(selected)}. `,
            el("a", { href: `index.html?city=${encodeURIComponent(city)}&tags=${encodeURIComponent(selected)}` },
              `See all meetings tagged ${tags.label(selected)} →`));
        } else {
          note.append("Select a tag to highlight its topics.");
        }
      };
      draw();
      $("meeting-tags").hidden = false;
    }

    // Timeline under the video, with a playhead that follows playback.
    const spans = entry && entry.topics && entry.topics.length === topics.length ? entry.topics
      : topics.map((t) => ({ title: t.title, tags: t.tags, start: t.stamps.length ? t.stamps[0] : null,
        end: t.stamps.length ? Math.max(t.stamps[t.stamps.length - 1], t.stamps[0] + 60) : null }));
    const tick = drawTimeline(spans.map((s, i) => ({ ...s, title: topics[i].title, tags: topics[i].tags })), tags, sections);
    setInterval(() => tick(currentTime()), 1000);
    const startAt = /#t=(\d+)/.exec(location.hash);
    if (startAt) tick(Number(startAt[1]));

    if (CONFIG.transcriptHost) {
      $("transcript-section").hidden = false;
      $("load-transcript").addEventListener("click", () => loadTranscript(city, id));
    }

    const m = /#t=(\d+)/.exec(location.hash);
    if (m) seek(Number(m[1]), false);
  }

  window.Council = { $, el, getJSON, loadManifest, parseDate, fmtDuration, status, tagIndex, tagChip };

  const page = document.body.dataset.page;
  if (page === "index") renderIndex();
  else if (page === "meeting") renderMeeting();
})();
