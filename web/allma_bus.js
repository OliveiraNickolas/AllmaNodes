import { app } from "../../scripts/app.js";
import { resolveUpstream } from "./allma_graph.js";

/* Names for the data bus, mirrored from the sending node to the receiving one.
 *
 * The backend carries values only. Names live here because a rename is not
 * something a generation depends on, and keeping them out of the payload means
 * the bus still works from an API prompt where no browser ever named anything.
 *
 * A slot's name defaults to whatever was plugged into it — the source's own
 * output label — because that is right most of the time and costs no typing.
 * Right-clicking a slot renames it, and a manual name is never overwritten by a
 * later reconnection.
 */

const IN_NODE = "AllmaBusIn";
const OUT_NODE = "AllmaBusOut";
const PROP_PREFIX = "name_";
const COLLAPSE_PROP = "allmaNamesCollapsed";
const TOGGLE_ID = "allma_names_toggle";

function isCollapsed(node) {
  // Collapsed by default: the names are set once and read rarely, so the node
  // should not carry two dozen boxes around for the other 99% of the time.
  return node?.properties?.[COLLAPSE_PROP] !== false;
}

/** Property key that holds slot `i`'s name (0-based index, 1-based key). */
function propKey(i) {
  return PROP_PREFIX + (i + 1);
}

function nameWidget(node, i) {
  return node.widgets?.find((w) => w.name === propKey(i));
}

/** Names for each slot of a Bus In node, index-aligned with its LINK inputs.
 *
 * The value lives in a real widget so the Parameters panel can edit it — that
 * panel lists widgets, and nothing else. */
function namesOf(node) {
  return linkInputs(node).map((inp, i) => {
    const written = nameWidget(node, i)?.value;
    if (written) return String(written);
    if (inp.link == null) return null; // empty slot carries no name
    return autoName(node, inp) || inp.name;
  });
}

/** Only the bus slots — the name widgets are inputs too, and must not count. */
function linkInputs(node) {
  return (node.inputs || []).filter((i) => !String(i.name).startsWith(PROP_PREFIX));
}

/** Keep one editable property per connected slot, seeded from the source.
 *
 * The seed is what makes this usable: a slot arrives already labelled with
 * whatever was plugged into it, and the panel is only for when that name is not
 * good enough — "MODEL" becoming "Speed Lora". */
/** Push the current names onto the slots and the receiving node. */
function applyNames(node) {
  const slots = linkInputs(node);
  slots.forEach((inp, i) => {
    const w = nameWidget(node, i);
    inp.label = w?.value ? String(w.value) : undefined;
  });
  node.setDirtyCanvas(true, true);
  mirrorConsumers(node.graph);
}

function syncProps(node) {
  const slots = linkInputs(node);
  const collapsed = isCollapsed(node);

  const toggle = node.widgets?.find((w) => w._allmaId === TOGGLE_ID);
  if (toggle) {
    const n = slots.filter((i) => i.link != null).length;
    toggle.name = `${collapsed ? "▸" : "▾"} slot names (${n})`;
  }

  slots.forEach((inp, i) => {
    const w = nameWidget(node, i);
    if (!w) return;
    if (inp.link != null && !w.value) w.value = autoName(node, inp) || inp.name || "";
    inp.label = w.value ? String(w.value) : undefined;
  });
  applyVisibility(node);
  resize(node);
}

/* Showing and hiding the name boxes.
 *
 * Nodes 2.0 renders widgets with Vue, from node.widgets, and ignores every
 * per-widget flag that used to work: `type = "hidden"`, `hidden = true`,
 * `options.hidden`, `type = "converted-widget"` and an emptied `draw()` were all
 * measured against the live frontend and all still drew 24 boxes.
 *
 * The only thing that removes them is taking them out of node.widgets — and
 * that also empties the Parameters panel, because the panel reads the same live
 * list rather than the schema. There is no "in the panel but off the node"
 * state to aim for, so the collapse has to move them in and out for real:
 * folded, the node is two rows tall and the panel is empty; unfolded, both show
 * the fields.
 */
function applyVisibility(node) {
  const all = node._allmaNameWidgets || [];
  if (!all.length) return;
  // Expanded shows EVERY slot, not just the wired ones.
  //
  // Trimming to the live slots meant a name beyond that point was absent from
  // node.widgets — and the Parameters panel reads that same list, so those
  // slots simply could not be renamed from the panel at all. Expanding is a
  // deliberate act; showing the full set is the price of the panel working for
  // every slot.
  const keep = isCollapsed(node) ? 0 : all.length;
  const others = (node.widgets || []).filter((w) => !all.includes(w));
  node.widgets = [...others, ...all.slice(0, keep)];
  resize(node);
}

function resize(node) {
  if (typeof node.computeSize !== "function") return;
  const s = node.computeSize();
  node.setSize([Math.max(node.size?.[0] ?? s[0], s[0]), s[1]]);
}

/** The label of whatever feeds this input, which is the name worth inheriting. */
function autoName(node, input) {
  const links = node.graph?.links;
  const link = links?.get ? links.get(input.link) : links?.[input.link];
  if (!link) return null;
  const origin = node.graph?.getNodeById?.(link.origin_id);
  const out = origin?.outputs?.[link.origin_slot];
  return out?.label || out?.name || origin?.title || null;
}

