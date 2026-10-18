import { initializeThemeManager } from "./theme-manager.js";
const bridge = window.AstrBotPluginView;
const form = document.getElementById("settings");
const status = document.getElementById("status");
const error = document.getElementById("error");
const saveButton = document.getElementById("save");
const refreshButton = document.getElementById("refresh");
const fields = new Map();
const groups = [
  ["数据源与鉴权", ["api_base", "api_key", "timezone"]],
  ["查询与缓存", ["page_size", "cache_minutes", "stale_hours", "interaction_hours"]],
  ["命令入口", ["command_entry", "function_aliases"]],
  ["裸触发开关", ["bare_numbers", "bare_names", "bare_ids"]],
  ["详情总结", ["ai_summary", "summary_provider_id", "summary_timeout"]],
  ["浏览器参数", ["browser_path", "render_timeout", "browser_download_host", "browser_download_proxy", "browser_download_timeout"]],
];
let state;
let browserTimer;
const runningJobs = new Set(["starting", "installing", "checking"]);
const browserError = document.getElementById("browser-error");
const browserProbe = document.getElementById("browser-probe");
const browserInstall = document.getElementById("browser-install");
const browserPreview = document.getElementById("browser-preview");

function browserFailure(message = "") {
  browserError.textContent = message;
  browserError.hidden = !message;
}

function showBrowser(data) {
  const labels = {idle:"尚无安装记录", starting:"准备中", installing:"安装中", checking:"渲染自检中", succeeded:"安装验证通过", failed:"失败", interrupted:"已中断", needs_configuration:"需要修正当前配置"};
  document.getElementById("browser-job").textContent = `安装任务：${labels[data.job.state] || data.job.state}。${data.job.message}`;
  document.getElementById("browser-log").textContent = data.job.logs.join("\n");
  document.getElementById("browser-guidance").textContent = data.environment.guidance;
  document.getElementById("browser-python").textContent = `宿主系统：${data.environment.system}；AstrBot Python：${data.environment.python}`;
  browserInstall.disabled = runningJobs.has(data.job.state);
  browserPreview.disabled = !data.preview_available;
  if (data.probe) {
    const result = data.probe;
    document.getElementById("browser-result").textContent = `${result.message} Playwright ${result.playwright}；浏览器：${result.browser || "未确认"}；路径：${result.browser_path}。`;
  } else if (data.job.active_probe) {
    document.getElementById("browser-result").textContent = `上次安装后检测：${data.job.active_probe.message}`;
  }
  clearTimeout(browserTimer);
  if (runningJobs.has(data.job.state)) browserTimer = setTimeout(readBrowser, 3000);
}

async function readBrowser() {
  try { showBrowser(await bridge.apiGet("browser")); }
  catch (e) { browserFailure(e.message || "无法读取安装状态，请重新打开页面。"); }
}

browserProbe.addEventListener("click", async () => {
  browserProbe.disabled = true;
  browserFailure();
  document.getElementById("browser-result").textContent = "正在检测启动、系统依赖、中文字体和实际渲染…";
  try { showBrowser(await bridge.apiPost("browser/probe", {})); }
  catch (e) { browserFailure(e.message || "检测失败，请检查宿主运行环境。"); }
  finally { browserProbe.disabled = false; }
});

browserInstall.addEventListener("click", async () => {
  browserInstall.disabled = true;
  browserFailure();
  try { showBrowser(await bridge.apiPost("browser/install", {})); }
  catch (e) { browserFailure(e.message || "安装任务创建失败。"); browserInstall.disabled = false; }
});

browserPreview.addEventListener("click", async () => {
  browserFailure();
  try { await bridge.download("browser/preview", {}, "furrycons-browser-check.png"); }
  catch (e) { browserFailure(e.message || "下载失败，请重新检测后再试。"); }
});
window.addEventListener("beforeunload", () => clearTimeout(browserTimer));

function showError(message) {
  error.textContent = message;
  error.hidden = !message;
  if (message) error.scrollIntoView({ block: "center", behavior: "smooth" });
}

function setBusy(value) {
  saveButton.disabled = value;
  refreshButton.disabled = value;
}

function updateStatus(message = "") {
  const pending = state.requires_reload ? "有已保存的变更，重载插件后生效。" : "当前配置已生效。";
  status.textContent = `${message} ${pending} 当前运行入口：${state.active_entry}。`;
}

