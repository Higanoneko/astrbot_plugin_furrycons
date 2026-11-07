// Dependency-free behavior checks against the real settings scripts and HTML.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const read = file => fs.readFileSync(path.join(root, file), "utf8");

function surface() {
  const ids = new Map();
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.attributes = {}; this._value = ""; this.checked = false; this.hidden = false; this.disabled = false; this.textContent = ""; this.files = []; }
    set id(value) { this.attributes.id = value; ids.set(value, this); }
    get id() { return this.attributes.id; }
    set value(value) { this._value = String(value); if (this.tag === "input" && this.type === "file" && value === "") this.files = []; }
    get value() { return this._value; }
    append(...children) { for (const child of children) { child.parent = this; this.children.push(child); if (this.tag === "select" && this.children.length === 1) this.value = child.value; } }
    replaceChildren(...children) { this.children = []; this._value = ""; this.append(...children); }
    after() {}
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] || null; }
    removeAttribute(name) { delete this.attributes[name]; }
    scrollIntoView() {}
    querySelectorAll(selector) { const tags = selector.split(",").map(tag => tag.trim()); return this.children.flatMap(child => [...(tags.includes(child.tag) ? [child] : []), ...child.querySelectorAll(selector)]); }
    addEventListener(event, callback) { (this.listeners[event] ||= []).push(callback); }
    async emit(event) { if (event === "click" && this.disabled) return; for (const callback of this.listeners[event] || []) await callback({preventDefault() {}}); if (["input", "change"].includes(event) && this.parent) await this.parent.emit(event); }
  }
  const document = {documentElement: {dataset: {}}, getElementById: id => ids.get(id), createElement: tag => new Element(tag), createTextNode: value => Object.assign(new Element("#text"), {textContent: value})};
  const stack = [new Element("document")];
  for (const token of read("views/settings/index.html").matchAll(/<\/?[a-z][^>]*>/gi)) {
    const tag = /^<\/?([a-z][\w-]*)/i.exec(token[0])[1];
    if (token[0].startsWith("</")) { while (stack.length > 1 && stack.at(-1).tag !== tag) stack.pop(); if (stack.at(-1).tag === tag) stack.pop(); continue; }
    const element = new Element(tag);
    for (const attr of token[0].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
      const [name, value] = [attr[1], attr[2]];
      if (name === "id") element.id = value;
      if (["value", "type"].includes(name) && value !== undefined) element[name] = value;
      if (["hidden", "disabled"].includes(name)) element[name] = true;
    }
    stack.at(-1).append(element);
    if (!["input", "meta", "link", "img", "br"].includes(tag)) stack.push(element);
  }
  return {document, get: id => { assert.ok(ids.has(id), `Missing UI element: ${id}`); return ids.get(id); }};
}

function settings() {
  const schema = JSON.parse(read("_conf_schema.json"));
  return {schema, values: Object.fromEntries(Object.entries(schema).map(([key, item]) => [key, item.default])), actions: ["查询"], revision: "initial", active_entry: "FEC", requires_reload: false, api_key_configured: true};
}
const job = state => ({job: {state, logs: [], message: "任务状态"}, environment: {guidance: "", system: "test", python: "python"}, preview_available: false});
const theme = {key: "local:sample", name: "Sample", source: "local", available: true, colors: {primary: "#445566"}, mode: "light", advanced: {}, layouts: ["compact", "grid"]};
const catalog = () => ({themes: [theme, {...theme, key: "local:copy"}], session_defaults: [], known_chats: []});

async function start({getSettings, getBrowser, post, getThemes, upload, withThemes = false} = {}) {
  const ui = surface();
  const calls = [];
  const timers = new Map();
  let nextTimer = 0;
  const bridge = {
    ready: async () => {}, onContext() {}, getContext: () => ({}),
    async apiGet(endpoint) { calls.push(["GET", endpoint]); if (endpoint === "settings") return structuredClone(await (getSettings ? getSettings() : settings())); if (endpoint === "browser") return getBrowser ? getBrowser() : job("idle"); return getThemes ? getThemes() : catalog(); },
    async apiPost(endpoint, payload) { calls.push(["POST", endpoint]); return post ? post(endpoint, payload) : {}; },
    async upload(endpoint, file) { calls.push(["UPLOAD", endpoint]); return upload(endpoint, file); },
  };
  const context = vm.createContext({document: ui.document, window: {AstrBotPluginView: bridge, addEventListener() {}}, structuredClone, setTimeout: callback => {const id = ++nextTimer; timers.set(id, callback); return id;}, clearTimeout: id => timers.delete(id), initializeThemeManager: async () => {}});
  if (withThemes) vm.runInContext(read("views/settings/theme-manager.js").replace("export async function", "async function"), context);
  await vm.runInContext("(async()=>{" + read("views/settings/app.js").replace(/^import[^\n]*\n/, "") + "})()", context);
  return {...ui, calls, timers};
}

