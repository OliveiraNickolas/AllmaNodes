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

// ── Inline button row ──────────────────────────────────────────────────────
// LiteGraph gives every widget its own full-width row, so N buttons = N rows.
// This is a single custom widget that paints N buttons side by side and routes
// the click by x position.

const ROW_H = 24;
const ROW_GAP = 4;
const ROW_MARGIN = 15; // matches litegraph's widget side margin

function makeButtonRow(name, buttons) {
  return {
    type: "allma_button_row",
    name,
    value: "",
    // Purely a control surface — must not end up in the saved workflow.
    options: { serialize: false },
    serialize: false,
    _buttons: buttons,
    _hover: -1,
    _pressed: -1,
    _flash: -1,
    _rects: [],

    computeSize(width) {
      return [width ?? 200, ROW_H];
    },

    draw(ctx, node, width, y) {
      const n = this._buttons.length;
      if (!n) return;
      const usable = width - ROW_MARGIN * 2;
      const bw = (usable - ROW_GAP * (n - 1)) / n;
      this._rects = [];

      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.font = "11px Arial";

      for (let i = 0; i < n; i++) {
        const x = ROW_MARGIN + i * (bw + ROW_GAP);
        this._rects.push([x, bw]);
        const hot = this._hover === i;
        const down = this._pressed === i;
        const ok = this._flash === i;
        // Pressed sinks 1px and darkens; the flash is the "it worked" beat for
        // actions like save that otherwise change nothing on screen.
        const oy = down ? 1 : 0;
        const h = ROW_H - 2 - (down ? 1 : 0);

        ctx.fillStyle = ok ? "#2f6b3a" : down ? "#242424" : hot ? "#4b4b4b" : "#353535";
        ctx.strokeStyle = ok ? "#59b06e" : down ? "#111" : "#1a1a1a";
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(x, y + oy, bw, h, 4);
        else ctx.rect(x, y + oy, bw, h);
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = ok ? "#d8ffe0" : down ? "#9a9a9a" : hot ? "#fff" : "#ddd";
        ctx.fillText(
          this._buttons[i].label,
          x + bw / 2,
          y + oy + h / 2,
          bw - 6,
        );
      }
      ctx.restore();
    },

    /** Pressed -> (ação) -> flash verde, com redraw em cada transição. */
    _run(idx, node) {
      const redraw = () => node.setDirtyCanvas(true, true);
      this._pressed = idx;
      redraw();

      const release = () => {
        if (this._pressed === idx) {
          this._pressed = -1;
          redraw();
        }
      };
      const flash = () => {
        this._flash = idx;
        redraw();
        setTimeout(() => {
          if (this._flash === idx) {
            this._flash = -1;
            redraw();
          }
        }, 260);
      };

      let ret;
      try {
        ret = this._buttons[idx].cb();
      } catch (e) {
        console.error("[allma] button row callback failed", e);
        release();
        return;
      }
      // Callbacks are async (fetch) and some open a blocking prompt/confirm;
      // hold the pressed look until the work actually settles.
      // A callback returning false means "nothing happened" (user cancelled a
      // prompt/confirm) — release the button but skip the success flash.
      if (ret && typeof ret.then === "function") {
        ret.then((r) => { release(); if (r !== false) flash(); },
                 (e) => { console.error("[allma] button row failed", e); release(); });
      } else {
        setTimeout(() => { release(); if (ret !== false) flash(); }, 90);
      }
    },

    mouse(event, pos, node) {
      // Vertical hit-testing already happened in litegraph; only x matters.
      const mx = pos[0];
      let idx = -1;
      for (let i = 0; i < this._rects.length; i++) {
        const [x, w] = this._rects[i];
        if (mx >= x && mx <= x + w) {
          idx = i;
          break;
        }
      }
      const t = event.type;
      if (t === "pointermove" || t === "mousemove") {
        if (this._hover !== idx) {
          this._hover = idx;
          node.setDirtyCanvas(true, true);
        }
        return false;
      }
      if ((t === "pointerdown" || t === "mousedown") && idx >= 0) {
        this._run(idx, node);
        return true;
      }
      if (t === "pointerup" || t === "mouseup") {
        // Safety net: a callback that never settles must not leave a stuck button.
        if (this._pressed >= 0) {
          setTimeout(() => {
            if (this._pressed >= 0) {
              this._pressed = -1;
              node.setDirtyCanvas(true, true);
            }
          }, 1200);
        }
        return false;
      }
      return false;
    },
  };
}

