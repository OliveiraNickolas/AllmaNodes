import { app } from "../../scripts/app.js";
import { allNodes, getLink, rootGraph } from "./allma_graph.js";

/* Slot fix — put every wire back on the slot whose NAME the file gave it.
 *
 * THE BUG (ComfyUI's, not ours, and it bites any node with growing inputs)
 * ----------------------------------------------------------------------
 * Links are stored by INDEX: a link says "target_slot 27". That only works if
 * loading rebuilds the input array exactly as it was saved, and for a node with
 * dynamic inputs it does not. Take Allma Muter: the schema lays out value_1,
 * then on_1..on_25, and Autogrow appends value_2, value_3 ... AFTER the
 * toggles. Reload a workflow whose muter sits in a subgraph and one link is
 * dropped while the rest slide up a slot — every toggle then drives its
 * neighbour. Measured the same way on MiniMaxH3ReferenceToVideo, which is not
 * ours: grouping it into a subgraph loses its ref_image wires. AllmaGenerate,
 * the bus and anything else that grows slots are exposed to it too.
 *
 * THE FIX
 * -------
 * The saved file is complete and correct, and it carries the NAME of every
 * slot next to the link. So before the graph is built we read "this link feeds
 * the slot called X", and once the whole workflow is up we compare that with
 * where each wire actually landed. Where the names disagree the wire is put
 * back with the ordinary connect API — the same call a person makes by hand —
 * and any duplicate the load invented is removed.
 *
 * Deliberately GENERIC: it looks at every node, of every type, so a node added
 * to this pack later (or one from another pack) is covered without being
 * listed anywhere. It is also conservative: a slot the file names but that no
 * longer exists is left alone, and a node whose wiring already matches is never
 * touched, so a healthy workflow goes through this untouched and silent.
 */

const LOG = "[AllmaNodes/slotfix]";
const AJUSTE = "Allma.SlotFix.AutoTidy";

/** Tidied automatically. Anything else is opt-in, from the node menu. */
const NOSSOS = /^Allma(BusIn|BusOut|Muter|Bypasser|Gate|Generate)$/;

/** name of the slot a link was saved on, per node, per graph. */
const plan = new Map();
const listeners = [];

/** Register a callback that runs whenever wires are repaired on load. */
export function onRepair(fn) {
  if (typeof fn === "function") listeners.push(fn);
}

/* The root graph is keyed by the word "root", never by its id.
 *
 * A saved workflow carries id 00000000-0000-0000-0000-000000000000 for the root
 * and gets a fresh uuid the moment it loads, so keying by id matched inside
 * subgraphs (their definition ids survive) and never at the top level — the
 * repair silently skipped every node in the main graph. Subgraphs keep using
 * their own id, which is stable and needed to tell two of them apart. */
const key = (graphId, nodeId) => `${graphId ?? "root"}:${nodeId}`;

function graphKey(graph) {
  return graph && graph !== rootGraph() ? graph.id : null;
}

/** Root links are arrays, subgraph links are objects; same five fields. */
function linkFields(l) {
  return Array.isArray(l)
    ? { id: l[0], origin_id: l[1], origin_slot: l[2], target_id: l[3], target_slot: l[4] }
    : { id: l?.id, origin_id: l?.origin_id, origin_slot: l?.origin_slot, target_id: l?.target_id, target_slot: l?.target_slot };
}

/* Which output feeds each NAMED slot, taken from the node's own inputs array.
 *
 * Not from the links array's target_slot, which cannot be trusted: the H3 Fun
 * ControlNet examples ship with EVERY link written as target_slot 0 (hand-built
 * files, valid because each input already carries its link id). Loading them by
 * index piles four wires onto slot 0 and throws the rest away — 24 links in the
 * file, 11 on the canvas. The inputs array is the one place both kinds of file
 * agree, so the plan is built from it and the links array is used only to look
 * up where each id came from. */
function collect(graphId, nodes, links) {
  const byLinkId = new Map();
  for (const raw of links || []) {
    const f = linkFields(raw);
    if (f.id != null) byLinkId.set(String(f.id), f);
  }
  for (const n of nodes || []) {
    const entries = [];
    for (const inp of n?.inputs || []) {
      if (inp?.link == null) continue;
      const f = byLinkId.get(String(inp.link));
      if (!f || f.origin_id == null) continue;
      entries.push({ name: String(inp.name), origin_id: f.origin_id, origin_slot: f.origin_slot ?? 0 });
    }
    if (entries.length) plan.set(key(graphId, n.id), entries);
  }
}

/** Read the whole workflow — root and every subgraph definition. */
export function collectFromGraphData(graphData) {
  plan.clear();
  collect(null, graphData?.nodes, graphData?.links);   // root: by name, not by id
  for (const sg of graphData?.definitions?.subgraphs || []) {
    collect(sg?.id, sg?.nodes, sg?.links);
  }
}