/** Rewrite a Bus Out node's outputs to match the bus feeding it. */
function mirror(node) {
  const src = resolveUpstream(node, "bus");
  const names = src?.node?.type === IN_NODE ? namesOf(src.node) : [];
  let shown = 0;

  (node.outputs || []).forEach((out, i) => {
    const name = names[i];
    if (name) {
      // Written to every field a renderer might read: litegraph draws `label`,
      // the Vue node renderer has been seen using `localized_name`, and `name`
      // is the fallback. Links reference outputs by INDEX, so renaming is safe.
      out.label = name;
      out.localized_name = name;
      out.name = name;
      out.hidden = false;
      shown = i + 1;
    } else {
      // Past the last named slot there is nothing to offer. Hiding rather than
      // removing keeps the indices stable, so an existing wire on slot 5 still
      // points at slot 5 after the bus grows or shrinks.
      out.label = undefined;
      out.hidden = !(out.links && out.links.length);
    }
  });

  // Trailing hidden slots would still reserve height; collapse to what is used.
  if (typeof node.computeSize === "function") {
    const s = node.computeSize();
    node.setSize([Math.max(node.size?.[0] ?? s[0], s[0]), s[1]]);
  }
  node.setDirtyCanvas(true, true);
  return shown;
}

/** Every Bus Out fed by this Bus In, so a rename propagates immediately. */
function mirrorConsumers(graph) {
  for (const n of graph?._nodes || []) {
    if (n.type === OUT_NODE) mirror(n);
  }
}

app.registerExtension({
  name: "allma.bus",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name === IN_NODE) {
      const origCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function () {
        const r = origCreated?.apply(this, arguments);
        setTimeout(() => syncProps(this), 0);
        return r;
      };

      const origConn = nodeType.prototype.onConnectionsChange;
      nodeType.prototype.onConnectionsChange = function (...args) {
        const r = origConn?.apply(this, args);
        this._allmaBusSynced = false;
        setTimeout(() => {
          syncProps(this);
          mirrorConsumers(this.graph);
        }, 0);
        return r;
      };

      // The Parameters panel writes widget.value DIRECTLY — it never calls the
      // widget's callback — so hooking the callback catches renames typed on the
      // node and misses every rename typed in the panel, which is the one place
      // these widgets are visible. Intercepting the property itself catches both.
      const origCreated2 = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function (...a) {
        const r = origCreated2?.apply(this, a);
        const node = this;

        // One row that opens the whole block, the way the system prompt on
        // Allma Generate works. Collapsed the node stays two rows tall; open it
        // and every slot can be renamed in place, without leaving the canvas.
        const toggle = node.addWidget("button", "▸ slot names", null, () => {
          node.properties = node.properties || {};
          node.properties[COLLAPSE_PROP] = !isCollapsed(node);
          syncProps(node);
          node.setDirtyCanvas(true, true);
        });
        toggle._allmaId = TOGGLE_ID;
        toggle.serialize = false;
        toggle.options = { serialize: false };

        // Captured once, in schema order. Collapsing takes them out of
        // node.widgets, so this is the only place they survive.
        node._allmaNameWidgets = (node.widgets || [])
          .filter((w) => String(w.name).startsWith(PROP_PREFIX));
        // Callback plus a poll, never Object.defineProperty.
        //
        // Intercepting `value` caught every write — including the Parameters
        // panel, which sets it directly and fires no callback — but it also took
        // the property away from Vue, and Nodes 2.0 renders these fields
        // reactively. The value would be right while the box on screen showed
        // something else. Anything visible has to leave `value` alone.
        for (const w of node.widgets || []) {
          if (!String(w.name).startsWith(PROP_PREFIX)) continue;
          const orig = w.callback;
          w.callback = function (...args) {
            const out = orig?.apply(this, args);
            applyNames(node);
            return out;
          };
        }
        // The panel writes silently, so the names are re-read on a timer and
        // pushed onward only when one actually changed.
        if (!node._allmaNameTimer) {
          node._allmaNameTimer = setInterval(() => {
            if (!node.graph) { clearInterval(node._allmaNameTimer); node._allmaNameTimer = null; return; }
            const key = namesOf(node).join("\u0000");
            if (key === node._allmaNameKey) return;
            node._allmaNameKey = key;
            applyNames(node);
          }, 400);
        }

        setTimeout(() => syncProps(node), 0);
        return r;
      };

      const origConfigure = nodeType.prototype.onConfigure;
      nodeType.prototype.onConfigure = function (...args) {
        const r = origConfigure?.apply(this, args);
        setTimeout(() => syncProps(this), 0);
        return r;
      };

      // The DOM element of a text widget is created by the renderer LATER than
      // any of the hooks above, so at hide time there is nothing to hide yet and
      // the box appears afterwards — which is why a lone name_24 was still drawn
      // across the node. Setting w.type is not enough on its own.
      //
      // So: the heavy pass stays gated, but every draw cheaply re-hides any
      // element that has since materialised. Both are no-ops once settled.
      const origDraw = nodeType.prototype.onDrawForeground;
      nodeType.prototype.onDrawForeground = function (...args) {
        if (!this._allmaBusSynced) {
          this._allmaBusSynced = true;
          syncProps(this);
        }
        return origDraw?.apply(this, args);
      };
      return;
    }

    if (nodeData?.name !== OUT_NODE) return;

    for (const hook of ["onNodeCreated", "onConnectionsChange", "onConfigure"]) {
      const orig = nodeType.prototype[hook];
      nodeType.prototype[hook] = function (...args) {
        const r = orig?.apply(this, args);
        setTimeout(() => mirror(this), 0);
        return r;
      };
    }
  },
});