const SEP_H = 11;

/** A plain horizontal rule, to visually group widgets above/below it. */
function makeSeparator(name) {
  return {
    type: "allma_separator",
    name,
    value: "",
    options: { serialize: false },
    serialize: false,
    computeSize(width) {
      return [width ?? 200, SEP_H];
    },
    draw(ctx, node, width, y) {
      ctx.save();
      ctx.strokeStyle = "#4a4a4a";
      ctx.lineWidth = 1;
      ctx.beginPath();
      // +0.5 keeps the 1px line crisp instead of blurred across two pixels.
      const yy = Math.round(y + SEP_H / 2) + 0.5;
      ctx.moveTo(ROW_MARGIN, yy);
      ctx.lineTo(width - ROW_MARGIN, yy);
      ctx.stroke();
      ctx.restore();
    },
    mouse() {
      return false;
    },
  };
}

const LABEL_H = 16;
// Floor only — no ceiling, so the boxes keep sharing whatever height the node
// has spare and grow together as it is resized.
const TEXTAREA_MIN_H = 72;

/** Collapse the node to its natural height, keeping the user's width. */
function shrinkToFit(node) {
  if (typeof node.computeSize !== "function") return;
  const s = node.computeSize();
  node.setSize([Math.max(node.size?.[0] ?? s[0], s[0]), s[1]]);
  node.setDirtyCanvas(true, true);
}

/** A dim caption, to title the textarea that follows it.
 *
 * With `onToggle` it also acts as a disclosure control: a chevron is drawn and
 * the whole row becomes the hit area, which is far easier to hit than the
 * glyph alone. `isCollapsed` is read at paint time so the arrow always agrees
 * with the real state, even when something else flipped it. */
function makeLabel(id, text, opts = {}) {
  const toggle = typeof opts.onToggle === "function" ? opts.onToggle : null;
  return {
    type: "allma_label",
    name: id,
    _allmaId: id,
    value: "",
    options: { serialize: false },
    serialize: false,
    computeSize(width) {
      return [width ?? 200, LABEL_H];
    },
    draw(ctx, node, width, y) {
      ctx.save();
      ctx.fillStyle = "#9a9a9a";
      ctx.font = "11px Arial";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      let x = ROW_MARGIN + 2;
      if (toggle) {
        const open = !opts.isCollapsed?.(node);
        ctx.fillText(open ? "▾" : "▸", x, y + LABEL_H / 2);
        x += 11;
      }
      ctx.fillText(text, x, y + LABEL_H / 2);
      if (toggle && opts.isCollapsed?.(node)) {
        ctx.fillStyle = "#6a6a6a";
        ctx.textAlign = "right";
        ctx.fillText("(collapsed)", width - ROW_MARGIN - 2, y + LABEL_H / 2);
      }
      ctx.restore();
    },
    mouse(event, _pos, node) {
      if (!toggle) return false;
      if (event?.type !== "pointerdown" && event?.type !== "mousedown") {
        return false;
      }
      toggle(node);
      return true;
    },
  };
}

/** Collapse or restore a DOM textarea widget.
 *
 * DOM widgets are laid out from computeLayoutSize(), not computeSize(), so
 * zeroing the height means overriding that hook — and the element itself has
 * to be hidden too, or it keeps painting over the node at its last position. */
