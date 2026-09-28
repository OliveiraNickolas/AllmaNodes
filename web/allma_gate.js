import { app } from "../../scripts/app.js";
import { resolveUpstream, allNodes, getLink, literalFrom, rootGraph, promotedWidget, nodeById } from "./allma_graph.js";
import { onRepair } from "./allma_slotfix.js";

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
 * ONLY the node a branch is wired to is touched — never anything upstream of
 * it. What feeds it is left alone: ComfyUI runs only what reaches an output, so
 * a node left orphaned by a mute does not execute anyway.
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
/** The number in `values.value_7` → 7. */
function branchNumber(input) {
  const m = /(\d+)$/.exec(String(input?.name || ""));
  return m ? Number(m[1]) : 0;
}

/** The wired branches, in BRANCH-NUMBER order.
 *
 * Sorted by the branch's own number and never by position in the inputs array,
 * because ComfyUI reorders that array — a real workflow came back as
 * v1 v4 v5 v6 v7 v8 v9 v10 v2. Toggles were matched to branches by position, so
 * the switch labelled 2 drove whatever input happened to sit second: here that
 * was v4. Every toggle past the first controlled the wrong node, which is what
 * "the muter does nothing" looks like when the thing it does is invisible.
 *
 * Same defect the bus had, same fix, and the array itself is again left alone:
 * links address inputs by index, so reordering it would move the wires. */
