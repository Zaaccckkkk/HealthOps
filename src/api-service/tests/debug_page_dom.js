// Minimal DOM stand-in to execute the debug page script under Node for regression tests.
// Only what the page uses: createElement, appendChild, replaceChildren, textContent, className,
// getElementById, addEventListener, value, sessionStorage, Intl.
"use strict";
const fs = require("fs");
const vm = require("vm");

class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ""; this.className = ""; this.value = ""; this.listeners = {}; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  get text() { return this.textContent + this.children.map(c => c.text).join(" "); }
}
const ids = {};
for (const id of ["token", "tz", "provider", "question", "ask", "fresh", "busy", "out"]) ids[id] = new Node(id);
const sandbox = {
  document: { createElement: (tag) => new Node(tag), getElementById: (id) => ids[id] },
  sessionStorage: { getItem: () => null, setItem: () => {} },
  Intl: { DateTimeFormat: () => ({ resolvedOptions: () => ({ timeZone: "UTC" }) }) },
  JSON, console, window: {},
};
sandbox.window = sandbox;
const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.slice(html.indexOf("<script>") + 8, html.indexOf("</script>"));
vm.createContext(sandbox);
vm.runInContext(script, sandbox);
const response = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
sandbox.__healthopsRender(response);
const text = ids.out.text;
process.stdout.write(JSON.stringify({ text, children: ids.out.children.length }));
