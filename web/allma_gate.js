import { app } from "../../scripts/app.js";
import { exclusiveUpstream, resolveUpstream, allNodes, literalFrom } from "./allma_graph.js";

/* Allma Gate — switching it off mutes the branch behind it.
 *
 * The Python side already stops the upstream from being EVALUATED, through a
 * lazy input. That is not the same as muting: a lazily-skipped node is still in
 * the prompt, so anything else that happens to want it still drags it in — an
 * output node downstream of the same branch will run the whole thing regardless
 * of the gate.
 *
 * Muting removes the nodes from the prompt outright, the way Ctrl+M does. The
 * link disappears with them, so the consumer sees an unconnected input rather
 * than a null, which is what a node with an optional slot actually wants.
 *
 * Only nodes that feed THIS gate and nothing else are touched — see
 * exclusiveUpstream(). A resize shared with another slot keeps running, because
 * switching this branch off must not break the branch next to it.
 */

const NODE = "AllmaMuter";
const OLD_NODE = "AllmaGate";
const MODE_ALWAYS = 0;
const MODE_NEVER = 2; // litegraph's "mute"


/** The branch slots only.
 *
 * Autogrow namespaces them as "values.value_N"; `enabled` and the on_N toggles
 * also live in node.inputs and must not be counted, or every branch index is
 * off by one and the toggles line up with the wrong wires. */
function branchInputs(node) {
  return (node.inputs || []).filter((i) => /(^|\.)value_\d+$/.test(String(i.name)));
}

function toggleFor(node, i) {
  return node.widgets?.find((w) => w.name === `on_${i + 1}`);
}

/** The value of a toggle, following the wire wherever it goes.
 *
 * Only a boolean counts: literalFrom returns undefined for anything a node
 * computes, and that has to fall back to the last clicked state rather than be
 * read as false. */
function litValue(node, name) {
  const v = literalFrom(node, name);
  return typeof v === "boolean" ? v : undefined;
}

function branchOn(node, i) {
  const wired = litValue(node, "enabled");
  const master = wired !== undefined
    ? wired
    : node.widgets?.find((w) => w.name === "enabled")?.value !== false;
  if (!master) return false;
  const own = litValue(node, `on_${i + 1}`);
  return own !== undefined ? own : toggleFor(node, i)?.value !== false;
}

/** The node a slot points at, plus everything feeding only that node.
 *
 * Pointing a muter at something means "switch this off", the way Ctrl+M does —
 * so the target is muted whether or not other nodes consume it. Refusing on the
 * grounds that it was shared made sense while values flowed THROUGH the muter;
 * now that the real wire bypasses it entirely, a shared consumer is the normal
 * case and refusing would mean never muting anything.
 *
 * Its private ancestors come along, because leaving a loader running to feed a
 * muted node is just wasted work. Anything shared with a live branch stays.
 */
function branchNodes(node, inputName) {
  const src = resolveUpstream(node, inputName);
  const target = src?.node;
  if (!target) return [];
  const out = [target];
  for (const inp of target.inputs || []) {
    if (inp.link == null) continue;
    for (const n of exclusiveUpstream(target, inp.name, [NODE, OLD_NODE])) {
      if (!out.includes(n)) out.push(n);
    }
  }
  return out;
}

/** Mute or restore the nodes one branch points at, and report what happened. */
function applyBranchMode(node) {
  const slots = branchInputs(node);
  let muted = 0;
  let restored = 0;

  slots.forEach((inp, i) => {
    if (inp.link == null) return;
    const on = branchOn(node, i);
    for (const n of branchNodes(node, inp.name)) {
      // Never revive a node the user muted by hand: only nodes this muter put
      // to sleep are woken again, tracked by a mark on the node itself.
      if (on && !n._allmaMutedBy) continue;
      if (!on && n.mode !== MODE_ALWAYS && !n._allmaMutedBy) continue;
      n.mode = on ? MODE_ALWAYS : MODE_NEVER;
      n._allmaMutedBy = on ? null : node.id;
      if (on) restored++; else muted++;
    }
  });

  const w = node.widgets?.find((x) => x.name === "enabled");
  if (w) {
    const off = slots.filter((s, i) => s.link != null && !branchOn(node, i)).length;
    // "enabled" describes a value; this one drives every branch at once, so it
    // says what it does. The count only appears when something is actually off.
    w.label = off
      ? `Toggle All — ${off} branch${off > 1 ? "es" : ""} off`
      : "Toggle All";
  }
  node.graph?.setDirtyCanvas?.(true, true);
  return muted;
}

/** Show a toggle only for a branch that actually has a wire.
 *
 * Nodes 2.0 renders from node.widgets and ignores every per-widget hide flag,
 * so the only way to keep unused toggles off the node is to take them out of
 * the list — see web/allma_bus.js for the measurements behind this. */