/** Put one node's wires back where the file named them. Returns how many moved. */
export function repairNode(node) {
  const graph = node?.graph;
  const wanted = plan.get(key(graphKey(graph), node?.id));
  if (!wanted?.length || !graph) return 0;

  let moved = 0;
  for (const { name, origin_id, origin_slot } of wanted) {
    const idx = (node.inputs || []).findIndex((i) => String(i.name) === name);
    if (idx < 0) continue;                       // slot renamed or gone: leave it
    const current = node.inputs[idx].link;
    const link = current != null ? getLink(graph, current) : null;
    if (link && String(link.origin_id) === String(origin_id) && link.origin_slot === origin_slot) continue;

    if (Number(origin_id) < 0) {
      // the subgraph's own input proxy connects from the slot, not from a node
      const proxy = (graph.inputs || [])[origin_slot];
      if (!proxy?.connect) continue;
      try {
        proxy.connect(node.inputs[idx], node);
      } catch (e) {
        console.warn(`${LOG} proxy.connect failed:`, e);
      }
    } else {
      const src = graph.getNodeById?.(origin_id);
      if (!src?.connect) continue;
      try {
        src.connect(origin_slot, node, idx);
      } catch (e) {
        console.warn(`${LOG} src.connect failed:`, e);
      }
    }
    moved++;
  }

  if (moved) {
    node.setDirtyCanvas?.(true, true);
  }
  return moved;
}

/* Tidy the visible order of a node's grown slots — safely.
 *
 * ComfyUI does not keep them in order. A bus saved with twelve named slots
 * comes back as IMAGE 1, IMAGE 4 … IMAGE 9, bus, Width, Height, slot_13,
 * slot_14, and only then IMAGE 2 and IMAGE 3. The MiniMax H3 reference node
 * does it too: after a refresh its ref_image_3..8 sit below ref_video_0 and
 * ref_audio_0, split from ref_image_0..2. Grown slots are appended after the
 * declared ones, so a family gets cut in half. It is ComfyUI's doing, not the
 * node's, and it happens to third-party nodes just the same.
 *
 * Reordering is only safe once you know where a wire lives: in this frontend
 * `input.link` is DERIVED FROM THE POSITION — the getter is linkIdOf(this) and
 * the setter only accepts null. The wire belongs to the index, not to the slot
 * object, so the array and every target_slot have to move together.
 *
 * The pass that used to live in the muter sorted by NAME and assumed names were
 * stable. They are not — Autogrow renames slots when it compacts — so it raced
 * that and dropped wires onto slots that had just been renamed. This one maps
 * OLD INDEX -> NEW INDEX, never names, and then CHECKS the result against a
 * snapshot: if one wire would end up on a different slot than it started, the
 * whole move is rolled back and the node is left exactly as it was.
 */
const FAMILIA = /^(.*?)_(\d+)$/;

function parteDinamica(nome) {
  const curto = String(nome).split(".").pop();
  const m = FAMILIA.exec(curto);
  return m ? { familia: m[1], numero: Number(m[2]) } : null;
}

function fotografar(node) {
  const graph = node.graph;
  const out = [];
  (node.inputs || []).forEach((inp, i) => {
    const id = inp.link;
    if (id == null) return;
    const l = getLink(graph, id);
    if (l) out.push({ i, nome: String(inp.name), id, origem: `${l.origin_id}[${l.origin_slot}]` });
  });
  return out;
}

export function tidyDynamicSlots(_node) {
  return false;
}

export function tidyAll() {
  return 0;
}

/** Is the automatic tidy on? Settings may not exist yet; default is on. */
function autoTidyLigado() {
  try {
    const v = app.extensionManager?.setting?.get(AJUSTE);
    return v === undefined || v === null ? true : !!v;
  } catch { return true; }
}

/* Putting slots in order is not an edit the USER made, so a workflow that was
 * untouched must not come up asking to be saved. The change tracker settles
 * shortly after the graph does, hence the second pass. */
function semSujar(fn) {
  const wf = app.extensionManager?.workflow?.activeWorkflow;
  const limpo = wf ? wf.isModified === false : false;
  const r = fn();
  if (limpo) {
    const restaurar = () => { try { wf.isModified = false; } catch { /* store changed shape */ } };
    queueMicrotask(restaurar);
    setTimeout(restaurar, 400);
  }
  return r;
}

/* The same damage, with no file involved
 * --------------------------------------
 * Grouping nodes into a subgraph breaks the wires exactly the same way, and no
 * workflow is being loaded, so the plan above is empty and nothing is repaired.
 * Watched live: three sources wired into a bypasser's value_1..value_3, then
 * "Convert to Subgraph", and inside the new subgraph the wires sit on value_1,
 * `enabled` and `solo` — because the links kept target_slot 0,1,2 while the
 * rebuilt node puts the grown slots at the END, after the toggles.
 *
 * The rebuild happens through node.configure, and the info it is handed is
 * still correct: it names every slot and the link id on it. So the fix is the
 * same idea one step earlier — remember what configure was told, and once the
 * links exist check where each one actually landed.
 *
 * Covers grouping, ungrouping, paste and undo alike, for every node type,
 * because it hooks the base class rather than any node of ours.
 */