function createFields(data) {
  const parent = document.getElementById("groups");
  parent.replaceChildren();
  fields.clear();
  for (const [title, names] of groups) {
    const section = document.createElement("section");
    const heading = document.createElement("h2");
    heading.textContent = title;
    section.append(heading);
    for (const name of names) {
      const schema = data.schema[name];
      const label = document.createElement("label");
      label.htmlFor = name;
      const text = document.createElement("span");
      text.textContent = schema.description;
      const input = document.createElement(schema.type === "list" ? "textarea" : "input");
      input.id = name;
      input.name = name;
      if (schema.type === "bool") {
        input.type = "checkbox";
        label.className = "toggle";
      } else if (schema.type === "int" || schema.type === "float") {
        input.type = "number";
        input.step = schema.type === "int" ? "1" : "any";
      } else if (name === "api_key") {
        input.type = "password";
        input.autocomplete = "new-password";
        input.spellcheck = false;
      } else if (schema.type === "list") {
        input.rows = 4;
      } else {
        input.type = "text";
      }
      label.append(text, input);
      section.append(label);
      if (schema.hint) {
        const hint = document.createElement("p");
        hint.className = "hint";
        hint.id = `${name}-hint`;
        hint.textContent = schema.hint;
        input.setAttribute("aria-describedby", hint.id);
        section.append(hint);
      }
      fields.set(name, { input, type: schema.type });
      if (name === "api_key") {
        const note = document.createElement("p");
        note.id = "key-status";
        note.className = "hint";
        section.append(note);
        const clearLabel = document.createElement("label");
        clearLabel.className = "toggle";
        const clear = document.createElement("input");
        clear.type = "checkbox";
        clear.id = "clear-key";
        clearLabel.append(clear, document.createTextNode("清除已保存的 API Key"));
        section.append(clearLabel);
      }
    }
    parent.append(section);
  }
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = `可绑定功能：${data.actions.join("、")}。每行一个“快捷入口=功能”；实际唤醒前缀沿用 AstrBot 配置。`;
  fields.get("function_aliases").input.after(hint);
}

function fill(data) {
  state = data;
  for (const [name, { input, type }] of fields) {
    if (type === "bool") input.checked = data.values[name];
    else input.value = name === "api_key" ? "" : type === "list" ? data.values[name].join("\n") : data.values[name];
  }
  document.getElementById("clear-key").checked = false;
  document.getElementById("key-status").textContent = `${data.api_key_configured ? "已配置" : "未配置"}。留空保留已有 Key；填写新值替换。`;
  updateStatus();
}

async function load() {
  setBusy(true);
  showError("");
  try {
    const data = await bridge.apiGet("settings");
    if (!state) createFields(data);
    fill(data);
    form.hidden = false;
    document.getElementById("management").hidden = false;
  } catch (e) {
    showError(e.message || "读取配置失败，请重开页面后重试。");
  } finally { setBusy(false); }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setBusy(true);
  showError("");
  const values = {};
  for (const [name, { input, type }] of fields) {
    values[name] = type === "bool" ? input.checked : type === "list" ? input.value.split(/\r?\n/).map(x => x.trim()).filter(Boolean) : type === "int" || type === "float" ? Number(input.value) : input.value;
  }
  try {
    const result = await bridge.apiPost("settings", { values, revision: state.revision, clear_api_key: document.getElementById("clear-key").checked });
    fill(result);
    updateStatus(result.message);
    status.scrollIntoView({ block: "start", behavior: "smooth" });
  } catch (e) {
    showError(e.message || "保存失败，配置尚未确认写入。");
  } finally { setBusy(false); }
});
refreshButton.addEventListener("click", load);

if (!bridge) {
  status.textContent = "请从 AstrBot 插件详情中的可视化视图打开此页。";
  showError("未连接 AstrBot 页面 bridge。");
} else {
  await bridge.ready();
  bridge.onContext(context => document.documentElement.dataset.theme = context.isDark ? "dark" : "light");
  document.documentElement.dataset.theme = bridge.getContext()?.isDark ? "dark" : "light";
  await load();
  await readBrowser();
  await initializeThemeManager(bridge);
}
