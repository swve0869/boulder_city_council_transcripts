// Counts one page view on the host's /hit endpoint (see serve_transcripts.py).
// Sends only a page key like "meeting:boulder/2026-10-22_abc": no cookies, no identifiers,
// nothing stored in the browser. Skipped when the visitor asks not to be tracked.
(function () {
  "use strict";
  const config = window.COUNCIL_CONFIG || {};
  if (!config.countVisits || !config.transcriptHost) return;
  if (navigator.doNotTrack === "1" || window.doNotTrack === "1" || navigator.globalPrivacyControl) return;
  if (!navigator.sendBeacon) return;

  const page = document.body.dataset.page || "index";
  const params = new URLSearchParams(location.search);
  const city = params.get("city");
  let key = page;
  if (page === "meeting" && city && params.get("id")) key = `meeting:${city}/${params.get("id")}`;
  else if (page === "issue" && params.get("tag")) key = `issue:${city || "boulder"}/${params.get("tag")}`;
  else if (city && page !== "about") key = `${page}:${city}`;

  try {
    // text/plain keeps this a "simple" cross-origin request: no preflight, no CORS round trip.
    navigator.sendBeacon(`${config.transcriptHost.replace(/\/+$/, "")}/hit`,
      new Blob([JSON.stringify({ p: key })], { type: "text/plain" }));
  } catch (_) { /* counting must never break the page */ }
})();
