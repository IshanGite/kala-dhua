// The raven page: loads without errors, shows a badge for waiting approvals, speaks a reminder, and a click (not a drag) asks the app to bring the window back.
import { JSDOM, VirtualConsole } from "jsdom";
import { readFileSync } from "node:fs";

const html = readFileSync("src/ragnarok/ui/mascot.html", "utf8");
const calls = [];
const allCalls = [];
const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errors.push(String(e)));
const dom = new JSDOM(html, { runScripts: "dangerously", url: "http://127.0.0.1:1/mascot#token=abc", pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.fetch = (u, o) => { calls.push([u, o && o.method]); allCalls.push([u, o && o.method, o && o.body ? JSON.parse(o.body) : null]); const body = u === "/shell/poll" ? { prompt: null, edge: "left", pinned: "left" } : u === "/approvals" ? { items: [{ id: "a1", headline: "Send this chat message" }] } : u === "/inbox" ? { items: [{ id: "r1", kind: "reminder", title: "Reminder: call mom" }] } : { ok: true };
      return Promise.resolve({ json: () => Promise.resolve(body) }); };
  } });
const { window } = dom, doc = window.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ok = true;
const check = (name, c) => { ok = ok && !!c; console.log(`${c ? "PASS" : "FAIL"}  ${name}`); };
await sleep(600);
check("the page runs without script errors", errors.length === 0);
check("a badge shows the waiting approvals", doc.getElementById("dot").textContent === "1" && doc.getElementById("dot").style.display === "block");
check("the raven announces the approval waiting first", /Waiting for your OK/.test(doc.getElementById("bubble").textContent));
await sleep(7600);
check("then the reminder, one at a time", /Reminder: call mom/.test(doc.getElementById("bubble").textContent));
const stage = doc.getElementById("row");
stage.dispatchEvent(new window.MouseEvent("mousedown", { bubbles: true, screenX: 10, screenY: 10 }));
doc.dispatchEvent(new window.MouseEvent("mouseup", { bubbles: true }));
check("a click asks the app to bring the window back", calls.some((c) => c[0] === "/shell/restore" && c[1] === "POST"));
calls.length = 0;
stage.dispatchEvent(new window.MouseEvent("mousedown", { bubbles: true, screenX: 10, screenY: 10 }));
doc.dispatchEvent(new window.MouseEvent("mousemove", { bubbles: true, screenX: 40, screenY: 10 }));
doc.dispatchEvent(new window.MouseEvent("mouseup", { bubbles: true }));
check("a drag moves the raven and does NOT restore the window", calls.some((c) => c[0] === "/shell/mascot/move") && calls.some((c) => c[0] === "/shell/mascot/dropped") && !calls.some((c) => c[0] === "/shell/restore"));
await sleep(1200);
check("the bolt sits at the left edge and the bubble opens into the screen", /left/.test(doc.getElementById("row").className));
check("the window is asked to grow so the words are not cut off", calls.length >= 0 && allCalls.some((c) => c[0] === "/shell/mascot/size" && c[2] && c[2].w > 72));
window.close();
process.exit(ok ? 0 : 1);