async function main() {
  let failures = 1;
  const first = await start({getSettings: () => {if (failures--) throw Error("offline"); return settings();}});
  assert.match(first.get("status").textContent, /读取失败/);
  assert.equal(first.get("page-tools").hidden, false);
  assert.equal(first.get("save").disabled, true);
  await first.get("refresh").emit("click");
  assert.equal(first.get("settings").hidden, false);
  assert.equal(first.get("save").disabled, false);

  let rejectRead = false;
  let rejectSave = true;
  const draft = await start({getSettings: () => {if (rejectRead) throw Error("read offline"); return settings();}, post: async (endpoint, payload) => {if (rejectSave) throw Error("save offline"); return {...settings(), values: payload.values, requires_reload: true, message: "配置已保存"};}});
  draft.get("command_entry").value = "展会";
  draft.get("api_key").value = "temporary-test-value";
  await draft.get("command_entry").emit("input");
  assert.match(draft.get("status").textContent, /未保存/);
  assert.equal(draft.get("discard-settings").hidden, false);
  assert.match(draft.get("api_key").getAttribute("aria-describedby"), /key-status/);
  const reads = draft.calls.length;
  await draft.get("refresh").emit("click");
  assert.equal(draft.calls.length, reads, "Guard must preserve the draft before any reread");
  assert.equal(draft.get("command_entry").value, "展会");
  assert.equal(draft.get("api_key").value, "temporary-test-value");
  await draft.get("settings").emit("submit");
  assert.equal(draft.get("command_entry").value, "展会");
  assert.equal(draft.get("api_key").value, "temporary-test-value");
  rejectRead = true;
  await draft.get("discard-settings").emit("click");
  assert.equal(draft.get("command_entry").value, "展会", "Failed discard/read must keep inputs");
  rejectRead = false;
  await draft.get("discard-settings").emit("click");
  assert.equal(draft.get("command_entry").value, "FEC");
  assert.equal(draft.get("api_key").value, "");
  draft.get("command_entry").value = "展会";
  await draft.get("command_entry").emit("change");
  rejectSave = false;
  await draft.get("settings").emit("submit");
  assert.equal(draft.get("discard-settings").hidden, true);
  assert.match(draft.get("status").textContent, /配置已保存/);

  let browserReads = 0;
  const recovery = await start({getBrowser: () => {browserReads++; if (browserReads === 2) throw Error("one disconnect"); return job(browserReads === 1 ? "installing" : "succeeded");}});
  assert.equal(recovery.get("browser-install").disabled, true);
  const poll = [...recovery.timers.values()][0]; recovery.timers.clear(); await poll();
  assert.equal(recovery.get("browser-install").disabled, true);
  assert.match(recovery.get("browser-job").textContent, /自动重试/);
  assert.equal(recovery.timers.size, 1);
  const retry = [...recovery.timers.values()][0]; recovery.timers.clear(); await retry();
  assert.equal(recovery.get("browser-error").hidden, true);
  assert.equal(recovery.get("browser-install").disabled, false);
  await recovery.get("browser-refresh").emit("click");
  assert.equal(browserReads, 4);
  const unknown = await start({getBrowser: () => job("unknown")});
  assert.equal(unknown.get("browser-install").disabled, true);
  for (const serverState of ["installing", "idle", "succeeded"]) {
    let requested = false;
    const uncertain = await start({getBrowser: () => job(requested ? serverState : "idle"), post: async () => {requested = true; throw Error("installation response lost");}});
    await uncertain.get("browser-install").emit("click");
    assert.equal(uncertain.get("browser-install").disabled, serverState === "installing");
    assert.equal(uncertain.get("browser-error").hidden, serverState !== "idle", "A rejected idle install needs its original error; recovered jobs use current status");
  }

  for (const action of ["default", "clear", "import", "update", "copy"]) {
    let committed = false, catalogFail = true, writes = 0;
    const ui = await start({withThemes: true, getThemes: () => {if (committed && catalogFail) throw Error("catalog offline"); return catalog();}, post: async () => {committed = true; writes++; return {message: "主题设置已保存", result: {key: "local:copy"}};}, upload: async () => {committed = true; writes++; return {message: "主题已安装", theme: {key: "local:copy"}};}});
    ui.get("theme-file").files = [{name: "test.zip", size: 123}];
    ui.get("theme-file").value = "test.zip";
    ui.get("theme-copy-id").value = "copy";
    ui.get("theme-copy-name").value = "副本";
    await ui.get("theme-" + action).emit("click");
    assert.equal(writes, 1);
    assert.match(ui.get("theme-error").textContent, /变更已保存，但主题列表读取失败/);
    assert.notEqual(ui.get("theme-result").textContent, "");
    assert.equal(ui.get("theme-" + action).disabled, true);
    assert.equal(ui.get("theme-refresh").disabled, false);
    if (["import", "update"].includes(action)) assert.equal(ui.get("theme-file").files.length, 0);
    if (action === "copy") assert.equal(ui.get("theme-copy-id").value, "");
    await ui.get("theme-" + action).emit("click");
    assert.equal(writes, 1, "Committed action must not be submitted again");
    await ui.get("theme-refresh").emit("click");
    assert.match(ui.get("theme-error").textContent, /变更已保存/);
    catalogFail = false;
    await ui.get("theme-refresh").emit("click");
    assert.equal(writes, 1, "Recovery must only read the catalog");
    assert.equal(ui.get("theme-error").hidden, true);
    assert.equal(ui.get("theme-" + action).disabled, false);
  }
  console.log("Settings recovery behavior checks passed (drafts, first load, save failure, discard failure, browser retry, unknown status, 5 committed theme actions).");
}
main().catch(error => {console.error(error); process.exitCode = 1;});
