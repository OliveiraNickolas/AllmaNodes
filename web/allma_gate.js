import { app } from "../../scripts/app.js";
import { exclusiveUpstream, resolveUpstream, allNodes, literalFrom, rootGraph, promotedWidget } from "./allma_graph.js";

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
const BYPASS_NODE = "AllmaBypasser";
const OLD_NODE = "AllmaGate";
const MODE_ALWAYS = 0;
const MODE_NEVER = 2;  // litegraph's "mute"
const MODE_BYPASS = 4; // litegraph's "bypass"

/** Every node type this extension drives. */
const OURS = [NODE, BYPASS_NODE, OLD_NODE];

/** Muting removes a node from the graph; bypassing keeps it and passes its
 *  input through. Same machinery, one different number. */
function offMode(node) {
  return node.type === BYPASS_NODE ? MODE_BYPASS : MODE_NEVER;
}


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

/** Is this input actually fed by a wire? */
function isWired(node, name) {
  const inp = (node.inputs || []).find((x) => x.name === name);
  return !!inp && inp.link != null;
}

function branchOn(node, i) {
  // The per-branch toggle is the truth. Toggle All is a bulk COMMAND — clicking
  // it writes every branch — not a gate over them, or turning one branch back on
  // would do nothing while it read OFF, and the node would show a state it was
  // not in.
  const own = litValue(node, `on_${i + 1}`);
  const branch = own !== undefined ? own : toggleFor(node, i)?.value !== false;

  // The master never gates, wired or not — it commands, and the branch toggle is
  // always the truth. A wired master is cascaded onto the toggles the moment its
  // value changes (see the poller), so by the time this is read the branches
  // already carry it. Gating on top of that would freeze the individual toggles:
  // flipping one back on under a master reading OFF would do nothing.
  return branch;
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
    for (const n of exclusiveUpstream(target, inp.name, OURS)) {
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
      n.mode = on ? MODE_ALWAYS : offMode(node);
      n._allmaMutedBy = on ? null : node.id;
      if (on) restored++; else muted++;
    }
  });

  const w = node.widgets?.find((x) => x.name === "enabled");
  if (w) {
    const wired = slots.filter((s) => s.link != null);
    const off = wired.filter((s, i) => !branchOn(node, slots.indexOf(s))).length;
    // The master answers "are they ALL on?", so switching any single branch off
    // makes it read OFF — that statement simply stopped being true. It does not
    // mean "everything is off", and it must not make it so: the flag keeps this
    // write from reaching the master's own setter, where a real click would
    // cascade to every branch. Reporting here, commanding only when clicked.
    const allOn = wired.length > 0 && off === 0;
    if (wired.length) {
      // A plain assignment: Vue sees it, and a callback only fires on a real
      // click, so this cannot cascade back into the branches.
      if (w.value !== allOn) w.value = allOn;
      // If the master was promoted, the switch on screen is the parent's — the
      // one here is a passenger, and leaving it alone would show ALL ON above a
      // subgraph with a branch switched off.
      const outer = promotedWidget(node, "enabled");
      if (outer && outer.value !== allOn) {
        outer.value = allOn;
        node._allmaWiredMaster = allOn;  // do not re-cascade our own report
      }
    }
    // "enabled" describes a value; this one drives every branch at once, so it
    // says what it does. The count only appears when something is actually off.
    const verbo = node.type === BYPASS_NODE ? "bypassed" : "muted";
    w.label = off
      ? `Toggle All — ${off} branch${off > 1 ? "es" : ""} ${verbo}`
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
    const marca = node.type === BYPASS_NODE ? " (bypassed)" : " (muted)";
    inp.label = `${i + 1}${inp.link != null && !branchOn(node, i) ? marca : ""}`;
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
    // The ROOT, never app.graph: stepping into a subgraph swaps app.graph for
    // that subgraph, and a muter inside it could then no longer climb out to
    // read a value promoted from the parent. It fell back to its own widget,
    // computed a different state, and re-applied it — which is why entering and
    // leaving a subgraph left the mutes scrambled.
    const g = rootGraph();
    if (!g) return;
    for (const n of allNodes(g)) {
      if (!OURS.includes(n.type)) continue;
      const slots = branchInputs(n);
      // A master driven from outside — a promoted subgraph input, a boolean —
      // has no click to fire a callback, so its change is caught here and
      // cascaded exactly as a click would. Only on CHANGE: doing it every tick
      // would overwrite a branch the user had just set on its own.
      const wiredMaster = isWired(n, "enabled") ? litValue(n, "enabled") : undefined;
      if (wiredMaster !== undefined && wiredMaster !== n._allmaWiredMaster) {
        n._allmaWiredMaster = wiredMaster;
        (n._allmaToggles || []).forEach((t, k) => {
          // When a branch toggle is itself promoted, the switch people see and
          // click lives on the CONTAINING node — the inner widget is a passenger
          // and writing to it moves nothing. Drive whichever one is real.
          const outer = promotedWidget(n, `on_${k + 1}`);
          if (outer) outer.value = wiredMaster; else t.value = wiredMaster;
        });
        rootGraph()?.setDirtyCanvas?.(true, true);
      }

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
    if (!OURS.includes(nodeData?.name)) return;

    const origCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = origCreated?.apply(this, arguments);
      const node = this;

      // Captured once: collapsing takes them out of node.widgets, so this is the
      // only reference that survives an unused branch.
      node._allmaToggles = (node.widgets || []).filter((w) => /^on_\d+$/.test(String(w.name)));

      // Widget callbacks, NOT a property interceptor.
      //
      // Replacing `value` with Object.defineProperty caught every write — panel,
      // promotion, script — but it also took the property away from Vue, and
      // Nodes 2.0 renders these switches reactively. The behaviour stayed right
      // while the drawing froze: toggles read OFF on screen with their branches
      // very much alive. Anything that must be seen has to leave `value` alone.
      //
      // So: the callback handles the click, and the poller below notices every
      // other route by comparing the computed state. Slower to react, and it
      // keeps the switch honest.
      const master = (node.widgets || []).find((w) => w.name === "enabled");
      if (master) {
        const orig = master.callback;
        master.callback = function (...args) {
          const out = orig?.apply(this, args);
          const v = master.value !== false;
          for (const t of node._allmaToggles) t.value = v;
          applyBranchMode(node);
          syncToggles(node);
          return out;
        };
      }
      for (const t of node._allmaToggles) {
        const orig = t.callback;
        t.callback = function (...args) {
          const out = orig?.apply(this, args);
          applyBranchMode(node);
          syncToggles(node);
          return out;
        };
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
