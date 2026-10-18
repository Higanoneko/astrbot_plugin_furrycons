export async function initializeThemeManager(bridge) {
  const byId = id => document.getElementById(id);
  const panel = byId("theme-panel");
  const selection = byId("theme-select");
  const result = byId("theme-result");
  const error = byId("theme-error");
  const dirty = new Set();
  let themes = [];
  let selected;
  let previewKind;
  let busy = false;

  function controls() {
    panel.querySelectorAll("input,select").forEach(input => input.disabled = busy);
    byId("theme-chat").disabled = busy || byId("theme-scope").value !== "session";
    if (!selected?.available) {
      byId("theme-primary").disabled = true;
      byId("theme-mode").disabled = true;
    }
    panel.querySelectorAll("button").forEach(button => button.disabled = busy);
    for (const id of ["theme-preview-list", "theme-preview-detail", "theme-export", "theme-colors", "theme-reset", "theme-default", "theme-copy"]) {
      byId(id).disabled = busy || !selected?.available;
    }
    byId("theme-update").disabled = busy || !selected?.available || !selected.key.startsWith("local:");
  }

  function say(message) { result.textContent = message; }
  function failure(message = "") { error.textContent = message; error.hidden = !message; }
  async function run(action) {
    if (busy) return;
    busy = true;
    controls();
    failure();
    try { await action(); }
    catch (e) { failure(e.message || "主题操作失败，请检查配置后重试。"); }
    finally { busy = false; controls(); }
  }

  function choose() {
    selected = themes.find(theme => theme.key === selection.value);
    dirty.clear();
    previewKind = undefined;
    byId("theme-preview-box").hidden = true;
    byId("theme-preview-image").removeAttribute("src");
    byId("theme-info").textContent = selected ? `${selected.source} · ${selected.key} · ${selected.version || "不可用"}${selected.origin ? " · 来源 " + selected.origin : ""}` : "尚无主题";
    const advanced = byId("theme-advanced");
    advanced.replaceChildren();
    if (selected?.available) {
      byId("theme-primary").value = selected.colors.primary;
      byId("theme-mode").value = selected.mode;
      for (const [name, labelText] of Object.entries(selected.advanced)) {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "color";
        input.id = "theme-advanced-" + name;
        input.value = selected.colors[name];
        input.addEventListener("input", () => dirty.add(name));
        label.htmlFor = input.id;
        label.append(document.createTextNode(labelText), input);
        advanced.append(label);
      }
    }
    controls();
  }

  async function load(key = selection.value) {
    const data = await bridge.apiGet("themes");
    themes = data.themes;
    selection.replaceChildren();
    for (const theme of themes) {
      const option = document.createElement("option");
      option.value = theme.key;
      option.textContent = `${theme.name} · ${theme.source} · ${theme.version || "不可用"}`;
      selection.append(option);
    }
    if (themes.some(theme => theme.key === key)) selection.value = key;
    byId("theme-defaults").textContent = `全局：${data.global_default || "初始 Material"}\n` + data.session_defaults.map(row => `${row.chat}：${row.key}`).join("\n");
    byId("theme-chats").replaceChildren();
    const chats = new Set([...data.known_chats, ...data.session_defaults.map(row => row.chat)]);
    for (const chat of chats) {
      const option = document.createElement("option"); option.value = chat; byId("theme-chats").append(option);
    }
    choose();
  }

  async function action(name, values) {
    return bridge.apiPost("themes/action", { action: name, ...values });
  }

  async function preview(kind) {
    say("正在使用当前主题模板渲染示例…");
    const data = await bridge.apiPost("themes/preview", { key: selected.key, kind });
    byId("theme-preview-image").src = data.image;
    byId("theme-preview-image").alt = `${selected.name} · ${kind === "list" ? "列表" : "详情"}示例预览`;
    byId("theme-preview-caption").textContent = `${selected.name} · ${kind === "list" ? "列表图" : "详情图"}；${data.message}`;
    byId("theme-preview-box").hidden = false;
    previewKind = kind;
    say("预览已生成，内容为示例资料。");
  }

  selection.addEventListener("change", choose);
  byId("theme-refresh").addEventListener("click", () => run(() => load()));
  for (const kind of ["list", "detail"]) byId("theme-preview-" + kind).addEventListener("click", () => run(() => preview(kind)));
  byId("theme-export").addEventListener("click", () => run(async () => {
    const [namespace, identity] = selected.key.split(":");
    await bridge.download(`themes/export/${namespace}/${identity}`, {}, `${identity}.zip`);
    say("主题 ZIP 已导出；独立配色继续保存在宿主中。");
  }));
  byId("theme-mode").addEventListener("change", () => {
    if (!selected?.available) return;
    const mode = byId("theme-mode").value;
    for (const name of Object.keys(selected.advanced)) {
      if (!dirty.has(name)) byId("theme-advanced-" + name).value = selected.overrides?.advanced?.[name] || selected.color_defaults.advanced[name][mode];
    }
  });
  byId("theme-colors").addEventListener("click", () => run(async () => {
    const changes = {};
    if (byId("theme-primary").value !== selected.colors.primary) changes.primary = byId("theme-primary").value;
    if (byId("theme-mode").value !== selected.mode) changes.mode = byId("theme-mode").value;
    if (dirty.size) changes.advanced = Object.fromEntries([...dirty].map(name => [name, byId("theme-advanced-" + name).value]));
    if (!Object.keys(changes).length) { say("配色未改变。"); return; }
    const previousPreview = previewKind;
    const data = await action("set_colors", { key: selected.key, changes });
    await load(); say(data.message);
    if (previousPreview) await preview(previousPreview);
  }));
  byId("theme-reset").addEventListener("click", () => run(async () => {
    const data = await action("reset_colors", { key: selected.key }); await load(); say(data.message);
  }));
  byId("theme-scope").addEventListener("change", () => byId("theme-chat").disabled = byId("theme-scope").value !== "session");
  async function setDefault(key) {
    const scope = byId("theme-scope").value;
    const data = await action("set_default", { key, scope, ...(scope === "session" ? { chat: byId("theme-chat").value.trim() } : {}) });
    await load(); say(data.message);
  }
  byId("theme-default").addEventListener("click", () => run(() => setDefault(selected.key)));
  byId("theme-clear").addEventListener("click", () => run(() => setDefault(null)));
  for (const mode of ["import", "update"]) byId("theme-" + mode).addEventListener("click", () => run(async () => {
    const file = byId("theme-file").files[0];
    if (!file || !file.name.toLowerCase().endsWith(".zip") || file.size > 32 * 1024 * 1024) throw new Error("请选择不超过 32 MiB 的主题 ZIP。");
    const [namespace, identity] = selected?.key.split(":") || [];
    const endpoint = mode === "import" ? "themes/import" : `themes/update/${namespace}/${identity}`;
    const data = await bridge.upload(endpoint, file);
    await load(data.theme.key); say(data.message); byId("theme-file").value = "";
  }));
  byId("theme-copy").addEventListener("click", () => run(async () => {
    const data = await action("copy", { key: selected.key, identity: byId("theme-copy-id").value.trim(), name: byId("theme-copy-name").value.trim() || null });
    await load(data.result.key); say("布局与配色已另存本地副本，默认值未改变。");
  }));
  await run(() => load());
}