function branchInputs(node) {
  return (node.inputs || [])
    .filter((i) => /(^|\.)value_\d+$/.test(String(i.name)))
    .sort((a, b) => branchNumber(a) - branchNumber(b));
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

/**
 * A branch switch fed by a wire shows what the wire says.
 *
 * branchOn already follows the wire, so the MUTE was right all along — a
 * boolean set to false did mute the branch. But the switch drawn on the node
 * kept its last clicked value, so it read "true" over a branch that was off:
 * the node showed a state it was not in. This copies the wired value onto the
 * switch people actually see (the promoted one on the parent, when there is
 * one). Only a literal boolean is mirrored; anything computed leaves the
 * switch alone, exactly as branchOn does. Writes only on change.
 */
function mirrorWiredToggles(node) {
  let changed = false;
  (node._allmaToggles || []).forEach((t, k) => {
    const name = `on_${k + 1}`;
    if (!isWired(node, name)) return;
    const v = litValue(node, name);
    if (v === undefined) return;
    const outer = promotedWidget(node, name);
    const shown = outer || t;
    if (shown.value !== v) { shown.value = v; changed = true; }
    if (outer && t.value !== v) t.value = v;
  });
  return changed;
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
/* Step through a bus to whatever actually produced the value.
 *
 * A muter wired to Allma Bus Out is pointing at the bus, and the bus is a
 * junction: muting it would switch off every slot travelling on it, not the one
 * branch the switch names. What the switch means is "turn off what fills this
 * slot", and that node sits on the other side, plugged into Allma Bus In.
 *
 * The two ends are positional — Bus Out's output k is Bus In's slot k+1, which
 * is exactly what the names mirrored onto the receiving node are made from — so
 * the hop is: find the Bus In feeding this Bus Out, then resolve whatever feeds
 * the matching slot. Buses nest, so it repeats until the far end is an ordinary
 * node; the depth cap is there for a graph that loops back on itself. */
function throughBus(src) {
  let cur = src;
  for (let hop = 0; hop < 8; hop++) {
    if (cur?.node?.type !== "AllmaBusOut") return cur;
    const busIn = resolveUpstream(cur.node, "bus");
    /* Nothing on the other side: point at NOTHING, never at the bus.
     *
     * Returning the bus here is what a first version did, and switching that
     * branch off then swept the bus AND everything feeding it — one empty slot
     * bypassed the whole graph. A branch whose slot carries nothing has nothing
     * to switch off. */
    if (busIn?.node?.type !== "AllmaBusIn") return null;
    const slotName = (busIn.node.inputs || [])
      .map((i) => String(i.name))
      .find((n) => new RegExp(`(^|\\.)slot_${cur.slot + 1}$`).test(n));
    if (!slotName) return null;
    const next = resolveUpstream(busIn.node, slotName);
    if (!next?.node) return null;
    cur = next;
  }
  return cur;
}

function branchTarget(node, inputName) {
  return throughBus(resolveUpstream(node, inputName))?.node ?? null;
}

/** Mute or restore the nodes one branch points at, and report what happened. */
/* Ownership of a sleeping node, written where it survives a page load.
 *
 * This used to be a plain field on the node object, and that field is gone the
 * moment the page reloads. The node came back from the file still muted, the
 * mark did not come back with it, and the test above — "only wake what I put to
 * sleep" — then read every sleeper as somebody else's. Flipping a branch back on
 * did nothing at all, for good: the muter had lost the right to wake its own
 * nodes. That is the "stops working after a refresh".
 *
 * properties is the one bag on a node that LiteGraph serialises for us, so the
 * claim now travels with the workflow.
 *
 * Nodes muted by a version that predates this keep no claim and stay asleep —
 * they are indistinguishable from a hand-mute, and guessing wrong would wake
 * something the user silenced on purpose. Un-muting one by hand hands it back.
 */
const OWNER_PROP = "allmaMutedBy";

function mutedBy(node) {
  const owner = node?.properties?.[OWNER_PROP];
  return owner === undefined || owner === null ? null : owner;
}

function setMutedBy(node, id) {
  if (!node) return;
  if (!node.properties) node.properties = {};
  if (id === null) delete node.properties[OWNER_PROP];
  else node.properties[OWNER_PROP] = id;
}

/* A branch that is ON means its nodes run. No exceptions, no ownership test.
 *
 * There used to be one: "never revive a node the user muted by hand", tracked by
 * a mark saying which muter had put it to sleep. The idea was sound and it could
 * not be made to work. The mark lived only in memory, so every reload turned
 * every sleeper into somebody else's and froze it for good; moving the mark into
 * properties fixed new mutes but left every node an older build had silenced
 * stuck forever; and reconciling that backlog "once" only ever adopted the
 * branches that happened to be ON at that one instant.
 *
 * Three rounds of that was enough. A switch reading ON above a muted node is a
 * lie the node tells about itself, and no bookkeeping is worth it. Hand-muting
 * something inside a live branch now lasts until the muter next looks — turn the
 * branch off instead, which is what the switch is for.
 *
 * Ownership is still recorded on the way DOWN, so a node this muter never
 * touched is not re-marked, and the mute path stays as careful as it was.
 */
function applyBranchMode(node) {
  const slots = branchInputs(node);
  let muted = 0;

  /* ONLY what each branch is wired to. Nothing upstream of it.
   *
   * This used to sweep the target's private ancestors as well, on the grounds
   * that a loader feeding a muted node is wasted work. It is not worth it:
   * switch every branch off and the sweep has nothing live left to stop it, so
   * it walks up and paints the whole graph — sampler, scheduler, upscaler and
   * all. The saving was imaginary too, since ComfyUI only runs what reaches an
   * output, so whatever a mute orphans never executes.
   *
   * Off wins over on, because the same node can be wired to two branches.
   */
  const alvosOff = new Set();
  const alvosOn = new Set();

  slots.forEach((inp, i) => {
    if (inp.link == null) return;
    const alvo = branchTarget(node, inp.name);
    if (!alvo) return;
    (branchOn(node, i) ? alvosOn : alvosOff).add(alvo);
  });

  for (const n of alvosOff) {
    // Already asleep and not ours: leave it exactly as it is.
    if (n.mode !== MODE_ALWAYS && !mutedBy(n)) continue;
    n.mode = offMode(node);
    setMutedBy(n, node.id);
    muted++;
  }
  for (const n of alvosOn) {
    if (alvosOff.has(n)) continue;
    n.mode = MODE_ALWAYS;
    setMutedBy(n, null);
  }

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
    // says what it does — and nothing else. A running tally of what is off
    // belongs on the canvas, where the muted nodes are already greyed out;
    // spelled into the label it just makes the row noisy.
    // Only a default label is rewritten: a name the user gave stays.
    if (!w.label || w.label === w.name || /^(toggle all|enabled|all (on|off))\b/i.test(String(w.label))) w.label = "Toggle All";
  }
  node.graph?.setDirtyCanvas?.(true, true);
  return muted;
}

/** Show a toggle only for a branch that actually has a wire.
 *
 * Nodes 2.0 renders from node.widgets and ignores every per-widget hide flag,
 * so the only way to keep unused toggles off the node is to take them out of
 * the list — see web/allma_bus.js for the measurements behind this. */
/* The inputs array is left exactly as ComfyUI builds it.
 *
 * There used to be a pass here that sorted the branch inputs by number and
 * rewrote each link's target_slot to match, written when the old frontend
 * rotated the array on load. It now does real damage: Autogrow COMPACTS the
 * slots itself when a middle one is emptied (disconnect branch 2 of 7 and the
 * rest renumber under your hand), and reordering on top of that moved wires
 * onto slots that had just been renamed. Measured: seven wired branches, three
 * disconnects, and the node came back with one wire after stepping into the
 * subgraph and out. With this gone the behaviour matches stock ComfyUI exactly.
 *
 * Pairing never needed it anyway: branchInputs() sorts by the NUMBER in the
 * slot name, so a toggle always follows its own branch wherever the array puts
 * it. */
/* The solo picker: "only this branch".
 *
 * Choosing a number switches that branch on and every other off. It is a
 * shortcut, not a mode — the toggles stay the truth, so flipping any of them by
 * hand breaks the promise the picker made (exactly one branch live) and it
 * falls back to "none" on the next pass. That is also why the list only offers
 * branches that actually have a wire.
 */
function soloWidget(node) {
  return node._allmaSolo || (node.widgets || []).find((w) => w.name === "solo") || null;
}

/** The branch the toggles currently say is the only live one, or "none". */
function soloAtual(node) {
  const ligados = (node._allmaToggles || [])
    .map((t, i) => ({ n: i + 1, on: branchOn(node, i) }))
    .filter((x) => x.on)
    .map((x) => x.n);
  const comFio = new Set(branchInputs(node).filter((s) => s.link != null).map(branchNumber));
  const vivos = ligados.filter((n) => comFio.has(n));
  return vivos.length === 1 ? String(vivos[0]) : "none";
}

/* Toggle restriction — how many branches may be on at once.
 *
 * Borrowed from rgthree's Fast Groups Muter, and kept where he keeps it: a node
 * PROPERTY, not a widget. Properties live in their own bag that LiteGraph
 * serialises for us, so adding one cannot shift widgets_values — which is
 * exactly the accident that broke every older muter when `solo` was inserted
 * between the master and the switches.
 *
 *   default    — nothing is enforced, the switches are free
 *   max one    — turning one on turns every other off; all-off is allowed
 *   always one — the same, and the last one on refuses to be turned off
 *
 * `solo` does the same move by hand; this makes it the rule.
 */
const RESTRICOES = ["default", "max one", "always one"];
const PROP_RESTRICAO = "toggleRestriction";
const PROP_WIRELESS = "wirelessMode";
const PROP_HIDE_SWITCHES = "hideBranchSwitches";   // só o Toggle All (e o solo) na tela
const PROP_HIDE_SOLO = "hideSolo";
const PROP_HIDE_MASTER = "hideToggleAll";
const PROP_HIDE_OUTPUTS = "hideSwitchOutputs";   // só a saída fallback (e as que têm fio)


/* Wireless also hides the branch INPUTS and the Connect button.
 *
 * The inputs cannot leave node.inputs — the links live on them and ComfyUI
 * would prune them. LiteGraph honours an explicit `input.pos`, though: an input
 * that has one drops out of the vertical slot rows (getDefaultVerticalInputs),
 * and hit-testing and drawing both use that same position. Parking the branch
 * inputs far off the node makes them unclickable and unseen while every wire
 * stays connected, and the widgets move up into the freed rows. Turning
 * wireless off removes the `pos` and the rows come back.
 *
 * The button is hidden by flag, never taken out of node.widgets (see the note
 * in syncToggles on what removing a widget does to the inputs). */
const OFFSTAGE = [-100000, -100000];
const isOffstage = (inp) => Array.isArray(inp?.pos) && inp.pos[0] === OFFSTAGE[0] && inp.pos[1] === OFFSTAGE[1];

/** Rows the slot column occupies: inputs drawn as rows vs outputs. */
function slotRows(node) {
  const rowInputs = (node.inputs || []).filter((i) => !i.widget && !i.pos).length;
  return Math.max(rowInputs, (node.outputs || []).filter((o) => !o?._allmaOnRow).length, 1);
}

function connectButtonOf(node) {
  return (node.widgets || []).find(
    (w) => w._allmaConnectBtn || w.name === "_allma_connect_btn" || w.name?.includes("Connect Selected") || w.name?.includes("Wire Selected")
  );
}

/** Apply or undo the wireless layout. `resize` adjusts the height by the rows
 * gained or lost — only on a toggle; a loaded node was saved at its size. */
function applyWirelessLayout(node, resize) {
  if (!node) return;
  const on = Boolean(node.properties?.[PROP_WIRELESS]);
  const before = slotRows(node);
  for (const inp of node.inputs || []) {
    if (!isBranchInput(inp)) continue;
    if (on) inp.pos = [...OFFSTAGE];
    else if (isOffstage(inp)) delete inp.pos;
  }
  const btn = connectButtonOf(node);
  if (btn) setWidgetHidden(btn, on);
  node._setConcreteSlots?.();
  if (resize && node.size) {
    const H = LiteGraph.NODE_SLOT_HEIGHT || 20;
    const delta = (slotRows(node) - before) * H;
    if (delta) node.setSize([node.size[0], Math.max(60, node.size[1] + delta)]);
  }
  node.setDirtyCanvas?.(true, true);
}

function toggleWireless(node) {
  if (!node) return;
  if (!node.properties) node.properties = {};
  node.properties[PROP_WIRELESS] = !node.properties[PROP_WIRELESS];
  applyWirelessLayout(node, true);
  node.setDirtyCanvas?.(true, true);
  const graph = node.graph || rootGraph();
  graph?.setDirtyCanvas?.(true, true);
  app.canvas?.setDirty?.(true, true);
}

function shouldHideLink(canvas, link) {
  try {
    if (!link || link.target_id == null) return false;
  const graph = canvas?.graph || rootGraph();
  if (!graph) return false;

  let targetNode = nodeById(graph, link.target_id);
  if (!targetNode && graph !== rootGraph()) {
    targetNode = nodeById(rootGraph(), link.target_id);
  }
  if (!targetNode || !OURS.includes(targetNode.type)) return false;
  if (!targetNode.properties?.[PROP_WIRELESS]) return false;

  const targetInput = targetNode.inputs?.[link.target_slot];
  if (!targetInput || !isBranchInput(targetInput)) return false;

  // No peek on select. Wireless means the wires stay hidden — selecting the
  // muter/bypasser or the node it points at no longer brings them back. The
  // only way to see them is to turn wireless off.
  return true;
  } catch (e) { return false; }
}

function hookCanvasLinks() {
  const canvasProto = window.LGraphCanvas?.prototype || app.canvas?.constructor?.prototype;
  if (!canvasProto || canvasProto._allmaWirelessHooked) return;
  canvasProto._allmaWirelessHooked = true;

  if (typeof canvasProto._renderAllLinkSegments === "function") {
    const origSegments = canvasProto._renderAllLinkSegments;
    canvasProto._renderAllLinkSegments = function (ctx, link, ...args) {
      if (shouldHideLink(this, link)) return;
      return origSegments.call(this, ctx, link, ...args);
    };
  }

  if (typeof canvasProto.renderLink === "function") {
    const origRender = canvasProto.renderLink;
    canvasProto.renderLink = function (ctx, a, b, link, ...args) {
      const l = link || args[args.length - 1]?.link;
      if (shouldHideLink(this, l)) return;
      return origRender.call(this, ctx, a, b, link, ...args);
    };
  }

  if (typeof canvasProto.drawLink === "function") {
    const origDraw = canvasProto.drawLink;
    canvasProto.drawLink = function (a, b, link, ...args) {
      if (shouldHideLink(this, link)) return;
      return origDraw.call(this, a, b, link, ...args);
    };
  }
}

/**
 * Wi-fi symbol — three arcs over a dot — drawn straight on the canvas, white.
 * No emoji: an emoji glyph renders differently on every system font, and on
 * some it is a color picture that ignores fillStyle entirely.
 */
function drawWifiGlyph(ctx, cx, bottomY, color) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 1.6;
  ctx.lineCap = "round";
  for (const r of [3.2, 6.2, 9.2]) {
    ctx.beginPath();
    ctx.arc(cx, bottomY, r, -Math.PI * 0.75, -Math.PI * 0.25);
    ctx.stroke();
  }
  ctx.beginPath();
  ctx.arc(cx, bottomY, 1.35, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawWirelessBadge(node, ctx) {
  if (!ctx || !node.size || node.flags?.collapsed) return;
  const isWireless = Boolean(node.properties?.[PROP_WIRELESS]);
  const titleH = LiteGraph.NODE_TITLE_HEIGHT || 30;
  const btnW = 26;
  const btnH = 18;
  const btnX = node.size[0] - btnW - 6;
  const btnY = -titleH + (titleH - btnH) / 2;

  ctx.save();
  ctx.beginPath();
  const radius = 4;
  if (typeof ctx.roundRect === "function") {
    ctx.roundRect(btnX, btnY, btnW, btnH, radius);
  } else {
    ctx.rect(btnX, btnY, btnW, btnH);
  }

  if (isWireless) {
    ctx.fillStyle = "#0284c7";
    ctx.fill();
    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 1;
    ctx.stroke();

    drawWifiGlyph(ctx, btnX + btnW / 2, btnY + btnH - 3.5, "#ffffff");
  } else {
    ctx.fillStyle = "rgba(255, 255, 255, 0.07)";
    ctx.fill();
    ctx.strokeStyle = "rgba(255, 255, 255, 0.15)";
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.globalAlpha = 0.35;
    drawWifiGlyph(ctx, btnX + btnW / 2, btnY + btnH - 3.5, "#ffffff");
  }
  ctx.restore();
}

/* Nodes 2.0 draws the title bar in HTML, so the canvas badge above never shows
 * there. The same button goes into the node's HTML header instead; the poller
 * puts it back if Vue re-renders the header and keeps its state in sync. */
const WIFI_SVG = '<svg width="14" height="12" viewBox="0 0 24 20" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round"><path d="M2 7.5a15 15 0 0 1 20 0"/><path d="M5.5 11a10 10 0 0 1 13 0"/><path d="M9 14.5a5 5 0 0 1 6 0"/><circle cx="12" cy="17.6" r="1.4" fill="#fff" stroke="none"/></svg>';
function ensureVueWirelessButton(node) {
  if (!node || node.graph !== app.canvas?.graph) return;
  const header = document.querySelector(`.lg-node[data-node-id="${node.id}"] [data-testid="node-header-${node.id}"]`);
  const row = header?.firstElementChild;
  if (!row) return;
  if (!document.getElementById("allma-wifi-style")) {
    const st = document.createElement("style");
    st.id = "allma-wifi-style";
    st.textContent = `.allma-wifi-btn{flex:none;display:inline-flex;align-items:center;justify-content:center;width:26px;height:18px;margin-left:4px;padding:0;border-radius:4px;cursor:pointer;border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.07)}
.allma-wifi-btn svg{opacity:.35;pointer-events:none}
.allma-wifi-btn:hover{border-color:#38bdf8}
.allma-wifi-btn.on{background:#0284c7;border-color:#38bdf8}
.allma-wifi-btn.on svg{opacity:1}`;
    document.head.append(st);
  }
  let btn = row.querySelector(":scope > .allma-wifi-btn");
  if (!btn) {
    btn = document.createElement("button");
    btn.type = "button";
    btn.className = "allma-wifi-btn";
    btn.innerHTML = WIFI_SVG;
    // Not a drag, not a selection: just the toggle.
    for (const ev of ["pointerdown", "mousedown", "dblclick"]) btn.addEventListener(ev, (e) => e.stopPropagation());
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      e.preventDefault();
      toggleWireless(node);
      ensureVueWirelessButton(node);
    });
    row.append(btn);
  }
  const on = Boolean(node.properties?.[PROP_WIRELESS]);
  if (btn.classList.contains("on") !== on) btn.classList.toggle("on", on);
  const tip = on ? "Wireless ON (wires hidden) — click to show them" : "Wireless OFF — click to hide the wires";
  if (btn.title !== tip) btn.title = tip;
}

