import { app } from "../../scripts/app.js";
import { allNodes, getLink, resolveUpstream, rootGraph } from "./allma_graph.js";

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

function nameWidget(node, slotNo) {
  const key = PROP_PREFIX + slotNo;
  // node.widgets alone is NOT where a name lives. Collapsing takes these boxes
  // out of that list (see applyVisibility), so a lookup that only searched it
  // came back empty whenever the panel was shut — and every label fell back to
  // the raw slot name. The stash is the list that always holds all of them.
  //
  // node.widgets still goes first: a loader that rebuilds widgets on configure
  // must win over the stale twin the stash would otherwise hand back.
  return node.widgets?.find((w) => w.name === key)
    || (node._allmaNameWidgets || []).find((w) => w.name === key);
}

/** Re-capture the stash whenever the real widgets are in hand.
 *
 * Expanded, node.widgets holds every name box, so it is the authority. Shut, it
 * holds none — and the stash is all that is left, so it must be kept. */
function refreshStash(node) {
  const live = (node.widgets || []).filter((w) =>
    String(w.name).startsWith(PROP_PREFIX));
  if (live.length) node._allmaNameWidgets = live;
}

/** Names for each slot of a Bus In node, index-aligned with its LINK inputs.
 *
 * The value lives in a real widget so the Parameters panel can edit it — that
 * panel lists widgets, and nothing else. */
function namesOf(node) {
  return linkInputs(node).map((inp) => {
    // Empty first, written second. A slot that carries nothing has no name to
    // offer even when a name is still typed in its box — that box is a leftover
    // from whatever used to be plugged there, and honouring it put a named
    // output on the receiving node with no value behind it.
    if (inp.link == null) return null;
    const written = nameWidget(node, slotNumber(inp))?.value;
    if (written) return String(written);
    return autoName(node, inp) || inp.name;
  });
}

/** The number in `slots.slot_7` → 7. */
function slotNumber(input) {
  const m = /(\d+)$/.exec(String(input?.name || ""));
  return m ? Number(m[1]) : 0;
}

/** Only the bus slots, in SLOT-NUMBER order — the name widgets are inputs too,
 * and must not count.
 *
 * Sorted by the slot's own number and never by position in the array, because
 * that array's order is not ours to trust: ComfyUI reorders it, and a saved
 * workflow came back as s1 s4 s5 s6 s7 s8 s9 s10 s2 s3. Names were then handed
 * out by position — the second row got name_2 whatever slot it happened to be —
 * so the labels still counted 1..9 neatly down the node while pointing at the
 * wrong wires. That is what "the bus shuffled the order I chose" looks like.
 *
 * The array itself is left exactly as it is. Reordering it would move every
 * input's index, and links address inputs by index — the wires would follow the
 * labels into the wrong holes for real. */
function linkInputs(node) {
  return (node.inputs || [])
    .filter((i) => !String(i.name).startsWith(PROP_PREFIX))
    .sort((a, b) => slotNumber(a) - slotNumber(b));
}

/** Keep one editable property per connected slot, seeded from the source.
 *
 * The seed is what makes this usable: a slot arrives already labelled with
 * whatever was plugged into it, and the panel is only for when that name is not
 * good enough — "MODEL" becoming "Speed Lora". */
/** Push the current names onto the slots and the receiving node. */
function applyNames(node) {
  const slots = linkInputs(node);
  slots.forEach((inp) => {
    // Same rule namesOf follows: an empty slot shows no name, however stale the
    // text still sitting in its box. Otherwise a slot people had unplugged kept
    // advertising what used to be there.
    const w = inp.link == null ? null : nameWidget(node, slotNumber(inp));
    inp.label = w?.value ? String(w.value) : undefined;
  });
  node.setDirtyCanvas(true, true);
  mirrorConsumers(node.graph);
}


