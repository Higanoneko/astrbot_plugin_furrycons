export async function initializeThemeManager(bridge) {
  const byId = id => document.getElementById(id);
  const panel = byId("theme-panel");
  const selection = byId("theme-select");
  const result = byId("theme-result");
  const error = byId("theme-error");
  const dirty = new Set();
  const drafts = new Map();
  let themes = [];
  let selected;
  let previewKind;
  let busy = false;
  let pendingSync;

  function controls() {
    const locked = busy || !!pendingSync;
    panel.querySelectorAll("input,select").forEach(input => input.disabled = locked);
    byId("theme-chat").disabled = locked || byId("theme-scope").value !== "session";
    if (!selected?.available) {
      byId("theme-primary").disabled = true;
      byId("theme-mode").disabled = true;
    }
    panel.querySelectorAll("button").forEach(button => button.disabled = locked);
    byId("theme-refresh").disabled = busy;
    for (const id of ["theme-preview-list", "theme-preview-detail", "theme-export", "theme-colors", "theme-reset", "theme-default", "theme-copy"]) {
      byId(id).disabled = locked || !selected?.available;
    }
    byId("theme-update").disabled = locked || !selected?.available || !selected.key.startsWith("local:");
    byId("theme-layout").disabled = locked || !selected?.layouts?.length;
  }

  function say(message) { result.textContent = message; }
  function failure(message = "") { error.textContent = message; error.hidden = !message; }
  function changes() {
    if (!selected?.available) return {};
    const values = {};
    if (byId("theme-primary").value !== selected.colors.primary) values.primary = byId("theme-primary").value;
    if (byId("theme-mode").value !== selected.mode) values.mode = byId("theme-mode").value;
    if (dirty.size) values.advanced = Object.fromEntries([...dirty].map(name => [name, byId("theme-advanced-" + name).value]));
    return values;
  }
  function rememberDraft() {
    if (!selected?.available) return;
    const values = changes();
    if (Object.keys(values).length) drafts.set(selected.key, values);
    else drafts.delete(selected.key);
  }
  function draftNotice() { say("配色有未保存的修改；预览显示已保存的配色，请先保存以预览修改。"); }
  async function run(action) {
    if (busy) return;
    busy = true;
    controls();
    failure();
    try { await action(); }
    catch (e) { failure(e.message || "主题操作失败，请检查配置后重试。"); }
    finally { busy = false; controls(); }
  }

  function choose(preferredLayout = null, preserveCurrent = true) {
    if (preserveCurrent) rememberDraft();
    selected = themes.find(theme => theme.key === selection.value);
    dirty.clear();
    previewKind = undefined;
    byId("theme-preview-box").hidden = true;
    byId("theme-preview-image").removeAttribute("src");
    byId("theme-info").textContent = selected ? `${selected.source} · ${selected.key} · ${selected.version || "不可用"}${selected.origin ? " · 来源 " + selected.origin : ""}` : "尚无主题";
    const advanced = byId("theme-advanced");
    advanced.replaceChildren();
    const layouts = byId("theme-layout");
    layouts.replaceChildren();
    const layoutNames = {compact:"紧凑图文", poster:"大图海报", grid:"双列目录"};
    for (const value of selected?.layouts?.length ? selected.layouts : [""]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = layoutNames[value] || "使用主题自身排版";
      layouts.append(option);
    }
    if (selected?.layouts?.includes(preferredLayout)) layouts.value = preferredLayout;
    if (selected?.available) {
      byId("theme-primary").value = selected.colors.primary;
      byId("theme-mode").value = selected.mode;
      for (const [name, labelText] of Object.entries(selected.advanced)) {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "color";
        input.id = "theme-advanced-" + name;
        input.value = selected.colors[name];
        input.addEventListener("input", () => { dirty.add(name); draftNotice(); });
        label.htmlFor = input.id;
        label.append(document.createTextNode(labelText), input);
        advanced.append(label);
      }
    }
    const draft = selected && drafts.get(selected.key);
    if (selected?.available && draft) {
      if (draft.primary) byId("theme-primary").value = draft.primary;
      if (draft.mode) {
        byId("theme-mode").value = draft.mode;
        for (const name of Object.keys(selected.advanced)) {
          byId("theme-advanced-" + name).value = selected.overrides?.advanced?.[name] || selected.color_defaults.advanced[name][draft.mode];
        }
      }
      for (const [name, value] of Object.entries(draft.advanced || {})) {
        const input = byId("theme-advanced-" + name);
        if (input) { input.value = value; dirty.add(name); }
      }
      draftNotice();
    } else {
      say("");
    }
    controls();
  }

  async function load(key = selection.value, preserveCurrent = true) {
    const previousLayout = selected?.key === key ? byId("theme-layout").value : null;
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
    choose(previousLayout, preserveCurrent);
  }

  async function action(name, values) {
    return bridge.apiPost("themes/action", { action: name, ...values });
  }

  async function readCommitted() {
    const committed = pendingSync;
    try {
      await load(committed.key);
      pendingSync = undefined;
      say(committed.message);
    } catch (e) {
      say(committed.message);
      failure("变更已保存，但主题列表读取失败：" + (e.message || "暂时无法连接。") + " 请只点击“刷新主题”重新读取，无需重复提交。");
    }
  }

  async function committed(message, key = selection.value) {
    pendingSync = {message, key};
    await readCommitted();
  }

  async function preview(kind) {
    say("正在使用当前主题模板渲染示例…");
    const layout = byId("theme-layout").value;
    const data = await bridge.apiPost("themes/preview", { key: selected.key, kind, ...(kind === "list" && layout ? {layout} : {}) });
    byId("theme-preview-image").src = data.image;
    byId("theme-preview-image").alt = `${selected.name} · ${kind === "list" ? "列表" : "详情"}示例预览`;
    byId("theme-preview-caption").textContent = `${selected.name} · ${kind === "list" ? "列表图" : "详情图"}；${data.message}`;
    byId("theme-preview-box").hidden = false;
    previewKind = kind;
    say(Object.keys(changes()).length
      ? "预览使用已保存的配色；当前修改尚未保存，保存后将重新生成预览。"
      : "预览已生成，内容为示例资料，使用已保存的配色。");
  }

  selection.addEventListener("change", () => choose());
  byId("theme-layout").addEventListener("change", () => {
    byId("theme-preview-box").hidden = true;
    previewKind = undefined;
  });
  byId("theme-refresh").addEventListener("click", () => run(() => pendingSync ? readCommitted() : load()));
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
    draftNotice();
  });
  byId("theme-primary").addEventListener("input", draftNotice);
  byId("theme-colors").addEventListener("click", () => run(async () => {
    const values = changes();
    if (!Object.keys(values).length) { say("配色未改变。"); return; }
    const previousPreview = previewKind;
    const key = selected.key;
    const data = await action("set_colors", { key, changes: values });
    drafts.delete(key);
    try { await load(key, false); }
    catch (e) {
      selected = undefined;
      say("配色已保存。请刷新主题后继续操作。");
      failure("配色已保存，但主题列表读取失败：" + (e.message || "请刷新主题。"));
      return;
    }
    say(data.message);
    if (previousPreview) {
      try { await preview(previousPreview); }
      catch (e) { failure("配色已保存，但预览刷新失败：" + (e.message || "请重试预览。")); }
    }
  }));
  byId("theme-reset").addEventListener("click", () => run(async () => {
    const key = selected.key;
    const data = await action("reset_colors", { key });
    drafts.delete(key);
    try { await load(key, false); }
    catch (e) {
      selected = undefined;
      say("配色已重置。请刷新主题后继续操作。");
      failure("配色已重置，但主题列表读取失败：" + (e.message || "请刷新主题。"));
      return;
    }
    say(data.message);
  }));
  byId("theme-scope").addEventListener("change", () => byId("theme-chat").disabled = byId("theme-scope").value !== "session");
  async function setDefault(key) {
    const scope = byId("theme-scope").value;
    const data = await action("set_default", { key, scope, ...(scope === "session" ? { chat: byId("theme-chat").value.trim() } : {}) });
    await committed(data.message);
  }
  byId("theme-default").addEventListener("click", () => run(() => setDefault(selected.key)));
  byId("theme-clear").addEventListener("click", () => run(() => setDefault(null)));
  for (const mode of ["import", "update"]) byId("theme-" + mode).addEventListener("click", () => run(async () => {
    const file = byId("theme-file").files[0];
    if (!file || !file.name.toLowerCase().endsWith(".zip") || file.size > 32 * 1024 * 1024) throw new Error("请选择不超过 32 MiB 的主题 ZIP。");
    const [namespace, identity] = selected?.key.split(":") || [];
    const endpoint = mode === "import" ? "themes/import" : `themes/update/${namespace}/${identity}`;
    const data = await bridge.upload(endpoint, file);
    byId("theme-file").value = "";
    await committed(data.message, data.theme.key);
  }));
  byId("theme-copy").addEventListener("click", () => run(async () => {
    const data = await action("copy", { key: selected.key, identity: byId("theme-copy-id").value.trim(), name: byId("theme-copy-name").value.trim() || null });
    byId("theme-copy-id").value = "";
    byId("theme-copy-name").value = "";
    await committed("布局与配色已另存本地副本，默认值未改变。", data.result.key);
  }));
  await run(() => load());
}