/**
 * Canvas: each switch output sits on its switch's row, at the right edge —
 * the same spot a widget's own input socket has on the left. Its name is not
 * drawn there (it would cover the switch's true/false); the name stays on the
 * slot, for tooltips, Nodes 2.0 and when the switches are hidden, in which
 * case the outputs go back to the normal list. Nodes 2.0 lays out its own
 * slots in HTML, so there they stay in the list.
 */
function placeSwitchOutputs(node) {
  const outs = node.outputs || [];
  if (outs.length < 2) return;
  const all = node._allmaToggles || [];
  const inVue = Boolean(window.LiteGraph?.vueNodesMode);
  const hideBranches = Boolean(node.properties?.[PROP_HIDE_SWITCHES]);
  const SH = LiteGraph.NODE_SLOT_HEIGHT || 20;
  const WH = LiteGraph.NODE_WIDGET_HEIGHT || 20;
  let changed = false;
  for (let i = 1; i < outs.length; i++) {
    const o = outs[i];
    const t = all[i - 1];
    const y = t?.y ?? t?.last_y;
    const onRow = !inVue && !hideBranches && !node.flags?.collapsed && !!t && !t.hidden && y != null;
    if (onRow) {
      const h = t.computedHeight || WH;
      const pos = [node.size[0] + 1 - SH * 0.5, y + h / 2];
      if (!o._allmaOnRow || !o.pos || Math.abs(o.pos[0] - pos[0]) > 0.5 || Math.abs(o.pos[1] - pos[1]) > 0.5) {
        o.pos = pos;
        o._allmaOnRow = true;
        changed = true;
      }
    } else if (o._allmaOnRow || o.pos) {   // (a saved spot, from a canvas session)
      delete o.pos;
      delete o._allmaOnRow;
      changed = true;
    }
  }
  // The drawn copy of each slot: no label for the ones on a switch row.
  (node._concreteOutputs || []).forEach((c, i) => {
    if (!c || i === 0) return;
    const hide = Boolean(outs[i]?._allmaOnRow);
    if (hide && !c._allmaNoLabel) {
      Object.defineProperty(c, "renderingLabel", { get: () => "", configurable: true });
      c._allmaNoLabel = true;
    } else if (!hide && c._allmaNoLabel) {
      delete c.renderingLabel;
      delete c._allmaNoLabel;
    }
  });
  if (changed) node.setDirtyCanvas?.(true, true);
}