function setTextareaCollapsed(node, name, collapsed) {
  node._allmaStash = node._allmaStash || {};
  // While collapsed the widget is OUT of node.widgets, so looking it up there
  // finds nothing and expanding would return before restoring anything. The
  // stash is the only reference that survives the collapsed state.
  const w = widget(node, name) || node._allmaStash[name];
  if (!w) return;

  // Nodes 2.0 renders from node.widgets with Vue and ignores the per-widget
  // height hooks below: measured on the live frontend, clicking the label left
  // the node at exactly the same height. The only thing that removes a widget
  // there is taking it out of the list, so that is what actually collapses it.
  if (collapsed) {
    if ((node.widgets || []).includes(w)) {
      node._allmaStash[name] = w;
      node.widgets = (node.widgets || []).filter((x) => x !== w);
    }
  } else if (node._allmaStash[name]) {
    if (!(node.widgets || []).includes(w)) node.widgets.push(w);
    delete node._allmaStash[name];
    // Pushed to the end, so the layout has to be restated.
    applyWidgetOrder(node, GENERATE_ORDER);
  }

  // The legacy renderer honours these, and still draws the element even when
  // the widget is absent from the list — so both paths are needed.
  w.options = w.options || {};
  if (collapsed) {
    w.options.getMinHeight = () => 0;
    w.options.getMaxHeight = () => 0;
    w.computeLayoutSize = () => ({ minHeight: 0, maxHeight: 0, minWidth: 0 });
    if (w.element) w.element.style.display = "none";
  } else {
    w.options.getMinHeight = () => TEXTAREA_MIN_H;
    delete w.options.getMaxHeight;
    delete w.computeLayoutSize;
    if (w.element) w.element.style.display = "";
  }
}

const COLLAPSE_PROP = "allmaSystemCollapsed";

function isSystemCollapsed(node) {
  return !!node?.properties?.[COLLAPSE_PROP];
}

/** Apply the stored collapse state without touching the node's height. */
function syncSystemCollapsed(node) {
  setTextareaCollapsed(node, "system_prompt", isSystemCollapsed(node));
  node.setDirtyCanvas(true, true);
}

function toggleSystemCollapsed(node) {
  node.properties = node.properties || {};
  const collapsing = !isSystemCollapsed(node);
  node.properties[COLLAPSE_PROP] = collapsing;
  setTextareaCollapsed(node, "system_prompt", collapsing);

  // The widget is now genuinely in or out of node.widgets, so the height has to
  // be recomputed rather than merely re-applied — re-applying kept the node at
  // its old size with an empty gap where the textarea had been.
  if (typeof node.computeSize === "function") {
    const s = node.computeSize();
    node.setSize([Math.max(node.size?.[0] ?? s[0], s[0]), s[1]]);
  } else {
    node.setSize(node.size);
  }
  node.setDirtyCanvas(true, true);
}

/** Stable key for ordering: button labels change at runtime, ids don't. */
function widgetId(w) {
  return w._allmaId || w.name;
}

/** Reorder node.widgets to `ids`; anything unlisted keeps its order, after. */
function applyWidgetOrder(node, ids) {
  if (!node.widgets) return;
  const byId = new Map(node.widgets.map((w) => [widgetId(w), w]));
  const ordered = [];
  for (const id of ids) {
    const w = byId.get(id);
    if (w) {
      ordered.push(w);
      byId.delete(id);
    }
  }
  for (const w of node.widgets) if (byId.has(widgetId(w))) ordered.push(w);
  node.widgets.length = 0;
  node.widgets.push(...ordered);
}

// Current layout. This is also the *serialization* order for the widgets that
// serialize, which is why every reshuffle needs a migration below.
const GENERATE_ORDER = [
  "use_image_metadata",
  "read_lora_metadata",
  "allma_sep",
  "preset",
  "preset_actions",
  "stop",
  "allma_lbl_user",
  "user_prompt",
  "allma_lbl_system",
  "system_prompt",
  "enabled",
];

// Serializable widgets per layout revision, in the order their values were
// written. Bumping LAYOUT_VERSION requires appending the previous order here.
const LAYOUT_VERSION = 5;
const VALUE_ORDERS = {
  // v1: original node, before any reordering
  1: ["preset", "system_prompt", "user_prompt",
      "use_image_metadata", "thinking", "read_lora_metadata"],
  // v2: toggles moved above preset
  2: ["use_image_metadata", "thinking", "read_lora_metadata",
      "preset", "system_prompt", "user_prompt"],
  // v3: user_prompt above system_prompt
  3: ["use_image_metadata", "thinking", "read_lora_metadata",
      "preset", "user_prompt", "system_prompt"],
  // v4: `enabled` toggle appended after system_prompt
  4: ["use_image_metadata", "thinking", "read_lora_metadata",
      "preset", "user_prompt", "system_prompt", "enabled"],
  // v5: `thinking` moved to AllmaConnectivity, so one switch covers every
  // Generate node on the same backend. Dropping a name mid-array is exactly
  // what the by-name mapping below handles: the value is simply not carried.
  5: ["use_image_metadata", "read_lora_metadata",
      "preset", "user_prompt", "system_prompt", "enabled"],
};