function syncToggles(node) {
  const all = node._allmaToggles || [];
  if (!all.length) return;
  const slots = branchInputs(node);
  const wanted = slots.filter((s) => s.link != null).length;
  // Toggle labels follow the slot they govern, so the pair reads as one row.
  all.forEach((t, i) => { t.label = `${i + 1}`; });
  const others = (node.widgets || []).filter((w) => !all.includes(w));
  node.widgets = [...others, ...all.slice(0, wanted)];
  // The schema name is "values.value_N", which is plumbing the user did not ask
  // to read. A bare index is enough to tell the slots apart, and the toggle
  // right below carries the state.
  slots.forEach((inp, i) => {
    inp.label = `${i + 1}${inp.link != null && !branchOn(node, i) ? " (muted)" : ""}`;
  });
  if (typeof node.computeSize === "function") {
    const s = node.computeSize();
    node.setSize([Math.max(node.size?.[0] ?? s[0], s[0]), s[1]]);
  }
}

/* One timer for the whole workflow, not one per node.
 *
 * A toggle driven by a wire has nothing to hook: the value lives on another node
 * entirely, and that node has never heard of this one. onDrawForeground looked
 * like the place for it, but the Vue renderer never calls it and the legacy one
 * skips nodes outside the viewport — so a muter just off-screen would quietly
 * stop tracking. Polling the computed state is crude, and it is the only thing
 * that holds for every renderer and every scroll position.
 *
 * The key is a string of the branch states, so the graph is only touched when
 * something actually changed; a steady workflow costs one string compare per
 * muter per tick.
 */
const POLL_MS = 300;
let poller = null;

function startPolling() {
  if (poller) return;
  poller = setInterval(() => {
    const g = app.graph;
    if (!g) return;
    for (const n of allNodes(g)) {
      if (n.type !== NODE && n.type !== OLD_NODE) continue;
      const slots = branchInputs(n);
      const key = slots
        .map((s, i) => (s.link == null ? "-" : branchOn(n, i) ? "1" : "0"))
        .join("");
      if (key === n._allmaBranchKey) continue;
      n._allmaBranchKey = key;
      applyBranchMode(n);
      syncToggles(n);
    }
  }, POLL_MS);
}

app.registerExtension({
  name: "allma.gate",

  async setup() {
    startPolling();
  },

  /* Silently upgrade the old id as a workflow loads.
   *
   * A saved workflow records node_id, so a rename leaves every existing instance
   * pointing at a name that no longer exists. The Python side keeps a deprecated
   * AllmaGate around so nothing breaks outright; this rewrites the type on the
   * way in, so the next save is clean and the shim stops being needed.
   *
   * Subgraph definitions carry their own node lists and are rewritten too —
   * missing them would leave the old id alive exactly where the node is most
   * used. */
  beforeConfigureGraph(graphData) {
    let n = 0;
    const sweep = (nodes) => {
      for (const node of nodes || []) {
        if (node?.type === OLD_NODE) { node.type = NODE; n++; }
      }
    };
    sweep(graphData?.nodes);
    for (const sg of graphData?.definitions?.subgraphs || []) sweep(sg?.nodes);
    if (n) console.log(`[AllmaNodes] upgraded ${n} AllmaGate → AllmaMuter`);
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE && nodeData?.name !== OLD_NODE) return;

    const origCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = origCreated?.apply(this, arguments);
      const node = this;

      // Captured once: collapsing takes them out of node.widgets, so this is the
      // only reference that survives an unused branch.
      node._allmaToggles = (node.widgets || []).filter((w) => /^on_\d+$/.test(String(w.name)));

      // Intercept the property, not the callback.
      //
      // A callback only fires when the widget is clicked on the node. Every
      // other route writes widget.value directly and silently: the Parameters
      // panel, a script, and — the reason this matters — a subgraph input
      // promoted from this toggle. Hooking `value` itself catches all of them,
      // so the muting reacts no matter where the change came from.
      const hook = (w, after) => {
        let stored = w.value;
        Object.defineProperty(w, "value", {
          configurable: true,
          enumerable: true,
          get: () => stored,
          set: (v) => {
            if (stored === v) return;
            stored = v;
            after(v);
          },
        });
      };

      const master = (node.widgets || []).find((w) => w.name === "enabled");
      if (master) {
        hook(master, (v) => {
          // The master sets every branch at once; each can still be changed on
          // its own afterwards, which is the point of having both.
          for (const t of node._allmaToggles) t.value = v !== false;
          applyBranchMode(node);
          syncToggles(node);
        });
      }
      for (const t of node._allmaToggles) {
        hook(t, () => { applyBranchMode(node); syncToggles(node); });
      }

      setTimeout(() => syncToggles(node), 0);
      return r;
    };

    // On the PROTOTYPE, not the instance. Assigning to the instance shadows the
    // handler ComfyUI installs for Autogrow, so new slots stopped appearing when
    // the previous one was filled.
    const origConn = nodeType.prototype.onConnectionsChange;
    nodeType.prototype.onConnectionsChange = function (...a) {
      const rr = origConn?.apply(this, a);
      setTimeout(() => { syncToggles(this); applyBranchMode(this); }, 0);
      return rr;
    };

    // On load the saved modes are already right; re-applying only repairs a
    // workflow edited elsewhere, and must wait until every node exists.
    const origConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = origConfigure?.apply(this, arguments);
      setTimeout(() => { syncToggles(this); applyBranchMode(this); }, 100);
      return r;
    };
  },
});