function restricaoDe(node) {
  const v = String(node?.properties?.[PROP_RESTRICAO] ?? "default");
  return RESTRICOES.includes(v) ? v : "default";
}

/** Write a branch switch for real: the promoted widget too, when there is one. */
function escreverToggle(node, i, valor) {
  const t = (node._allmaToggles || [])[i];
  if (!t) return;
  const outer = promotedWidget(node, `on_${i + 1}`);
  if (outer) outer.value = valor;
  t.value = valor;
}

function ramoTemFio(node, i) {
  return isWired(node, `values.value_${i + 1}`);
}

/** The branches that are on AND wired, by index. */
function ramosVivos(node) {
  return (node._allmaToggles || [])
    .map((_, i) => i)
    .filter((i) => ramoTemFio(node, i) && branchOn(node, i));
}

/** Enforce the restriction after branch `i` was flipped by hand. */
function reforcarRestricao(node, i) {
  const modo = restricaoDe(node);
  if (modo === "default") return false;
  const all = node._allmaToggles || [];
  if (!all[i]) return false;
  let mexeu = false;

  if (branchOn(node, i)) {
    // turned ON: it becomes the only one
    all.forEach((_, k) => {
      if (k !== i && branchOn(node, k)) { escreverToggle(node, k, false); mexeu = true; }
    });
  } else if (modo === "always one" && ramosVivos(node).length === 0) {
    // turned OFF and nothing is left: refuse, there must always be one
    escreverToggle(node, i, true);
    mexeu = true;
  }
  return mexeu;
}

/** Enforce it on the node as it stands — after the property itself changes, or
 *  after the master was clicked. Keeps the FIRST live branch and drops the rest. */
function ajustarAoModo(node) {
  const modo = restricaoDe(node);
  if (modo === "default") return false;
  const vivos = ramosVivos(node);
  let mexeu = false;
  if (vivos.length > 1) {
    for (const k of vivos.slice(1)) { escreverToggle(node, k, false); mexeu = true; }
  } else if (!vivos.length && modo === "always one") {
    const primeiro = (node._allmaToggles || []).findIndex((_, i) => ramoTemFio(node, i));
    if (primeiro >= 0) { escreverToggle(node, primeiro, true); mexeu = true; }
  }
  return mexeu;
}

function aplicarSolo(node, escolha) {
  if (!node._allmaToggles?.length) {
    node._allmaToggles = (node.widgets || []).filter((w) => /^on_\d+$/.test(String(w.name)));
  }
  if (escolha === "none") {
    (node._allmaToggles || []).forEach((_, i) => escreverToggle(node, i, false));
  } else {
    const alvo = Number(escolha);
    if (!Number.isFinite(alvo)) return;
    (node._allmaToggles || []).forEach((_, i) => escreverToggle(node, i, i + 1 === alvo));
  }
  applyBranchMode(node);
  syncToggles(node);
}

/** Hide/show a widget in BOTH renderers: the canvas reads `hidden`, Nodes 2.0
 * (Vue) reads `options.hidden` — the same pair ComfyUI's own code sets. */
function setWidgetHidden(w, hide) {
  if (!w) return;
  hide = Boolean(hide);
  w.hidden = hide;
  if (w.options) w.options.hidden = hide;
  else w.options = { hidden: hide };
}

/** Tell Nodes 2.0 that slot labels changed (it keeps its own copy). */
function announceSlotLabels(node) {
  // Only when something changed: syncToggles runs often.
  const sig = [...(node.inputs || []), "|", ...(node.outputs || [])].map((x) => (typeof x === "string" ? x : x?.label ?? x?.name)).join("\u0001");
  if (sig === node._allmaLabelSig) return;
  node._allmaLabelSig = sig;
  const T = window.LiteGraph?.NodeSlotType || { INPUT: 1, OUTPUT: 2 };
  try {
    node.graph?.trigger?.("node:slot-label:changed", { nodeId: node.id, slotType: T.INPUT ?? 1 });
    node.graph?.trigger?.("node:slot-label:changed", { nodeId: node.id, slotType: T.OUTPUT ?? 2 });
  } catch { /* older frontends: nothing to tell */ }
}

/**
 * One BOOLEAN output per switch (the Python side declares fallback + on_1..25).
 * The node keeps outputs only up to the highest wired branch — or the highest
 * output that already has a wire — so a 3-branch bypasser shows fallback, 1,
 * 2, 3 and nothing else, in both renderers. Outputs are only ever added or
 * removed at the END, so every index still matches what Python returns.
 */
function syncSwitchOutputs(node, wired) {
  const outs = node.outputs;
  const all = node._allmaToggles || [];
  if (!Array.isArray(outs) || !outs.length || !all.length) return;
  let keep = 0;
  // "Hide Outputs" — and "Hide Branch Switches", whose outputs go with them —
  // leave no switch outputs besides the ones already wired.
  const hideOuts = node.properties?.[PROP_HIDE_OUTPUTS] || node.properties?.[PROP_HIDE_SWITCHES];
  if (!hideOuts) for (const n of wired) keep = Math.max(keep, n);
  outs.forEach((o, i) => { if (i > 0 && o?.links?.length) keep = Math.max(keep, i); });
  keep = Math.min(keep, all.length);
  while (node.outputs.length - 1 > keep) {
    const i = node.outputs.length - 1;
    if (node.outputs[i]?.links?.length) break;
    node.removeOutput(i);
  }
  while (node.outputs.length - 1 < keep) {
    const k = node.outputs.length;
    node.addOutput(String(k), "BOOLEAN");
  }
  // Each output reads like its switch (a renamed switch renames its output).
  for (let i = 1; i < node.outputs.length; i++) {
    const t = all[i - 1];
    const label = t?.label || String(i);
    if (node.outputs[i].label !== label) node.outputs[i].label = label;
    node.outputs[i].tooltip = `Whether switch "${label}" is on`;
  }
}

