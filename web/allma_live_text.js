import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

/* Allma Live Text — a display that fills in while the model is still typing.
 *
 * A node output cannot stream: execute() returns once and the STRING lands
 * whole. So the live text arrives by a different road — api/stream.py pushes
 * it over the websocket ComfyUI already holds open, keyed by the AllmaGenerate
 * node that produced it.
 *
 * This node finds its own source by following its `text` input link back
 * through the graph, so it only reacts to the generator it is actually wired
 * to. Two of them on two generators stay independent.
 */

const NODE = "AllmaLiveText";
const EVENT = "allma.stream";
const MAX_CHARS = 20000; // a runaway think loop must not grow the DOM forever

let ComfyWidgets;

/** The node id feeding our `text` input, or null when unconnected. */
function sourceId(node) {
  const input = node.inputs?.find((i) => i.name === "text");
  if (!input || input.link == null) return null;
  const links = node.graph?.links;
  const link = links?.get ? links.get(input.link) : links?.[input.link];
  return link ? String(link.origin_id) : null;
}

/** Which output slot we are reading: 1 is `thinking`, 0 is `output_prompt`. */
function sourceSlot(node) {
  const input = node.inputs?.find((i) => i.name === "text");
  if (!input || input.link == null) return null;
  const links = node.graph?.links;
  const link = links?.get ? links.get(input.link) : links?.[input.link];
  return link ? link.origin_slot : null;
}

function box(node) {
  return node.widgets?.find((w) => w._allmaLive);
}

function ensureBox(node) {
  if (box(node)) return box(node);
  const w = ComfyWidgets.STRING(
    node, "display", ["STRING", { multiline: true }], app,
  ).widget;
  w._allmaLive = true;
  w.serialize = false;
  w.options = w.options || {};
  w.options.serialize = false;
  w.options.getMinHeight = () => 120;
  const el = w.element || w.inputEl;
  if (el) {
    el.readOnly = true;
    el.style.fontSize = "10px";
    el.style.opacity = "0.9";
  }
  return w;
}

function write(node, text, follow = true) {
  const w = ensureBox(node);
  w.value = text;
  const el = w.element || w.inputEl;
  if (el && follow && el.scrollHeight) el.scrollTop = el.scrollHeight;
  node.setDirtyCanvas(true, true);
}

app.registerExtension({
  name: "allma.liveText",

  async setup() {
    ({ ComfyWidgets } = await import("../../scripts/widgets.js"));

    api.addEventListener(EVENT, (e) => {
      const d = e.detail || {};
      for (const node of app.graph?._nodes || []) {
        if (node.type !== NODE) continue;
        if (sourceId(node) !== String(d.node)) continue;

        // slot 1 = thinking, slot 0 = output_prompt. Show the channel this
        // node is actually wired to, and ignore the other one.
        const slot = sourceSlot(node);
        const wanted = slot === 1 ? "reasoning" : "content";

        if (d.event === "start") {
          node._allmaBuf = "";
          write(node, "");
        } else if (d.event === "chunk" && d.kind === wanted) {
          node._allmaBuf = (node._allmaBuf || "") + (d.text || "");
          if (node._allmaBuf.length > MAX_CHARS) {
            node._allmaBuf = node._allmaBuf.slice(-MAX_CHARS);
          }
          write(node, node._allmaBuf);
        } else if (d.event === "done" && d.note) {
          node._allmaBuf = (node._allmaBuf || "") + `\n\n— ${d.note}`;
          write(node, node._allmaBuf);
        }
      }
    });
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE) return;

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      setTimeout(() => ensureBox(this), 0);
      return r;
    };

    // The executed value is the authority: it replaces whatever the live
    // stream left behind, so a truncated tail can never linger as the result.
    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      onExecuted?.apply(this, arguments);
      const t = message?.text;
      if (Array.isArray(t) && t.length) {
        this._allmaBuf = String(t[0] ?? "");
        write(this, this._allmaBuf, false);
      }
    };
  },
});