/**
 * Values are applied positionally to the serializable widgets, so a workflow
 * saved under an older layout would land every value on the wrong widget.
 *
 * v1 is detectable by shape (it starts with `preset`, a string; v2/v3 start
 * with a boolean) but v2 and v3 are identical in shape — they only differ in
 * whether index 4 is the system or the user prompt. Hence the explicit version
 * stamped into node.properties; its absence means "v2 or older".
 */
function migrateGenerateValues(values, properties) {
  if (!Array.isArray(values) || values.length < 6) return values;
  const stamped = Number(properties?.allmaLayout) || 0;
  const from = stamped || (typeof values[0] === "string" ? 1 : 2);
  if (from >= LAYOUT_VERSION) return values;

  const fromOrder = VALUE_ORDERS[from];
  if (!fromOrder) return values;
  // Trailing entries are dead slots from button widgets in older layouts.
  const mapped = VALUE_ORDERS[LAYOUT_VERSION]
    .map((name) => (fromOrder.includes(name) ? values[fromOrder.indexOf(name)] : undefined));
  // Widgets the old layout never had (e.g. `enabled`) must be dropped from the
  // tail rather than handed `undefined` — a short array leaves them on their
  // declared default, an undefined entry would overwrite it with nothing.
  while (mapped.length && mapped[mapped.length - 1] === undefined) mapped.pop();
  return mapped;
}

/** Re-ask the backend for its model list and refresh the dropdown in place.
 *
 * The dropdown is built once, when ComfyUI imports the pack. A backend that was
 * down, still loading, or stuck in a failed load at that moment leaves the list
 * empty until ComfyUI itself is restarted — and a stuck load answers every later
 * request with a bare HTTP 500, so the node looks broken when the backend is.
 * This asks again, live, and reports what the backend says about itself. */
async function reconnect(node, button) {
  const w = widget(node, "model");
  const host = widget(node, "host")?.value || "127.0.0.1";
  const port = widget(node, "port")?.value || 9000;
  const label = button?.name;
  const say = (t) => { if (button) { button.name = t; node.setDirtyCanvas(true, true); } };

  say("connecting…");
  try {
    const r = await fetch(`/allma/models?host=${encodeURIComponent(host)}&port=${port}`);
    const d = await r.json();
    const models = d.models || [];
    if (w && models.length) {
      w.options = w.options || {};
      w.options.values = models;
      // A model that vanished from the backend must not stay selected, or the
      // run fails later with a confusing "no valid model" instead of here.
      if (!models.includes(w.value)) w.value = models[0];
    }
    if (d.error) {
      console.warn(`[AllmaNodes] backend reports: ${d.error} (${d.error_model || "?"})`);
      say(`⚠ ${models.length} model(s) — backend error`);
    } else {
      say(`✓ ${models.length} model(s)`);
    }
  } catch (e) {
    console.warn("[AllmaNodes] reconnect failed", e);
    say("✖ unreachable");
  }
  setTimeout(() => say(label), 4000);
  node.setDirtyCanvas(true, true);
}