/* The inputs array is left exactly as ComfyUI builds it.
 *
 * There used to be a pass here that sorted the bus slots by number and
 * rewrote each link's target_slot to match, written when the old frontend
 * rotated the array on load. It now does real damage: Autogrow COMPACTS the
 * slots itself when a middle one is emptied (disconnect branch 2 of 7 and the
 * rest renumber under your hand), and reordering on top of that moved wires
 * onto slots that had just been renamed. Measured: seven wired branches, three
 * disconnects, and the node came back with one wire after stepping into the
 * subgraph and out. With this gone the behaviour matches stock ComfyUI exactly.
 *
 * Pairing never needed it anyway: slotInputs() sorts by the NUMBER in the
 * slot name, so a toggle always follows its own branch wherever the array puts
 * it. */

function syncProps(node) {
  refreshStash(node);
  const slots = linkInputs(node);
  const collapsed = isCollapsed(node);

  const toggle = node.widgets?.find((w) => w._allmaId === TOGGLE_ID);
  if (toggle) {
    const n = slots.filter((i) => i.link != null).length;
    toggle.name = `${collapsed ? "▸" : "▾"} slot names (${n})`;
  }

  slots.forEach((inp) => {
    const w = nameWidget(node, slotNumber(inp));
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
  // Split by NAME, never by object identity. Nodes 2.0 hands widgets to Vue,
  // which wraps them in reactive proxies — so the object sitting in
  // node.widgets stops being `===` the one captured in the stash, every
  // includes() test comes back false, and the "everything else" half swallowed
  // the name boxes whole. Collapsing then rebuilt the list out of the names and
  // dropped the toggle, leaving a node with no way to reopen the panel.
  const others = (node.widgets || []).filter(
    (w) => !String(w.name).startsWith(PROP_PREFIX));
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

/** The Bus In whose slots a bus-carrying wire ultimately came from.
 *
 * Buses nest: a Bus In slot can hold another bus, so unpacking the outer one
 * hands you an inner bus that a second Bus Out expands. Following only one hop
 * stopped at the Bus Out in the middle, found no Bus In, and every leaf fell
 * back to slot_1..slot_24 — the names existed the whole time, just one level
 * further up than the lookup was willing to walk.
 *
 * A Bus Out's output slot k IS slot k of the bus feeding it, so crossing one
 * costs a hop up to that bus's Bus In and a step sideways into its slot k. */
function busSourceOf(node, depth = 0) {
  if (depth > 8) return null;             // cycles and silly-deep nesting
  const up = resolveUpstream(node, "bus");
  if (!up?.node) return null;
  if (up.node.type === IN_NODE) return up.node;
  if (up.node.type !== OUT_NODE) return null;

  const outer = busSourceOf(up.node, depth + 1);
  if (!outer) return null;
  const slot = linkInputs(outer).find((x) => slotNumber(x) === up.slot + 1);
  if (!slot) return null;

  // What feeds that slot has to be a bus itself for the nesting to mean
  // anything; anything else is a plain value with no names to offer.
  const feed = resolveUpstream(outer, slot.name);
  return feed?.node?.type === IN_NODE ? feed.node : null;
}

/** Rewrite a Bus Out node's outputs to match the bus feeding it. */
function mirror(node) {
  const src = busSourceOf(node);
  const names = src ? namesOf(src) : [];
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
      //
      // The default name goes back on too: only `label` used to be cleared, so
      // unplugging a bus left the old names painted on a node that no longer
      // carried them — a Bus Out still advertising IMAGE 1 with nothing behind
      // it. Links address outputs by index, so renaming is free.
      out.name = `slot_${i + 1}`;
      out.localized_name = out.name;
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

/** Re-mirror every Bus Out anywhere, so a rename propagates immediately.
 *
 * Deliberately not "only the ones fed by this Bus In": with nesting, a rename
 * on an inner bus reaches leaves several hops away, and walking the graph to
 * find exactly which ones costs more than just redoing all of them. There are
 * never many Bus Outs, and mirror() is a few field writes.
 *
 * allNodes rather than graph._nodes: the latter is one graph's worth, and a
 * bus that leaves a subgraph has its consumer outside it. */
function mirrorConsumers(_graph) {
  for (const n of allNodes(rootGraph())) {
    if (n.type === OUT_NODE) mirror(n);
  }
}

/* One sweep for every Bus Out, instead of a timer per node.
 *
 * A per-node timer was the obvious shape and it does not survive subgraphs: the
 * instances inside one are rebuilt, so the closure ends up holding a node the
 * canvas no longer draws — the timer keeps ticking against a corpse while the
 * live node shows stale names. Proved it by parking a sentinel on the visible
 * node and watching the timer never overwrite it.
 *
 * Walking from the root each time has no such handle to go stale, and it is what
 * makes nesting settle: a leaf's names depend on links several nodes upstream,
 * and nothing local tells it when one of those moved.
 */
const MIRROR_POLL_MS = 400;
const mirrorKeys = new WeakMap();

function sweepMirrors() {
  const root = rootGraph();
  if (!root) return;
  for (const node of allNodes(root)) {
    if (node.type !== OUT_NODE) continue;
    let key;
    try {
      const src = busSourceOf(node);
      key = src ? namesOf(src).join("\u0000") : "";
    } catch {
      continue;              // a half-built graph is not worth a stack trace
    }
    if (mirrorKeys.get(node) === key) continue;
    mirrorKeys.set(node, key);
    mirror(node);
  }
}

if (!globalThis.__allmaBusSweep) {
  globalThis.__allmaBusSweep = setInterval(sweepMirrors, MIRROR_POLL_MS);
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

      // Two separate hazards live in widgets_values, and both corrupt names.
      //
      // 1. Collapsed, node.widgets holds only the toggle — and widgets_values is
      //    built from node.widgets. A node saved with the panel shut serialised
      //    an EMPTY list, so every typed name vanished on the next load.
      //
      // 2. The toggle is added in onNodeCreated but the renderer has it in hand
      //    by save time and NOT by load time. Saving wrote 25 entries (a leading
      //    null for the toggle, then 24 names); loading poured them into the 24
      //    name widgets, so every value landed one slot late — name_1 came back
      //    empty and got re-seeded from the wire, ALFA slid onto name_2, and so
      //    on down the list.
      //
      // Both go away by making the array mean exactly one thing: the 24 names,
      // in schema order, nothing else. Save writes that; load reads that. The
      // toggle never belonged in there — it is a view control with no state
      // worth keeping.
      //
      // Neither path touches node.widgets. Swapping the list for the length of
      // the call was the obvious way to do this and it broke Nodes 2.0: the base
      // configure REBUILDS node.widgets, so putting the pre-call snapshot back
      // afterwards discarded the freshly built list — toggle included — and left
      // a node whose panel could no longer be opened. Reading and writing the
      // widget objects directly has no such window.
      function nameWidgetsOf(node) {
        const byName = new Map();
        for (const w of node._allmaNameWidgets || []) byName.set(String(w.name), w);
        // Live widgets win: a rebuilt one is the object the renderer now draws.
        for (const w of node.widgets || []) {
          if (String(w.name).startsWith(PROP_PREFIX)) byName.set(String(w.name), w);
        }
        const out = [];
        for (let i = 1; ; i++) {
          const w = byName.get(PROP_PREFIX + i);
          if (!w) break;
          out.push(w);
        }
        return out;
      }

      const origSerialize = nodeType.prototype.serialize;
      nodeType.prototype.serialize = function (...args) {
        const data = origSerialize?.apply(this, args);
        const names = nameWidgetsOf(this);
        if (data && names.length) {
          data.widgets_values = names.map((w) => w.value ?? "");
        }
        return data;
      };

      const origCfg = nodeType.prototype.configure;
      nodeType.prototype.configure = function (info, ...rest) {
        const names = nameWidgetsOf(this);
        const vals = info?.widgets_values;
        let mine = null;
        if (names.length && Array.isArray(vals) && vals.length) {
          // Longer than the name list means a pre-fix save carrying the toggle's
          // null in front. The names are always the tail, so trim to it.
          mine = vals.length > names.length ? vals.slice(-names.length) : vals.slice();
          // Corrected in place, on the caller's own object. Handing the base a
          // COPY would be tidier but the loader keeps hold of the node data it
          // passed in, and a swapped-in twin is a desync waiting to happen.
          info.widgets_values = mine;
        }
        const r = origCfg?.call(this, info, ...rest);
        if (mine) {
          nameWidgetsOf(this).forEach((w, i) => {
            if (i < mine.length && mine[i] != null) w.value = mine[i];
          });
        }
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
