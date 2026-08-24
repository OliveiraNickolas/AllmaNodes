import { app } from "../../scripts/app.js";
import { allNodes, targetsOf } from "./allma_graph.js";

/* AllmaComboSelect — a dropdown with no list of its own.
 *
 * When its output is wired into another node's widget, we read that widget's
 * option list and rebuild our own widget as a combo over the same options. The
 * result behaves like the target's dropdown but lives in a separate node, so it
 * can be switched, routed or reused.
 *
 * We prefer the LIVE widget's options.values over the static node definition:
 * plenty of nodes mutate their own list at runtime (refresh buttons, filtered
 * model lists), and the live widget is the only place that shows.
 */

const COMBO_NODE = "AllmaComboSelect";
const PRESET_NODE = "AllmaPresetPrompt";
const IDLE_LABEL = "value";
const IDLE_HINT = "(not connected)";

const MAX_HOPS = 8;

/** The option list of the widget at `slot`, without following anything. */
function directComboSpec(targetNode, slot) {
  const input = targetNode.inputs?.[slot];
  if (!input) return null;
  // A widget-backed input carries the widget name; a plain socket does not.
  const name = input.widget?.name || input.name;
  if (!name) return null;

  const live = targetNode.widgets?.find((w) => w.name === name);
  const values = live?.options?.values;
  if (Array.isArray(values) && values.length) {
    return { values: values.slice(), label: name };
  }

  // Fallback: the node definition, for a widget that has not been built yet.
  const def = targetNode.constructor?.nodeData?.input;
  for (const group of [def?.required, def?.optional]) {
    const spec = group?.[name];
    if (Array.isArray(spec?.[0]) && spec[0].length) {
      return { values: spec[0].slice(), label: name };
    }
  }
  return null;
}

/** Find the dropdown this value eventually lands in.
 *
 * Landing straight on a widget is the easy case. But the whole point of this
 * node is to be switched and routed, and a switch/reroute exposes a wildcard
 * input with no list of its own — so when the immediate target has nothing, we
 * keep walking downstream until we reach a node that does. Depth-capped and
 * cycle-guarded, since a graph can loop back through a router.
 */
function resolveComboSpec(targetNode, slot, depth = 0, seen = new Set()) {
  if (!targetNode || depth > MAX_HOPS) return null;
  const key = `${targetNode.id}:${slot}`;
  if (seen.has(key)) return null;
  seen.add(key);

  const direct = directComboSpec(targetNode, slot);
  if (direct) return direct;

  for (const t of targetsOf(targetNode)) {
    const found = resolveComboSpec(t.node, t.slot, depth + 1, seen);
    if (found) return found;
  }
  return null;
}

/** Swap widget 0 for a fresh one of the right type, keeping its slot index so
 *  widgets_values stays a single-element array across save/load. */
function replaceWidget(node, type, value, extra) {
  if (!node.widgets) node.widgets = [];
  node.widgets.splice(0, 1);
  const w = node.addWidget(type, "value", value, () => {}, extra);
  node.widgets.pop(); // addWidget appended it; move it to the front instead
  node.widgets.splice(0, 0, w);
  return w;
}

function syncComboNode(node) {
  const previous = node.widgets?.[0]?.value ?? "";

  let spec = null;
  for (const t of targetsOf(node)) {
    spec = resolveComboSpec(t.node, t.slot);
    if (spec) break; // first dropdown wins when fanned out to several nodes
  }

  if (!spec) {
    if (node.widgets?.[0]?.type !== "text") {
      replaceWidget(node, "text", previous);
    }
    if (node.outputs?.[0]) node.outputs[0].name = IDLE_LABEL;
    node.title = `Combo Select ${IDLE_HINT}`;
    node.setDirtyCanvas(true, true);
    return;
  }

  // Keep the current selection when the new list still offers it, so
  // reconnecting to an equivalent widget does not silently reset the choice.
  const value = spec.values.includes(previous) ? previous : spec.values[0];
  replaceWidget(node, "combo", value, { values: spec.values });
  if (node.outputs?.[0]) node.outputs[0].name = spec.label;
  node.title = `Combo Select · ${spec.label}`;
  node.setDirtyCanvas(true, true);
}

/* Our list depends on links we are not part of: wiring a switch into its final
 * destination changes what we resolve to, but fires no event on us. So we watch
 * connection changes graph-wide and re-resolve every selector. Debounced, and
 * a no-op when the graph holds no selector at all. */
let resyncTimer = null;

function scheduleResync() {
  if (resyncTimer) return;
  resyncTimer = setTimeout(() => {
    resyncTimer = null;
    for (const node of allNodes(app.graph)) {
      if (node.type === COMBO_NODE) syncComboNode(node);
    }
  }, 0);
}

app.registerExtension({
  name: "allma.selectors.combo",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    // Every node type, not just ours: any rewiring anywhere can change the
    // dropdown a selector resolves to.
    const onConnectionsChange = nodeType.prototype.onConnectionsChange;
    nodeType.prototype.onConnectionsChange = function () {
      onConnectionsChange?.apply(this, arguments);
      scheduleResync();
    };

    if (nodeData.name !== COMBO_NODE) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      onNodeCreated?.apply(this, arguments);
      // Deferred: outputs/links are not wired up yet during construction.
      setTimeout(() => syncComboNode(this), 0);
    };
  },

  async afterConfigureGraph() {
    // Combo lists are not serialized — rebuild them from the restored links.
    scheduleResync();
  },
});

/* AllmaPresetPrompt — the preset list is baked into INPUT_TYPES when ComfyUI
 * starts, so a preset saved afterwards would not appear until a restart. Pull
 * the current list from the server instead. */
app.registerExtension({
  name: "allma.selectors.preset",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== PRESET_NODE) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      onNodeCreated?.apply(this, arguments);
      const w = this.widgets?.find((x) => x.name === "preset");
      if (!w) return;
      fetch("/allma/presets")
        .then((r) => r.json())
        .then((d) => {
          const names = ["(none)", ...(d.presets || [])];
          w.options = w.options || {};
          w.options.values = names;
          if (!names.includes(w.value)) w.value = names[0];
          this.setDirtyCanvas(true, true);
        })
        .catch(() => {}); // offline server: keep whatever the server baked in
    };
  },
});