function applyConnectivityVisibility(node) {
  const showW = widget(node, "show_sampling");
  const show = showW?.value ?? false;
  console.log(
    `[AllmaNodes] AllmaConnectivity #${node.id}: show_sampling=${show}, `
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

async function applyLastModel(node) {
  // Freshly added node (not a workflow load): default the model widget to
  // the last model actually used, fetched live so it works within the same
  // browser session (the Python-side INPUT_TYPES default only refreshes on
  // page load).
  if (node._allmaConfigured) return;
  try {
    const state = await fetch("/allma/state").then((r) => r.json());
    const last = state?.last_model;
    if (!last || node._allmaConfigured) return;
    const w = widget(node, "model");
    if (w && w.options?.values?.includes(last)) {
      w.value = last;
      node.setDirtyCanvas(true, true);
    }
  } catch (e) {
    console.warn("[AllmaNodes] could not fetch last model", e);
  }
}

// No positional migration for AllmaConnectivity, deliberately.
//
// An earlier build remapped widgets_values by name whenever the array "looked
// like" an older layout. That detection keys off array LENGTH, and length is not
// a reliable signal: converting a widget to an input removes it from the array,
// so a node with `model` linked looks exactly like a pre-change save. The remap
// then fired on every load and shifted every value one place further — the node
// lost its settings a little more on each refresh, silently, until temperature
// read NaN and max_tokens held what top_k used to.
//
// A one-off shift on workflows saved before `thinking` and `effort` existed is
// the smaller cost, and it is visible: wrong numbers show up immediately and are
// corrected by hand once, rather than drifting forever.

app.registerExtension({
  name: "allma.connectivity_ui",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "AllmaConnectivity") return;

    const origCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = origCreated?.apply(this, arguments);
      const btn = this.addWidget("button", "🔌 reconnect", null, () => reconnect(this, btn));
      // BOTH flags. serialize alone still leaves a null hole in widgets_values,
      // and a hole at index 3 shifts every later value by one — which silently
      // reset saved timeouts and token budgets to their defaults.
      btn.serialize = false;
      btn.options = { serialize: false };
      // The label doubles as a status line during a reconnect, so anything that
      // keys off widget names must use this instead — see widgetId().
      btn._allmaId = "reconnect";
      // LEFT LAST ON PURPOSE — do not reorder this into the middle of the node.
      //
      // On load ComfyUI assigns widgets_values POSITIONALLY: values[i] goes to
      // widgets[i]. `serialize: false` governs writing, not reading, so a
      // non-serializing widget sitting at index 3 still consumes the value meant
      // for the widget that used to be there, and every later value slides one
      // place. Re-saving persists the slide, so the node loses its settings a
      // little more on every refresh — temperature ends up NaN and max_tokens
      // holds whatever top_k used to.
      //
      // Appended at the end, there is no saved value at its index, so nothing
      // can shift. The cosmetic cost is that the button sits below the sampling
      // widgets instead of beside the model picker.

      const showW = widget(this, "show_sampling");
      if (showW) {
        const origCb = showW.callback;
        showW.callback = (v) => {
          origCb?.(v);
          applyConnectivityVisibility(this);
        };
      }
      setTimeout(() => {
        applyConnectivityVisibility(this);
        applyLastModel(this);
      }, 0);
      return r;
    };

    const origConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      // Workflow load — the saved model value must win over the last-used
      // default, so flag the node before applyLastModel's fetch resolves.
      this._allmaConfigured = true;
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
    console.warn("[AllmaNodes] refresh failed", e);
  }
}

async function applyPreset(node, name) {
  // Load preset content into the widget AND update the "clean baseline"
  // (_allmaPresetOrig) so dirty tracking knows what "unedited" looks like.
  const sp = widget(node, "system_prompt");
  if (!name || name === "(none)") {
    // Switching to "(none)" — leave whatever content is in the widget and
    // adopt it as the new baseline (so the user isn't nagged about edits).
    if (sp) node._allmaPresetOrig = sp.value || "";
    return;
  }
  const data = await API.load(name);
  if (!data) return;
  if (sp && typeof data.system_prompt === "string") {
    sp.value = data.system_prompt;
    node._allmaPresetOrig = data.system_prompt;
    node.setDirtyCanvas(true, true);
  }
}

function isDirty(node) {
  const sp = widget(node, "system_prompt");
  const orig = node._allmaPresetOrig ?? "";
  return (sp?.value ?? "") !== orig;
}

