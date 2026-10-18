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
  ["浏览器参数", ["browser_path", "render_timeout"]],
];
let state;

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
}
