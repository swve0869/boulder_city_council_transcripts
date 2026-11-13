// Minimal Chrome DevTools Protocol driver for the browser tests (no npm packages).
// Needs Node 20+ (run with --experimental-websocket on Node 20/21) and a Chromium started
// with --remote-debugging-port=$CDP_PORT (run.sh does both).
import fs from "fs";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const BASE = (process.argv[2] || "http://localhost:8920").replace(/\/$/, "");
export const LOCAL = /localhost|127\.0\.0\.1/.test(BASE);
const PORT = process.env.CDP_PORT || 9333;

export async function connect() {
  const targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  const sock = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
  let id = 0;
  const pending = {}, errors = [];
  sock.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.method === "Runtime.exceptionThrown") errors.push(d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text);
    if (d.method === "Runtime.consoleAPICalled" && d.params.type === "error") errors.push(d.params.args.map((a) => a.value || a.description).join(" "));
    if (pending[d.id]) { pending[d.id](d.result); delete pending[d.id]; }
  };
  await new Promise((r) => (sock.onopen = r));
  const send = (method, params = {}) => new Promise((r) => { pending[++id] = r; sock.send(JSON.stringify({ id, method, params })); });
  await send("Runtime.enable");
  // Evaluate an expression in the page; page-side exceptions count as test errors.
  const ev = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) errors.push("eval: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    return r.result?.value;
  };
  const go = async (path, wait = 1800) => { await send("Page.navigate", { url: path.startsWith("http") ? path : `${BASE}/${path}` }); await sleep(wait); };
  const size = (w, h, dark = false) => Promise.all([
    send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 }),
    send("Emulation.setEmulatedMedia", { features: dark ? [{ name: "prefers-color-scheme", value: "dark" }] : [] })]);
  const shot = async (path) => { await sleep(250); const r = await send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(path, Buffer.from(r.data, "base64")); };
  const type = (sel, v) => ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.value = ${JSON.stringify(v)}; e.dispatchEvent(new Event("input")); e.dispatchEvent(new Event("change")); })()`);
  const click = (sel, n = 0) => ev(`document.querySelectorAll(${JSON.stringify(sel)})[${n}].click()`);
  const overflow = () => ev(`document.documentElement.scrollWidth > innerWidth`);
  return { send, ev, go, size, shot, type, click, overflow, errors };
}

// Collects named checks; finish() prints a summary and sets the exit code.
export function checker(name) {
  const failures = [];
  const check = (label, ok, detail = "") => {
    console.log(`${ok ? "  ok  " : "  FAIL"} ${label}${ok || detail === "" ? "" : `: ${JSON.stringify(detail)}`}`);
    if (!ok) failures.push(label);
  };
  const finish = (c) => {
    check("no JavaScript errors or CSP violations", c.errors.length === 0, c.errors);
    console.log(failures.length ? `${name}: ${failures.length} FAILED` : `${name}: passed`);
    process.exit(failures.length ? 1 : 0);
  };
  return { check, finish };
}