async function initPresetTracking(node) {
  await refreshPresetDropdown(node);
  const presetW = widget(node, "preset");
  const spW = widget(node, "system_prompt");
  node._allmaLastPreset = presetW?.value || "(none)";
  node._allmaPresetOrig = spW?.value || "";
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
          const prev = this._allmaLastPreset || "(none)";
          if (v === prev) {
            origCb?.(v);
            return;
          }
          if (isDirty(this) && prev !== "(none)") {
            const ok = confirm(
              `You have unsaved edits to preset "${prev}". Switch to "${v}" `
                + `and discard them?`,
            );
            if (!ok) {
              presetW.value = prev;
              this.setDirtyCanvas(true, true);
              return;
            }
          }
          origCb?.(v);
          applyPreset(this, v).then(() => {
            this._allmaLastPreset = v;
          });
        };
      }

      const onNew = async () => {
        const name = prompt("Name for the new preset:");
        if (!name) return false;
        const spVal = widget(this, "system_prompt")?.value || "";
        const res = await API.save(name, { system_prompt: spVal, notes: "" });
        if (res?.ok) {
          await refreshPresetDropdown(this);
          const w = widget(this, "preset");
          if (w) {
            w.value = res.name || name;
            this._allmaLastPreset = res.name || name;
            this._allmaPresetOrig = spVal;
            this.setDirtyCanvas(true, true);
          }
        }
      };

      const onSave = async () => {
        const w = widget(this, "preset");
        const name = w?.value;
        if (!name || name === "(none)") {
          alert("Select a preset first (or use ➕ new).");
          return false;
        }
        const spVal = widget(this, "system_prompt")?.value || "";
        await API.save(name, { system_prompt: spVal, notes: "" });
        // Widget content is now what's stored — no longer dirty.
        this._allmaPresetOrig = spVal;
      };

      const onReload = async () => {
        const w = widget(this, "preset");
        if (isDirty(this) && w?.value && w.value !== "(none)") {
          const ok = confirm(
            `You have unsaved edits to preset "${w.value}". Reload from disk `
              + "and discard them?",
          );
          if (!ok) return false;
        }
        await refreshPresetDropdown(this);
        if (w?.value) await applyPreset(this, w.value);
        this._allmaLastPreset = w?.value || "(none)";
      };

      const onDelete = async () => {
        const w = widget(this, "preset");
        const name = w?.value;
        if (!name || name === "(none)") return false;
        if (!confirm(`Delete preset "${name}"?`)) return false;
        await API.del(name);
        await refreshPresetDropdown(this);
        // Dropdown fell back to "(none)". Widget content is left as-is;
        // adopt it as the clean baseline so no false-dirty warnings fire.
        this._allmaLastPreset = w?.value || "(none)";
        this._allmaPresetOrig = widget(this, "system_prompt")?.value || "";
      };

      const row = makeButtonRow("preset_actions", [
        { label: "➕ new", cb: onNew },
        { label: "💾 save", cb: onSave },
        { label: "🔄 reload", cb: onReload },
        { label: "🗑️ delete", cb: onDelete },
      ]);
      this.addCustomWidget(row);
      this.addCustomWidget(makeSeparator("allma_sep"));
      this.addCustomWidget(makeLabel("allma_lbl_user", "prompt"));
      this.addCustomWidget(
        makeLabel("allma_lbl_system", "system prompt", {
          onToggle: toggleSystemCollapsed,
          isCollapsed: isSystemCollapsed,
        }),
      );

      // The textareas are DOM widgets. computeLayoutSize() reads these hooks,
      // and the layout pass splits the node's spare height between whatever
      // widgets have room to grow. Setting only a floor keeps them compact when
      // the node is small and lets both expand in step when it is dragged
      // taller; capping getMaxHeight here would freeze them instead.
      for (const name of ["system_prompt", "user_prompt"]) {
        const w = widget(this, name);
        if (!w?.options) continue;
        w.options.getMinHeight = () => TEXTAREA_MIN_H;
        delete w.options.getMaxHeight;
        if (Array.isArray(w.options.minNodeSize)) {
          w.options.minNodeSize = [w.options.minNodeSize[0], 120];
        }
      }

      applyWidgetOrder(this, GENERATE_ORDER);
      this.properties = this.properties || {};
      this.properties.allmaLayout = LAYOUT_VERSION;

      setTimeout(() => {
        initPresetTracking(this);
        syncSystemCollapsed(this);
        // Only a freshly dropped node is auto-sized. One coming from a
        // workflow already had its size restored by configure(), which runs
        // between onNodeCreated and this callback — shrinking here would
        // silently discard the size the user saved.
        if (!this._allmaConfigured) shrinkToFit(this);
      }, 0);
      return r;
    };

    // Remap values written under the old widget order before litegraph
    // assigns them positionally. Must wrap `configure` itself — `onConfigure`
    // fires after the values have already landed.
    const origConfigureFn = nodeType.prototype.configure;
    nodeType.prototype.configure = function (info) {
      if (info && Array.isArray(info.widgets_values)) {
        info.widgets_values = migrateGenerateValues(
          info.widgets_values,
          info.properties,
        );
      }
      const out = origConfigureFn?.apply(this, arguments);
      // Marks this node as deserialized rather than newly dropped, so the
      // deferred auto-size in onNodeCreated leaves its restored size alone.
      this._allmaConfigured = true;
      // Stamp the current revision so this node re-saves as v3.
      this.properties = this.properties || {};
      this.properties.allmaLayout = LAYOUT_VERSION;
      // Deliberately no shrinkToFit here: the workflow carries the size the
      // user resized this node to, and collapsing it would discard that.
      return out;
    };

    const origConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = origConfigure?.apply(this, arguments);
      // After a workflow load, take whatever preset/system_prompt came in
      // as the clean baseline — user saved it in that state on purpose.
      // syncSystemCollapsed only re-applies the saved flag; it never resizes,
      // so the node keeps the height stored in the workflow.
      setTimeout(() => {
        initPresetTracking(this);
        syncSystemCollapsed(this);
      }, 0);
      return r;
    };

    // litegraph writes widgets_values by absolute widget index, so a
    // `serialize: false` widget leaves a null hole. Loading, however, walks
    // only the serializable widgets and consumes values sequentially — so that
    // hole shifts every later value by one on each save/load round trip.
    // Rewrite the array compacted, which is exactly what loading expects and
    // is also the shape older workflows already have.
    const origSerialize = nodeType.prototype.onSerialize;
    nodeType.prototype.onSerialize = function (o) {
      origSerialize?.apply(this, arguments);
      if (!o || !Array.isArray(o.widgets_values) || !this.widgets) return;
      o.widgets_values = this.widgets
        .filter((w) => w.serialize !== false)
        .map((w) => {
          const v = w.value;
          return typeof v === "object" && v
            ? JSON.parse(JSON.stringify(v))
            : v ?? null;
        });
    };
  },
});

