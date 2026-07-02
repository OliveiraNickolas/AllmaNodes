import { app } from "../../scripts/app.js";

const API = {
  list: () => fetch("/allma/presets").then((r) => r.json()),
  load: (name) =>
    fetch(`/allma/presets/${encodeURIComponent(name)}`).then((r) =>
      r.ok ? r.json() : null
    ),
  save: (name, body) =>
    fetch("/allma/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, ...body }),
    }).then((r) => r.json()),
  del: (name) =>
    fetch(`/allma/presets/${encodeURIComponent(name)}`, { method: "DELETE" }),
};

function widget(node, name) {
  return node.widgets?.find((w) => w.name === name);
}

const SAMPLING_WIDGETS = [
  "temperature",
  "top_p",
  "top_k",
  "max_tokens",
  "seed",
  "control_after_generate",
  "control after generate",
];

function hasOwn(obj, key) {
  return Object.prototype.hasOwnProperty.call(obj, key);
}

function toggleWidget(w, show) {
  if (!w) return;
  if (show) {
    if (!w._allmaOrig) return;
    const bak = w._allmaOrig;
    w.type = bak.type;
    if (bak.hadOwnComputeSize) {
      w.computeSize = bak.computeSize;
    } else {
      delete w.computeSize;
    }
    if (bak.hadOwnDraw) {
      w.draw = bak.draw;
    } else {
      delete w.draw;
    }
    delete w._allmaOrig;
  } else {
    if (!w._allmaOrig) {
      w._allmaOrig = {
        type: w.type,
        hadOwnComputeSize: hasOwn(w, "computeSize"),
        computeSize: w.computeSize,
        hadOwnDraw: hasOwn(w, "draw"),
        draw: w.draw,
      };
    }
    w.type = "converted-widget";
    w.computeSize = () => [0, -4];
    w.draw = () => {};
    if (typeof w.serializeValue !== "function") {
      w.serializeValue = () => w.value;
    }
  }
}

function applyConnectivityVisibility(node) {
  const showW = widget(node, "show_sampling");
  const show = showW?.value ?? false;
  console.log(
    `[ComfyUI-Allma] AllmaConnectivity #${node.id}: show_sampling=${show}, `
      + `hiding=${SAMPLING_WIDGETS.filter((n) => widget(node, n)).length} widgets`,
  );
  for (const name of SAMPLING_WIDGETS) {
    toggleWidget(widget(node, name), show);
  }
  if (typeof node.computeSize === "function") {
    const s = node.computeSize();
    node.setSize([Math.max(node.size?.[0] ?? s[0], s[0]), s[1]]);
  }
  node.setDirtyCanvas(true, true);
  if (app.canvas) app.canvas.setDirty(true, true);
}

app.registerExtension({
  name: "allma.connectivity_ui",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "AllmaConnectivity") return;

    const origCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = origCreated?.apply(this, arguments);
      const showW = widget(this, "show_sampling");
      if (showW) {
        const origCb = showW.callback;
        showW.callback = (v) => {
          origCb?.(v);
          applyConnectivityVisibility(this);
        };
      }
      setTimeout(() => applyConnectivityVisibility(this), 0);
      return r;
    };

    const origConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = origConfigure?.apply(this, arguments);
      setTimeout(() => applyConnectivityVisibility(this), 0);
      return r;
    };
  },
});

async function refreshPresetDropdown(node) {
  try {
    const { presets } = await API.list();
    const w = widget(node, "preset");
    if (!w) return;
    const values = presets && presets.length ? ["(none)", ...presets] : ["(none)"];
    w.options.values = values;
    if (!values.includes(w.value)) w.value = "(none)";
    node.setDirtyCanvas(true, true);
  } catch (e) {
    console.warn("[ComfyUI-Allma] refresh failed", e);
  }
}

async function applyPreset(node, name) {
  if (!name || name === "(none)") return;
  const data = await API.load(name);
  if (!data) return;
  const sp = widget(node, "system_prompt");
  if (sp && typeof data.system_prompt === "string") {
    sp.value = data.system_prompt;
    node.setDirtyCanvas(true, true);
  }
}

app.registerExtension({
  name: "allma.preset_ui",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "AllmaGenerate") return;

    const origCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = origCreated?.apply(this, arguments);

      const presetW = widget(this, "preset");
      if (presetW) {
        const origCb = presetW.callback;
        presetW.callback = (v) => {
          origCb?.(v);
          applyPreset(this, v);
        };
      }

      this.addWidget("button", "➕ new", null, async () => {
        const name = prompt("Nome do novo preset:");
        if (!name) return;
        const spVal = widget(this, "system_prompt")?.value || "";
        const res = await API.save(name, { system_prompt: spVal, notes: "" });
        if (res?.ok) {
          await refreshPresetDropdown(this);
          const w = widget(this, "preset");
          if (w) {
            w.value = res.name || name;
            this.setDirtyCanvas(true, true);
          }
        }
      });

      this.addWidget("button", "💾 save", null, async () => {
        const w = widget(this, "preset");
        const name = w?.value;
        if (!name || name === "(none)") {
          alert("Selecione um preset primeiro (ou use 'new preset').");
          return;
        }
        const spVal = widget(this, "system_prompt")?.value || "";
        await API.save(name, { system_prompt: spVal, notes: "" });
      });

      this.addWidget("button", "🔄 reload", null, async () => {
        const w = widget(this, "preset");
        await refreshPresetDropdown(this);
        if (w?.value) await applyPreset(this, w.value);
      });

      this.addWidget("button", "🗑️ delete", null, async () => {
        const w = widget(this, "preset");
        const name = w?.value;
        if (!name || name === "(none)") return;
        if (!confirm(`Deletar preset "${name}"?`)) return;
        await API.del(name);
        await refreshPresetDropdown(this);
      });

      setTimeout(() => refreshPresetDropdown(this), 0);
      return r;
    };
  },
});