function syncToggles(node) {
  const all = node._allmaToggles || [];
  if (!all.length) return;
  const slots = branchInputs(node);
  // Which BRANCH NUMBERS are wired — not how many. Autogrow leaves holes: a
  // real muter had v1 v2 v4..v10 wired with v3 empty, and showing "the first 9
  // toggles" then handed switch 3 to nothing and left branch 10 with no switch
  // at all. A branch with no toggle reads as ON and can never be turned off.
  const wired = new Set(slots.filter((s) => s.link != null).map(branchNumber));
  // Mapa de slots por número de ramo para sincronizar toggle e slot
  const slotMap = new Map();
  slots.forEach((inp) => {
    const num = branchNumber(inp);
    if (num) slotMap.set(num, inp);
  });

  // Rótulo padrão (número simples, on_N ou value_N) não é considerado nome customizado
  const isDefaultLabel = (str) => !str || /^\d+$/.test(str) || /^on_\d+$/.test(str) || /(^|\.)value_\d+$/.test(str);

  // Toggle labels follow the slot they govern, so the pair reads as one row.
  // Preserva nomes customizados dados pelo usuário (não reseta para o número).
  all.forEach((t, i) => {
    const num = i + 1;
    const slot = slotMap.get(num);
    const outer = promotedWidget(node, `on_${num}`);
    const customToggle = t.label && !isDefaultLabel(t.label) ? t.label : null;
    const customSlot = slot?.label && !isDefaultLabel(slot.label) ? slot.label : null;
    const customOuter = outer?.label && !isDefaultLabel(outer.label) ? outer.label : null;
    const label = customToggle || customSlot || customOuter || `${num}`;

    t.label = label;
    if (slot) slot.label = label;
    if (outer && (!outer.label || isDefaultLabel(outer.label))) outer.label = label;
  });

  slots.forEach((inp) => {
    const num = branchNumber(inp);
    if (!slotMap.has(num) && isDefaultLabel(inp.label)) {
      inp.label = `${num}`;
    }
  });

  const master = node._allmaMaster || (node.widgets || []).find((w) => w.name === "enabled");
  const connectBtn = (node.widgets || []).find(
    (w) => w._allmaConnectBtn || w.name === "_allma_connect_btn" || w.name?.includes("Connect Selected") || w.name?.includes("Wire Selected")
  );
  if (connectBtn) {
    connectBtn.name = "Connect Selected Nodes";
    connectBtn.label = "Connect Selected Nodes";
    connectBtn._allmaConnectBtn = true;
  }
  const extraOthers = (node.widgets || []).filter(
    (w) => !all.includes(w) && w !== master && w !== connectBtn
  );
  const topWidgets = [connectBtn, master, ...extraOthers].filter(Boolean);
  /* Esconder por FLAG, nunca tirando de node.widgets.
   *
   * Tirar o widget da lista era o que fazia o ComfyUI podar a ENTRADA
   * correspondente — e a poda dele varre para frente com splice, pulando um a
   * cada acerto: um bypasser de 25 toggles voltava com on_1, on_3, on_5…
   * Quem sumia levava junto a promoção ligada nele, e aí todos os widgets do
   * nó-pai deslizavam uma casa (o "model = 2" do Nick). Medido em A/B: com a
   * remoção, carregar o arquivo dava 13 toggles; com a flag, 25, e o ComfyUI
   * ainda repõe os que já tinham sido perdidos.
   *
   * `hidden` é respeitado por este frontend: o node encolheu de 726 para 150px
   * de altura no teste, e a lista de entradas ficou intacta. */
  node.widgets = [...topWidgets, ...all];
  if (node.properties?.[PROP_WIRELESS]) applyWirelessLayout(node, false);
  const hideBranches = Boolean(node.properties?.[PROP_HIDE_SWITCHES]);
  all.forEach((t, i) => setWidgetHidden(t, hideBranches || !wired.has(i + 1)));
  syncSwitchOutputs(node, wired);
  announceSlotLabels(node);
  const solo = soloWidget(node);
  if (solo) setWidgetHidden(solo, node.properties?.[PROP_HIDE_SOLO]);
  if (master) setWidgetHidden(master, node.properties?.[PROP_HIDE_MASTER]);
  const outerSolo = promotedWidget(node, "solo");
  const numeros = [...wired].sort((a, b) => a - b).map(String);
  const optsValues = ["none", ...numeros];

  // The solo list shows each branch by its switch's name ("a", "Upscale"…);
  // the value stays the number, which is what Python validates.
  const soloLabel = (v) => {
    if (v == null || v === "none") return "none";
    const t = (node._allmaToggles || [])[Number(v) - 1];
    return (t?.label && String(t.label)) || String(v);
  };
  if (solo) {
    const opts = solo.options || (solo.options = {});
    opts.values = optsValues;
    opts.getOptionLabel = soloLabel;
    const real = soloAtual(node);
    if (solo.value !== real) solo.value = real;
    if (!solo.label || solo.label === solo.name) solo.label = "solo";
  }

  if (outerSolo) {
    const opts = outerSolo.options || (outerSolo.options = {});
    opts.values = optsValues;
    opts.getOptionLabel = soloLabel;
    const real = soloAtual(node);
    if (outerSolo.value !== real) outerSolo.value = real;

    if (!outerSolo._allmaHooked) {
      outerSolo._allmaHooked = true;
      const origOuter = outerSolo.callback;
      outerSolo.callback = function (...args) {
        const out = origOuter?.apply(this, args);
        const escolha = String(outerSolo.value ?? "none");
        node._allmaWiredSolo = escolha;
        if (escolha) {
          aplicarSolo(node, escolha);
        }
        return out;
      };
    }
  }
  if (typeof node.computeSize === "function") {
    const s = node.computeSize();
    const curW = node.size?.[0] ?? 0;
    const curH = node.size?.[1] ?? 0;
    const targetW = Math.max(curW, s[0], 210);
    const targetH = Math.max(curH, s[1]);
    if (curW < targetW || curH < targetH) {
      node.setSize([targetW, targetH]);
    }
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
    const vueNodes = !!document.querySelector(".lg-node");
    for (const n of allNodes(g)) {
      if (!OURS.includes(n.type)) continue;
      if (vueNodes) ensureVueWirelessButton(n);
      const slots = branchInputs(n);
      // A master driven from outside — a promoted subgraph input, a boolean —
      // has no click to fire a callback, so its change is caught here and
      // cascaded exactly as a click would. Only on CHANGE: doing it every tick
      // would overwrite a branch the user had just set on its own.
      const wiredMaster = isWired(n, "enabled") ? litValue(n, "enabled") : undefined;
      // The FIRST reading is adopted in silence, never cascaded.
      //
      // _allmaWiredMaster starts undefined on every page load, so "differs from
      // last time" was true the first time it was ever read — and the cascade
      // below then wrote the master's value over all ten branch toggles. Every
      // refresh flattened whatever the user had picked branch by branch to
      // whatever Toggle All happened to say. The switches were not being
      // ignored after a refresh; they were being erased by one.
      if (wiredMaster !== undefined && !("_allmaWiredMaster" in n)) {
        n._allmaWiredMaster = wiredMaster;
      } else if (wiredMaster !== undefined && wiredMaster !== n._allmaWiredMaster) {
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

      const rawSolo = isWired(n, "solo") ? literalFrom(n, "solo") : undefined;
      const wiredSolo = rawSolo !== undefined && rawSolo !== null ? String(rawSolo) : undefined;
      if (wiredSolo !== undefined && !("_allmaWiredSolo" in n)) {
        n._allmaWiredSolo = wiredSolo;
        if (wiredSolo) {
          aplicarSolo(n, wiredSolo);
        }
      } else if (wiredSolo !== undefined && wiredSolo !== n._allmaWiredSolo) {
        n._allmaWiredSolo = wiredSolo;
        if (wiredSolo) {
          aplicarSolo(n, wiredSolo);
        }
        rootGraph()?.setDirtyCanvas?.(true, true);
      }

      if (mirrorWiredToggles(n)) rootGraph()?.setDirtyCanvas?.(true, true);

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

/** Helper to identify if an input is one of the gate branch slots. */
function isBranchInput(inp) {
  return /(^|\.)value_\d+$/.test(String(inp?.name || ""));
}

/** Find first unwired branch slot index in node.inputs. */
function findFreeBranchSlot(node) {
  return (node.inputs || []).findIndex(
    (inp) => isBranchInput(inp) && inp.link == null
  );
}

/** Allocate next branch slot if all current slots are wired. */
function allocateNextBranchSlot(node, maxBranches = 25) {
  const currentBranches = (node.inputs || []).filter(isBranchInput);
  let maxNum = 0;
  for (const inp of currentBranches) {
    const num = branchNumber(inp);
    if (num > maxNum) maxNum = num;
  }
  const nextNum = maxNum + 1;
  if (nextNum > maxBranches) return -1;
  const inputName = `values.value_${nextNum}`;
  let idx = (node.inputs || []).findIndex((i) => i.name === inputName);
  if (idx !== -1) return idx;
  node.addInput(inputName, "*");
  return (node.inputs || []).findIndex((i) => i.name === inputName);
}

/** Snapshot target nodes from current canvas selection or clicked node. */
function getTargetNodes(clickedNode) {
  const selMap = app.canvas?.selected_nodes || {};
  const selected = Object.values(selMap);
  const set = new Set(selected);
  if (clickedNode) set.add(clickedNode);
  return [...set].filter((n) => n && !OURS.includes(n.type));
}

/** Connect a list of source nodes to an AllmaMuter / AllmaBypasser node. */
function connectNodesToGate(targets, gateNode) {
  if (!gateNode || !targets?.length) return;
  const graph = gateNode.graph || app.canvas?.graph || app.graph;
  if (!graph) return;

  // Visual sort: top-to-bottom, then left-to-right
  const sorted = [...targets].sort((a, b) => {
    const dy = (a.pos?.[1] ?? 0) - (b.pos?.[1] ?? 0);
    if (Math.abs(dy) > 20) return dy;
    return (a.pos?.[0] ?? 0) - (b.pos?.[0] ?? 0);
  });

  for (const srcNode of sorted) {
    if (srcNode === gateNode || OURS.includes(srcNode.type)) continue;

    // Skip if already wired into this gate
    const alreadyConnected = (gateNode.inputs || []).some((inp) => {
      if (inp.link == null) return false;
      const link = getLink(graph, inp.link);
      return link && link.origin_id === srcNode.id;
    });
    if (alreadyConnected) continue;

    // Pick best output on srcNode: prefer one with outgoing links, or fallback to 0
    let outSlot = -1;
    if (srcNode.outputs && srcNode.outputs.length > 0) {
      outSlot = srcNode.outputs.findIndex((o) => o.links && o.links.length > 0);
      if (outSlot === -1) outSlot = 0;
    }
    if (outSlot === -1) continue;

    let inSlot = findFreeBranchSlot(gateNode);
    if (inSlot === -1) {
      inSlot = allocateNextBranchSlot(gateNode);
    }
    if (inSlot === -1) {
      console.warn("[AllmaNodes] Max branches reached on", gateNode.title || gateNode.type);
      break;
    }

    srcNode.connect(outSlot, gateNode, inSlot);
  }

  setTimeout(() => {
    syncToggles(gateNode);
    applyBranchMode(gateNode);
    // A gate created by "Add Allma Muter/Bypasser" is fitted to its rows once
    // the branches are wired (it was born ~700px tall).
    if (gateNode._allmaFitOnce) {
      delete gateNode._allmaFitOnce;
      fitGateHeight(gateNode);
    }
    gateNode.setDirtyCanvas?.(true, true);
    graph.setDirtyCanvas?.(true, true);
  }, 20);
}

/** Shrink the node to the height its visible rows need (never grows it). */
function fitGateHeight(node) {
  if (typeof node?.computeSize !== "function" || !node.size) return false;
  const s = node.computeSize();
  if (!s?.[1] || node.size[1] <= s[1] + 4) return false;
  node.setSize([Math.max(node.size[0], s[0]), s[1]]);
  return true;
}

/** Create a new Allma Muter or Bypasser next to the targets and wire them. */
function connectNodesToNewGate(targets, gateType) {
  if (!targets?.length) return;
  const graph = targets[0]?.graph || app.canvas?.graph || app.graph;
  if (!graph) return;

  let maxX = -Infinity;
  let minY = Infinity;
  for (const n of targets) {
    const x = n.pos ? n.pos[0] : 0;
    const y = n.pos ? n.pos[1] : 0;
    const w = n.size ? n.size[0] : 200;
    if (x + w > maxX) maxX = x + w;
    if (y < minY) minY = y;
  }

  const gateNode = LiteGraph.createNode(gateType);
  if (!gateNode) {
    console.error(`[AllmaNodes] Failed to create node: ${gateType}`);
    return;
  }

  gateNode.pos = [maxX + 60, minY];
  gateNode._allmaFitOnce = true;
  graph.add(gateNode);

  connectNodesToGate(targets, gateNode);

  if (app.canvas?.selectNode) {
    app.canvas.selectNode(gateNode);
  }
}

/** Disconnect all branch inputs from a gate node. */
function disconnectAllBranches(gateNode) {
  const graph = gateNode.graph || app.canvas?.graph || app.graph;
  if (!graph) return;
  const slots = branchInputs(gateNode);
  for (const slot of slots) {
    if (slot.link != null) {
      graph.removeLink(slot.link);
    }
  }
  setTimeout(() => {
    syncToggles(gateNode);
    applyBranchMode(gateNode);
    gateNode.setDirtyCanvas?.(true, true);
    graph.setDirtyCanvas?.(true, true);
  }, 20);
}

app.registerExtension({
  name: "allma.gate",

  async setup() {
    startPolling();
    hookCanvasLinks();
    /* The wires themselves are put back by allma_slotfix.js, which does it for
     * every node in the workflow. This only redraws what depends on them: a
     * branch that changed hands has to mute the node it now points at. */
    onRepair(() => {
      const g = rootGraph();
      if (!g) return;
      for (const n of allNodes(g)) {
        if (OURS.includes(n.type)) { syncToggles(n); applyBranchMode(n); }
      }
    });
  },

  getNodeMenuItems(node) {
    if (!node) return [];

    if (OURS.includes(node.type)) {
      return [
        {
          content: node.properties?.[PROP_WIRELESS]
            ? "Wireless Mode: ON (Hide Wires)"
            : "Wireless Mode: OFF (Show Wires)",
          callback: () => toggleWireless(node),
        },
        {
          content: "Connect Selected Nodes",
          callback: () => {
            const targets = getTargetNodes(null);
            if (!targets.length) {
              alert("Selecione um ou mais nós no canvas antes de conectar.");
              return;
            }
            connectNodesToGate(targets, node);
          },
        },
        {
          content: "Disconnect All Branches",
          callback: () => {
            disconnectAllBranches(node);
          },
        },
        // Same switches as the Properties panel — Nodes 2.0 has no such panel
        // in its context menu, so they live here too. ✓ = shown.
        {
          content: "Show / Hide",
          has_submenu: true,
          submenu: {
            options: [
              [PROP_HIDE_MASTER, "Toggle All"],
              [PROP_HIDE_SOLO, "Solo"],
              [PROP_HIDE_SWITCHES, "Branch Switches"],
              [PROP_HIDE_OUTPUTS, "Switch Outputs"],
            ].map(([prop, label]) => ({
              content: `${node.properties?.[prop] ? "\u2003 " : "\u2713 "}${label}`,
              callback: () => node.setProperty(prop, !node.properties?.[prop]),
            })),
          },
        },
      ];
    }

    const targets = getTargetNodes(node);
    if (!targets.length) return [];

    const labelSuffix = targets.length > 1 ? ` (${targets.length} nodes)` : "";

    return [
      {
        content: `Add Allma Muter${labelSuffix}`,
        callback: () => {
          connectNodesToNewGate(targets, NODE);
        },
      },
      {
        content: `Add Allma Bypasser${labelSuffix}`,
        callback: () => {
          connectNodesToNewGate(targets, BYPASS_NODE);
        },
      },
    ];
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

    // Shows the property as a dropdown in the Properties panel, the same way
    // rgthree's does.
    nodeType["@" + PROP_RESTRICAO] = { type: "combo", values: RESTRICOES };
    nodeType["@" + PROP_WIRELESS] = { type: "boolean", label: "Wireless Mode (Hide Wires)" };
    nodeType["@" + PROP_HIDE_SWITCHES] = { type: "boolean", label: "Hide Branch Switches" };
    nodeType["@" + PROP_HIDE_SOLO] = { type: "boolean", label: "Hide Solo" };
    nodeType["@" + PROP_HIDE_MASTER] = { type: "boolean", label: "Hide Toggle All" };
    nodeType["@" + PROP_HIDE_OUTPUTS] = { type: "boolean", label: "Hide Outputs" };

    const origProp = nodeType.prototype.onPropertyChanged;
    nodeType.prototype.onPropertyChanged = function (name, value) {
      const r = origProp?.apply(this, arguments);
      if (name === PROP_RESTRICAO) {
        // Picking a restriction while several branches are on has to mean
        // something: keep the first live one, drop the rest.
        ajustarAoModo(this);
        applyBranchMode(this);
        syncToggles(this);
      } else if (name === PROP_WIRELESS) {
        applyWirelessLayout(this, true);
        app.canvas?.setDirty?.(true, true);
      } else if (name === PROP_HIDE_SWITCHES || name === PROP_HIDE_SOLO || name === PROP_HIDE_MASTER || name === PROP_HIDE_OUTPUTS) {
        // Rows appear/disappear: the node takes exactly the height they need.
        syncToggles(this);
        const need = this.computeSize?.();
        if (need?.[1]) this.setSize([Math.max(this.size[0], need[0]), need[1]]);
        app.canvas?.setDirty?.(true, true);
      }
      return r;
    };

    // A branch slot is named the moment it is created. Autogrow adds slots as
    // links arrive (also while loading), and Nodes 2.0 reads a slot's label
    // only when it first draws it: named later, it kept showing "value_3".
    const origInputAdded = nodeType.prototype.onInputAdded;
    nodeType.prototype.onInputAdded = function (slot) {
      const r = origInputAdded?.apply(this, arguments);
      const num = /(^|\.)value_\d+$/.test(String(slot?.name)) ? branchNumber(slot) : 0;
      if (num && (!slot.label || /(^|\.)value_\d+$/.test(slot.label))) {
        const t = (this._allmaToggles || [])[num - 1] || toggleFor(this, num - 1);
        const custom = t?.label && !/^\d+$/.test(t.label) && !/^on_\d+$/.test(t.label) ? t.label : null;
        slot.label = custom || String(num);
      }
      return r;
    };

    const origCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = origCreated?.apply(this, arguments);
      const node = this;

      // A brand-new node is born at the height ComfyUI computed while all
      // 25 on_N switches were still counted (~700px), and the size code only
      // ever grows it. Shrink it once to what computeSize actually needs —
      // only for new nodes: a loaded one keeps the size it was saved with
      // (configure runs before this timeout and sets _allmaConfigured).
      // The switches are hidden a moment later (syncToggles), so check a few
      // times during the first half second.
      for (const ms of [0, 60, 150, 300, 500]) {
        setTimeout(() => {
          if (node._allmaConfigured || node._allmaSized || node._allmaFitOnce) return;   // menu path fits after wiring
          const s = node.computeSize?.();
          if (s?.[1] > 300) return;   // switches not hidden yet: try again
          if (fitGateHeight(node)) node._allmaSized = true;
          node.setDirtyCanvas?.(true, true);
        }, ms);
      }

      // Captured once: collapsing takes them out of node.widgets, so this is the
      // only reference that survives an unused branch.
      node._allmaToggles = (node.widgets || []).filter((w) => /^on_\d+$/.test(String(w.name)));

      if (!node.properties) node.properties = {};
      if (node.properties[PROP_RESTRICAO] === undefined) node.properties[PROP_RESTRICAO] = "default";
      if (node.properties[PROP_WIRELESS] === undefined) node.properties[PROP_WIRELESS] = false;
      if (node.properties[PROP_HIDE_SWITCHES] === undefined) node.properties[PROP_HIDE_SWITCHES] = false;
      if (node.properties[PROP_HIDE_SOLO] === undefined) node.properties[PROP_HIDE_SOLO] = false;
      if (node.properties[PROP_HIDE_MASTER] === undefined) node.properties[PROP_HIDE_MASTER] = false;
      if (node.properties[PROP_HIDE_OUTPUTS] === undefined) node.properties[PROP_HIDE_OUTPUTS] = false;

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
      node._allmaMaster = master || null;

      node._allmaSolo = (node.widgets || []).find((w) => w.name === "solo") || null;
      if (node._allmaSolo) {
        const origSolo = node._allmaSolo.callback;
        node._allmaSolo.callback = function (...args) {
          const out = origSolo?.apply(this, args);
          const escolha = node._allmaSolo.value;
          if (escolha) aplicarSolo(node, escolha);
          return out;
        };
      }
      if (master) {
        const orig = master.callback;
        master.callback = function (...args) {
          const out = orig?.apply(this, args);
          const v = master.value !== false;
          for (const t of node._allmaToggles) t.value = v;
          // Under a restriction "all on" is not a state the node may hold, so
          // the bulk command lands on the first branch and stops there.
          ajustarAoModo(node);
          applyBranchMode(node);
          syncToggles(node);
          return out;
        };
      }
      node._allmaToggles.forEach((t, i) => {
        const orig = t.callback;
        t.callback = function (...args) {
          const out = orig?.apply(this, args);
          reforcarRestricao(node, i);
          applyBranchMode(node);
          syncToggles(node);
          return out;
        };
      });

      if (!node.widgets?.some((w) => w._allmaConnectBtn || w.name === "_allma_connect_btn" || w.name?.includes("Connect Selected") || w.name?.includes("Wire Selected"))) {
        const btn = node.addWidget("button", "Connect Selected Nodes", null, () => {
          const targets = getTargetNodes(null);
          if (!targets.length) {
            alert("Selecione um ou mais nós no canvas antes de conectar.");
            return;
          }
          connectNodesToGate(targets, node);
        });
        btn.name = "Connect Selected Nodes";
        btn.label = "Connect Selected Nodes";
        btn._allmaConnectBtn = true;
        btn.serialize = false;
      }

      setTimeout(() => syncToggles(node), 0);
      return r;
    };

    const origGetExtra = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
      origGetExtra?.apply(this, arguments);
      options.push(
        {
          content: this.properties?.[PROP_WIRELESS]
            ? "Wireless Mode: ON (Hide Wires)"
            : "Wireless Mode: OFF (Show Wires)",
          callback: () => toggleWireless(this),
        },
        {
          content: "Connect Selected Nodes",
          callback: () => {
            const targets = getTargetNodes(null);
            if (!targets.length) {
              alert("Selecione um ou mais nós no canvas antes de conectar.");
              return;
            }
            connectNodesToGate(targets, this);
          },
        },
        {
          content: "Rename Branch...",
          callback: () => {
            const slots = branchInputs(this).filter((s) => s.link != null);
            if (!slots.length) {
              alert("Nenhum ramo conectado.");
              return;
            }
            const nums = slots.map(branchNumber);
            const branchChoice = prompt(`Qual ramo deseja renomear? (${nums.join(", ")})`, String(nums[0]));
            if (!branchChoice) return;
            const bNum = Number(branchChoice);
            const slot = slots.find((s) => branchNumber(s) === bNum);
            if (!slot) {
              alert(`Ramo ${branchChoice} não encontrado.`);
              return;
            }
            const currentName = (!slot.label || /^\d+$/.test(slot.label)) ? "" : slot.label;
            const newName = prompt(`Nome para o switch/ramo ${bNum}:`, currentName);
            if (newName !== null) {
              const val = newName.trim();
              slot.label = val || `${bNum}`;
              const t = toggleFor(this, bNum - 1);
              if (t) t.label = slot.label;
              syncToggles(this);
              this.setDirtyCanvas(true, true);
            }
          },
        },
        {
          content: "Disconnect All Branches",
          callback: () => {
            disconnectAllBranches(this);
          },
        }
      );
    };

    const origDrawFg = nodeType.prototype.onDrawForeground;
    nodeType.prototype.onDrawForeground = function (ctx, canvas) {
      try { placeSwitchOutputs(this); } catch { /* só posição visual */ }
      const r = origDrawFg?.apply(this, arguments);
      drawWirelessBadge(this, ctx);
      return r;
    };

    const origMouseDown = nodeType.prototype.onMouseDown;
    nodeType.prototype.onMouseDown = function (e, localPos, canvas) {
      if (this.flags?.collapsed) return origMouseDown?.apply(this, arguments);

      const titleH = LiteGraph.NODE_TITLE_HEIGHT || 30;
      const btnW = 26;
      const btnH = 18;
      const btnX = this.size[0] - btnW - 6;
      const btnY = -titleH + (titleH - btnH) / 2;

      if (
        localPos &&
        localPos[0] >= btnX &&
        localPos[0] <= btnX + btnW &&
        localPos[1] >= btnY &&
        localPos[1] <= btnY + btnH
      ) {
        toggleWireless(this);
        return true;
      }

      return origMouseDown?.apply(this, arguments);
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

    // The native computeSize counts every non-widget input as a row, parked
    // or not — syncToggles would then grow the node straight back to the
    // height wireless had just taken away.
    const origComputeSize = nodeType.prototype.computeSize;
    // The widgets are laid out BELOW the slots' bounds. An output parked on a
    // switch row would push the switches down, and the output with them, every
    // frame. While measuring, such outputs count as sitting at the top (among
    // the inputs); their real spot is measured right after, for hit testing.
    const origMeasure = nodeType.prototype._measureSlots;
    if (typeof origMeasure === "function") {
      nodeType.prototype._measureSlots = function () {
        const rows = (this.outputs || []).map((o, i) => (o?._allmaOnRow ? i : -1)).filter((i) => i >= 0);
        if (!rows.length) return origMeasure.apply(this, arguments);
        const saved = rows.map((i) => this.outputs[i].pos);
        const top = (LiteGraph.NODE_SLOT_HEIGHT || 20) * 0.7;
        rows.forEach((i) => { this.outputs[i].pos = [saved[0][0], top]; });
        let r;
        try { r = origMeasure.apply(this, arguments); }
        finally { rows.forEach((i, k) => { this.outputs[i].pos = saved[k]; }); }
        rows.forEach((i) => { const c = this._concreteOutputs?.[i]; if (c) this._measureSlot?.(c, i, false); });
        return r;
      };
    }

    nodeType.prototype.computeSize = function (out) {
      const sz = (origComputeSize || window.LGraphNode?.prototype?.computeSize).apply(this, arguments);
      const parked = (this.inputs || []).filter((i) => !i.widget && isOffstage(i)).length;
      const onRow = (this.outputs || []).some((o) => o?._allmaOnRow);
      if ((!parked && !onRow) || !sz) return sz;
      const H = LiteGraph.NODE_SLOT_HEIGHT || 20;
      const all = Math.max((this.inputs || []).filter((i) => !i.widget).length, (this.outputs || []).length, 1);
      sz[1] -= (all - slotRows(this)) * H;
      return sz;
    };

    // On load the saved modes are already right; re-applying only repairs a
    // workflow edited elsewhere, and must wait until every node exists.
    const origConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
      const r = origConfigure?.apply(this, arguments);
      this._allmaConfigured = true;   // loaded/pasted: keep the saved size
      // While loading, ComfyUI re-adds the inputs one by one and grows the node
      // to fit each step (one row too many for a moment): a node saved at its
      // exact height came back 20px taller after every refresh. Put the saved
      // size back once the switches are sorted — never below what it needs.
      const saved = Array.isArray(info?.size) || info?.size?.length ? [info.size[0], info.size[1]] : null;
      setTimeout(() => {
        syncToggles(this); applyBranchMode(this); applyWirelessLayout(this, false);
        if (saved && this.size && typeof this.computeSize === "function") {
          const need = this.computeSize();
          const w = Math.max(saved[0], need[0]), h = Math.max(saved[1], need[1]);
          if (Math.abs(this.size[0] - w) > 1 || Math.abs(this.size[1] - h) > 1) {
            this.setSize([w, h]);
            this.setDirtyCanvas?.(true, true);
          }
        }
      }, 100);
      return r;
    };

    /* widgets_values must not depend on which toggles happen to be VISIBLE.
     *
     * syncToggles hides the switches for unwired branches by taking them out of
     * node.widgets, and node.widgets is exactly the list widgets_values is built
     * from and poured back into. Saving was fine — the visible prefix is always
     * [enabled, on_1..on_k]. Loading was not: the trim can run before the values
     * are applied, so they land on a shorter list and the rest of the switches
     * come up on their defaults. A branch saved OFF came back ON.
     *
     * Same defect the bus had, same shape of fix: both directions work off the
     * full ordered set, captured once, never the drawn one. A short array from
     * an older save still lands correctly, because the prefix means the same
     * thing in both. */
    function allGateWidgets(node) {
      const master = node._allmaMaster
        || (node.widgets || []).find((w) => w.name === "enabled")
        || null;
      const solo = soloWidget(node);
      const toggles = node._allmaToggles || [];
      if (!toggles.length) return [];
      // Schema order, always: enabled, solo, on_1..on_N. widgets_values is
      // positional, so a widget missing from this list lands on the wrong one.
      return [master, solo, ...toggles].filter(Boolean);
    }

    const origSerialize = nodeType.prototype.serialize;
    nodeType.prototype.serialize = function (...args) {
      const data = origSerialize?.apply(this, args);
      const all = allGateWidgets(this);
      if (data && all.length) data.widgets_values = all.map((w) => w.value);
      return data;
    };

    /* Files saved before the solo picker existed
     *
     * widgets_values is POSITIONAL and solo was inserted between the master and
     * the toggles, so an older file pours [enabled, on_1, on_2, …] into
     * [enabled, solo, on_1, …]: solo receives a boolean it cannot hold and every
     * switch takes its neighbour's state. A muter with nine branches came back
     * with all nine wrong, which is exactly what it looked like on screen.
     *
     * Solo is the only STRING in that row — the master and the toggles are
     * booleans — so a boolean in second place is a file from before it, and
     * "none" goes back where it belongs. Nothing else has to be guessed. */
    function migrarSolo(vals) {
      if (!Array.isArray(vals) || vals.length < 2) return vals;
      if (typeof vals[1] === "string") return vals;             // já tem solo
      return [vals[0], "none", ...vals.slice(1)];
    }

    const origCfg = nodeType.prototype.configure;
    nodeType.prototype.configure = function (info, ...rest) {
      const all = allGateWidgets(this);
      const vals = migrarSolo(info?.widgets_values);
      let mine = null;
      if (all.length && Array.isArray(vals) && vals.length) {
        mine = vals.slice(0, all.length);
        info.widgets_values = mine;
      }
      const r = origCfg?.call(this, info, ...rest);
      if (mine) {
        allGateWidgets(this).forEach((w, i) => {
          if (i < mine.length && mine[i] != null) w.value = mine[i];
        });
      }
      setTimeout(() => { syncToggles(this); applyBranchMode(this); }, 120);
      return r;
    };
  },
});