// ── Stop button ────────────────────────────────────────────────────────────
// Aborts the in-flight LLM call the way a chat UI does: the backend drops the
// connection, llama.cpp sees the disconnect and frees its slot, and the node
// returns whatever text had already been generated.

const IDLE_LABEL = "🟥 stop generation";

async function allmaInterrupt(node) {
  const w = node._allmaStopWidget;
  if (w) {
    w.name = "⏳ stopping…";
    node.setDirtyCanvas(true, true);
  }
  try {
    const res = await fetch("/allma/interrupt", { method: "POST" }).then((r) =>
      r.json(),
    );
    if (w) {
      // `live: false` means nothing was actually streaming — say so instead of
      // pretending we stopped something.
      w.name = res?.live ? "✅ stopped" : "· nothing running";
    }
  } catch (e) {
    console.error("[allma] interrupt failed", e);
    if (w) w.name = "⚠ interrupt failed";
  } finally {
    node.setDirtyCanvas(true, true);
    setTimeout(() => {
      if (node._allmaStopWidget) {
        node._allmaStopWidget.name = IDLE_LABEL;
        node.setDirtyCanvas(true, true);
      }
    }, 1600);
  }
}

app.registerExtension({
  name: "allma.stop_ui",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    // The standalone node is nothing but this button, so it is wired up here
    // rather than in its own extension — same handler, same label states.
    if (nodeData?.name === "AllmaStop") {
      const origStop = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function () {
        const r = origStop?.apply(this, arguments);
        const stop = this.addWidget("button", IDLE_LABEL, null,
          () => allmaInterrupt(this));
        stop._allmaId = "stop";
        stop.serialize = false;
        stop.options = { serialize: false };
        this._allmaStopWidget = stop;
        this.size = [230, 58];
        return r;
      };
      return;
    }
    if (nodeData?.name !== "AllmaGenerate") return;

    const origCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = origCreated?.apply(this, arguments);
      const stop = this.addWidget(
        "button",
        IDLE_LABEL,
        null,
        () => allmaInterrupt(this),
      );
      // Its label changes while stopping, so ordering keys off a stable id.
      stop._allmaId = "stop";
      stop.serialize = false;
      this._allmaStopWidget = stop;
      // Runs after the preset extension, so this is where the full layout —
      // stop button included — can finally be put in order.
      applyWidgetOrder(this, GENERATE_ORDER);
      setTimeout(() => {
        if (!this._allmaConfigured) shrinkToFit(this);
      }, 0);
      return r;
    };
  },
});