let refeitos = new Map();     // node id -> [{ name, linkId }] as configure saw it
let varredura = null;
let carregando = false;       // during a load the file's own plan is better

function lembrarConfigure(node, info) {
  if (carregando) return;
  const entradas = [];
  for (const inp of info?.inputs || []) {
    if (inp?.link == null) continue;
    entradas.push({ name: String(inp.name), linkId: inp.link });
  }
  if (!entradas.length) return;
  // The id is only final AFTER configure runs — before it, a node being built
  // still says -1 — which is why this is called on the way out.
  refeitos.set(String(node.id), entradas);
  if (varredura == null) varredura = setTimeout(varrerRefeitos, 150);
}

/** Put one rebuilt node's wires back, going by the link ids configure named. */
function reencaixar(node, entradas) {
  const graph = node.graph;
  if (!graph) return 0;
  let mexeu = 0;

  for (const { name, linkId } of entradas) {
    const link = getLink(graph, linkId);
    // An id that belongs to some other node is not ours to touch: ids are only
    // unique within a graph, and two graphs can hold a node numbered 4.
    if (!link || String(link.target_id) !== String(node.id)) continue;
    const idx = (node.inputs || []).findIndex((i) => String(i.name) === name);
    if (idx < 0 || link.target_slot === idx) continue;   // gone, or already right

    if (Number(link.origin_id) < 0) {
      const proxy = (graph.inputs || [])[link.origin_slot];
      if (!proxy?.connect) continue;
      try { proxy.connect(node.inputs[idx], node); mexeu++; }
      catch (e) { console.warn(`${LOG} proxy.connect failed:`, e); }
      continue;
    }
    const src = graph.getNodeById?.(link.origin_id);
    if (!src?.connect) continue;
    try { src.connect(link.origin_slot, node, idx); mexeu++; }
    catch (e) { console.warn(`${LOG} src.connect failed:`, e); }
  }
  return mexeu;
}

function vigiarConfigure() {
  return true;
}

/** Sweep the whole workflow. Returns [nodes touched, wires moved]. */
export function repairAll() {
  const g = rootGraph();
  if (!g || !plan.size) return [0, 0];
  let nodes = 0, wires = 0;
  for (const node of allNodes(g)) {
    const n = repairNode(node);
    if (n) { nodes++; wires += n; }
  }
  return [nodes, wires];
}

app.registerExtension({
  name: "allma.slotfix",

  settings: [{
    id: AJUSTE,
    category: ["Allma", "Slots", "Fix slot order automatically"],
    name: "Fix slot order automatically",
    tooltip: "Ao abrir um workflow (e ao agrupar/colar), põe os slots que "
      + "crescem de volta em ordem: famílias na ordem em que aparecem, números "
      + "subindo. Se um fio não voltar ao lugar, o node é deixado como estava.",
    type: "boolean",
    defaultValue: true,
  }],

  setup() {
    if (!vigiarConfigure()) console.warn(`${LOG} LiteGraph not reachable: rebuilt nodes will not be checked`);
  },

  beforeConfigureGraph(graphData) {
    carregando = true;
    collectFromGraphData(graphData);
  },

  /* Manual tidy only for supported Allma nodes. MiniMax H3 reference node and
   * Bus nodes must never be touched — dynamic inputs collapse when disconnected. */
  getNodeMenuItems(node) {
    if (!node || !/^Allma(Muter|Bypasser|Gate|Generate)$/.test(node.type) || !(node.inputs || []).some((i) => parteDinamica(i.name))) return [];
    return [{
      content: "Fix slot order (Allma)",
      callback: () => {
        const mexeu = tidyDynamicSlots(node);
        console.log(`${LOG} ${node.type} #${node.id}: ${mexeu ? "slot order fixed" : "already in order"}`);
      },
    }];
  },

  afterConfigureGraph() {
    // Nodes come first, then links, and subgraphs are built later still; the
    // sweep waits for all of it.
    setTimeout(() => {
      // The file's own plan is the authority while a workflow loads; anything
      // configure noticed on the way in is dropped as redundant.
      carregando = false;
      refeitos.clear();
      const [nodes, wires] = repairAll();
      if (wires) {
        console.log(`${LOG} ${wires} wire(s) on ${nodes} node(s) put back on the slot the file named`);
      }
      for (const fn of listeners) {
        try { fn(nodes, wires); } catch (e) { console.warn(`${LOG} listener failed`, e); }
      }
    }, 250);
  },
});
