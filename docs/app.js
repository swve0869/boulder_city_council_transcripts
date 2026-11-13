// Renders the index and meeting pages from the summary JSON files in summaries/.
// Summaries come from an LLM, so every string is inserted with textContent, never innerHTML.
(function () {
  "use strict";

  const CONFIG = window.COUNCIL_CONFIG || {};
  const CITY_RE = /^[a-z0-9-]+$/;
  const ID_RE = /^(\d{4}-\d{2}-\d{2}|undated)_([A-Za-z0-9_-]+)$/;

  const $ = (id) => document.getElementById(id);

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else node.setAttribute(k, v);
    }
    for (const c of children) node.append(c);
    return node;
  }

  async function getJSON(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
    return res.json();
  }

  function parseDate(s) {
    return s ? new Date(`${s}T00:00:00`) : null;
  }

  function fmtTime(seconds) {
    const s = Math.floor(seconds);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    const mm = String(m).padStart(h ? 2 : 1, "0"), ss = String(sec).padStart(2, "0");
    return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
  }

  function status(msg) {
    const s = $("status");
    s.textContent = msg || "";
    s.hidden = !msg;
  }

  // ---- Summary format (see summary_prompt_JSON_format.json) ------------------------------

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
        });
      }
    }
    return out;
  }

  // ---- Index page ---------------------------------------------------------------------

  async function renderIndex() {
    let manifest;
    try {
      manifest = await getJSON("summaries/index.json", { cache: "no-cache" });
    } catch (e) {
      status(`Could not load the meeting list (${e.message}).`);
      return;
    }
    const cities = manifest.cities || [];
    const meetings = manifest.meetings || [];
    if (!meetings.length) {
      status("No meeting summaries yet.");
      return;
    }
    status("");

    const select = $("city");
    let chosen = new URLSearchParams(location.search).get("city");
    try { chosen = chosen || localStorage.getItem("city"); } catch (_) { /* storage unavailable */ }
    if (!cities.some((c) => c.slug === chosen)) chosen = cities[0] && cities[0].slug;

    if (cities.length > 1) {
      for (const c of cities) select.append(el("option", { value: c.slug }, c.name));
      select.value = chosen;
      $("city-picker").hidden = false;
      select.addEventListener("change", () => {
        try { localStorage.setItem("city", select.value); } catch (_) { /* ignore */ }
        draw(select.value);
      });
    }

    function draw(city) {
      const list = $("list");
      list.replaceChildren();
      const groups = new Map();
      for (const m of meetings.filter((m) => m.city === city)) {
        const year = m.date ? m.date.slice(0, 4) : "Undated";
        if (!groups.has(year)) groups.set(year, []);
        groups.get(year).push(m);
      }
      for (const [year, items] of groups) {
        const ul = el("ul", { class: "meetings" });
        for (const m of items) {
          const d = parseDate(m.date);
          const label = d ? d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "Undated";
          const href = `meeting.html?city=${encodeURIComponent(m.city)}&id=${encodeURIComponent(m.id)}`;
          ul.append(el("li", {}, el("span", { class: "date" }, label), el("a", { href }, m.title || m.id)));
        }
        list.append(el("h2", {}, year), ul);
      }
    }
    draw(chosen);
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
    history.replaceState(null, "", `#t=${Math.floor(t)}`);
    highlightTranscript(t);
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
    const m = /^#t=(\d+)/.exec(location.hash);
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
        getJSON("summaries/index.json", { cache: "no-cache" }).catch(() => ({})),
      ]);
    } catch (e) {
      $("title").textContent = "Meeting not found";
      status(`Could not load this summary (${e.message}).`);
      return;
    }

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
    for (const t of topics) {
      const section = el("section", { class: "topic" }, el("h3", {}, t.title), el("p", {}, t.summary));
      if (t.stamps.length) section.append(el("div", { class: "stamps" }, ...t.stamps.map(stampLink)));
      container.append(section);
    }

    if (CONFIG.transcriptHost) {
      $("transcript-section").hidden = false;
      $("load-transcript").addEventListener("click", () => loadTranscript(city, id));
    }

    const m = /^#t=(\d+)/.exec(location.hash);
    if (m) seek(Number(m[1]), false);
  }

  const page = document.body.dataset.page;
  if (page === "index") renderIndex();
  else if (page === "meeting") renderMeeting();
})();
