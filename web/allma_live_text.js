import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { allNodes, resolveUpstream, rootGraph } from "./allma_graph.js";

/* Allma Live Text — a display that fills in while its source is still working.
 *
 * A node output cannot stream: execute() returns once and the value lands
 * whole. So what is shown DURING the run arrives by other roads, both over the
 * websocket ComfyUI already holds open:
 *
 * - allma.stream — AllmaGenerate's own relay (api/stream.py), the text itself.
 * - progress / progress_text / executing — ComfyUI's generic events, sent for
 *   ANY node with a progress bar. This is what makes the display agnostic: a
 *   YuE2GenerateABC shows "340/3000 tokens" while it samples, without the
 *   source knowing this node exists.
 *
 * This node finds its own source by following its `text` input link back
 * through the graph, so it only reacts to the node it is actually wired to.
 * Two of them on two sources stay independent.
 */

const NODE = "AllmaLiveText";
const EVENT = "allma.stream";
const MAX_CHARS = 20000; // a runaway think loop must not grow the DOM forever
const BAR_WIDTH = 24;

let ComfyWidgets;

/** Does a stream event come from the node feeding us?
 *
 * The backend identifies itself with its EXECUTION id, which ComfyUI namespaces
 * for anything inside a subgraph — "6158:6160" rather than "6160". The link we
 * read only knows the local id, so a plain equality check silently never fires
 * and the box stays empty for every node that lives in a subgraph.
 *
 * Matching the last path segment reconnects the two. Two instances of the same
 * subgraph would share a local id and both boxes would fill; that is a far
 * smaller problem than the display never working inside a subgraph at all. */
function isOurSource(localId, streamId) {
  if (localId == null || streamId == null) return false;
  const s = String(streamId);
  return s === localId || s.endsWith(":" + localId);
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

/** Every Live Text wired to the node that `executionId` names, with its source.
 *
 * Walks from the ROOT, not app.graph: stepping into a subgraph swaps app.graph,
 * and a display outside it would otherwise stop updating until you came back —
 * then show a stale half-run. */
function* listenersOf(executionId) {
  if (executionId == null) return;
  for (const node of allNodes(rootGraph())) {
    if (node.type !== NODE) continue;
    const src = resolveUpstream(node, "text");
    if (src && isOurSource(src.id, executionId)) yield [node, src];
  }
}

function duration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "?";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  return `${m}min ${String(Math.round(seconds % 60)).padStart(2, "0")}s`;
}

/* The progress view: which node, a bar, the count, speed and time left.
 *
 * Speed is measured from the first progress event of this run, not from when
 * the node started — model loading happens before the first step and would make
 * the estimate wildly pessimistic for the first half of the run. */
function renderProgress(node) {
  const run = node._allmaRun;
  if (!run) return;
  const lines = [`⏳ ${run.title}`];
  if (run.max > 0) {
    const frac = Math.min(1, run.value / run.max);
    const filled = Math.round(frac * BAR_WIDTH);
    lines.push(`${"█".repeat(filled)}${"░".repeat(BAR_WIDTH - filled)}  ${Math.round(frac * 100)}%`);
    const elapsed = (performance.now() - (run.t1 ?? performance.now())) / 1000;
    const done = run.value - (run.v1 ?? 0);
    const rate = elapsed > 0.5 && done > 0 ? done / elapsed : 0;
    const stats = [`${run.value}/${run.max}`];
    if (rate) {
      stats.push(`${rate >= 10 ? Math.round(rate) : rate.toFixed(1)}/s`);
      stats.push(`~${duration((run.max - run.value) / rate)} restantes`);
    }
    lines.push(stats.join(" · "));
  } else {
    lines.push("rodando…");
  }
  if (run.text) lines.push("", run.text);
  write(node, lines.join("\n"), false);
}

/** A node that streams its own text (AllmaGenerate) owns the display; generic
 *  progress must not overwrite the words appearing in it. */
function streaming(node) {
  return !!node._allmaStreaming;
}

app.registerExtension({
  name: "allma.liveText",

  async setup() {
    ({ ComfyWidgets } = await import("../../scripts/widgets.js"));

    // The source started: clear the last run so an old result is not mistaken
    // for this one while the model loads.
    api.addEventListener("executing", (e) => {
      const id = typeof e.detail === "object" && e.detail !== null ? e.detail.node : e.detail;
      for (const [node, src] of listenersOf(id)) {
        node._allmaStreaming = false;
        node._allmaRun = { title: src.node?.title || src.node?.type || `#${src.id}`, value: 0, max: 0, text: "" };
        renderProgress(node);
      }
    });

    api.addEventListener("progress", (e) => {
      const d = e.detail || {};
      for (const [node, src] of listenersOf(d.node)) {
        if (streaming(node)) continue;
        const run = node._allmaRun ||= { title: src.node?.title || src.node?.type || `#${src.id}`, value: 0, max: 0, text: "" };
        // A second progress bar inside the same node (a new phase) starts over.
        if (run.t1 === undefined || d.value < run.value || d.max !== run.max) {
          run.t1 = performance.now();
          run.v1 = d.value;
        }
        run.value = d.value;
        run.max = d.max;
        renderProgress(node);
      }
    });

    // Some nodes publish a line of status text alongside the bar.
    api.addEventListener("progress_text", (e) => {
      const d = e.detail || {};
      for (const [node, src] of listenersOf(d.nodeId)) {
        if (streaming(node)) continue;
        const run = node._allmaRun ||= { title: src.node?.title || src.node?.type || `#${src.id}`, value: 0, max: 0 };
        run.text = String(d.text ?? "").slice(-MAX_CHARS);
        renderProgress(node);
      }
    });

    api.addEventListener(EVENT, (e) => {
      const d = e.detail || {};
      for (const [node, src] of listenersOf(d.node)) {
        // slot 1 = thinking, slot 0 = output_prompt. Show the channel this
        // node is actually wired to, and ignore the other one.
        const wanted = src.slot === 1 ? "reasoning" : "content";

        if (d.event === "start") {
          node._allmaStreaming = true;
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
      this._allmaRun = null;
      this._allmaStreaming = false;
      const t = message?.text;
      if (Array.isArray(t) && t.length) {
        this._allmaBuf = String(t[0] ?? "");
        write(this, this._allmaBuf, false);
      }
    };
  },
});
