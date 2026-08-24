import { app } from "../../scripts/app.js";

/* Graph helpers that do not stop at a subgraph boundary.
 *
 * ComfyUI's graph is a tree once subgraphs are in play, and almost every naive
 * traversal quietly assumes it is flat:
 *
 *   - app.graph._nodes holds the ROOT graph only. Anything inside a subgraph is
 *     absent from it, so a loop over it never sees those nodes at all.
 *   - A link that crosses into a subgraph points at the SUBGRAPH NODE, not at
 *     the node inside that actually produces the value.
 *   - The backend identifies a node by its execution path — "6141:6160" — while
 *     a link only ever carries the local id, "6160".
 *
 * Every one of those produced a bug that looked like something else entirely,
 * so the crossing logic lives here once rather than being re-derived per file.
 */

/** Look up a link by id, whichever container shape litegraph is using. */
export function getLink(graph, id) {
  const links = graph?.links;
  if (!links || id == null) return null;
  return links.get ? links.get(id) : links[id];
}

/** The subgraph a node contains, or null when it is an ordinary node. */
export function subgraphOf(node) {
  return node?.subgraph || node?.graph_instance || null;
}

export function nodeById(graph, id) {
  return graph?.getNodeById?.(id) || (graph?._nodes || []).find((n) => n.id === id) || null;
}

/** Every node in the workflow, at any depth.
 *
 * `seen` guards a subgraph that (directly or otherwise) contains itself, which
 * would otherwise recurse until the tab dies. */
export function* allNodes(graph, seen = new Set()) {
  if (!graph || seen.has(graph)) return;
  seen.add(graph);
  for (const n of graph._nodes || []) {
    yield n;
    const sub = subgraphOf(n);
    if (sub) yield* allNodes(sub, seen);
  }
}

/** Which node holds each subgraph, so a traversal can climb back out.
 *
 * A subgraph knows its own contents but keeps no reference to the node that
 * contains it, so going UP is impossible from the inside alone. Walking down
 * from the root builds the missing direction; rebuilt per traversal because a
 * workflow can be edited between calls and a stale map would resolve to a node
 * that no longer exists.
 */
function parentMap(root, map = new Map(), seen = new Set()) {
  if (!root || seen.has(root)) return map;
  seen.add(root);
  for (const n of root._nodes || []) {
    const sub = subgraphOf(n);
    if (sub && !map.has(sub)) {
      map.set(sub, { node: n, graph: root });
      parentMap(sub, map, seen);
    }
  }
  return map;
}

/** Follow an input link back to the node that really produces the value.
 *
 * Returns { node, slot, id } where `id` is the execution path the BACKEND uses,
 * built by accumulating each subgraph node's id on the way down — which is
 * exactly how ComfyUI composes it, so the two agree.
 *
 * Bounded rather than unbounded: a malformed graph must not hang the canvas. */
export function resolveUpstream(node, inputName) {
  const input = node.inputs?.find((i) => i.name === inputName);
  if (!input || input.link == null) return null;

  let graph = node.graph;
  let link = getLink(graph, input.link);
  if (!link) return null;
  let prefix = "";

  const parents = parentMap(app?.graph);

  for (let hop = 0; hop < 16; hop++) {
    // A negative origin is the subgraph's input proxy: the real source is one
    // level UP, on whatever feeds the matching slot of the containing node.
    // Without this the walk dies here, which is why a boolean outside a subgraph
    // never reached a muter inside it.
    if (link.origin_id < 0) {
      const up = parents.get(graph);
      if (!up) return null;
      const outerInput = (up.node.inputs || [])[link.origin_slot];
      const outerLink = getLink(up.graph, outerInput?.link);
      if (!outerLink) return null;
      graph = up.graph;
      link = outerLink;
      prefix = "";  // ids restart from the parent's frame of reference
      continue;
    }

    const origin = nodeById(graph, link.origin_id);
    if (!origin) return null;

    const sub = subgraphOf(origin);
    if (!sub) return { node: origin, slot: link.origin_slot, id: prefix + String(origin.id) };

    // Step inside: the subgraph's output slot records the inner link feeding it.
    const innerId = (sub.outputs || [])[link.origin_slot]?.linkIds?.[0];
    const innerLink = getLink(sub, innerId);
    if (!innerLink) return null;
    prefix += String(origin.id) + ":";
    graph = sub;
    link = innerLink;
  }
  return null;
}

/** Everything an output feeds, following links out through subgraph inputs.
 *
 * The mirror of resolveUpstream: a link leaving a node may land on a subgraph
 * node, in which case the real consumers are inside it. */
export function targetsOf(node, maxHops = 8) {
  const out = [];
  const walk = (n, graph, hops) => {
    if (hops > maxHops) return;
    for (const output of n.outputs || []) {
      for (const id of output?.links || []) {
        const link = getLink(graph, id);
        if (!link) continue;
        const target = nodeById(graph, link.target_id);
        if (!target) continue;
        const sub = subgraphOf(target);
        if (!sub) {
          out.push({ node: target, slot: link.target_slot });
          continue;
        }
        // Into the subgraph: its input slot lists the inner links it feeds.
        const inner = (sub.inputs || [])[link.target_slot]?.linkIds || [];
        for (const innerId of inner) {
          const innerLink = getLink(sub, innerId);
          if (!innerLink) continue;
          const innerNode = nodeById(sub, innerLink.target_id);
          if (innerNode) out.push({ node: innerNode, slot: innerLink.target_slot });
        }
      }
    }
  };
  walk(node, node.graph, 0);
  return out;
}

/** Chase an input back to a literal value, crossing subgraph walls both ways.
 *
 * resolveUpstream answers "which node", which is not enough here: a promoted
 * subgraph input is a WIDGET on the containing node, with no link to follow, and
 * a primitive whose own widget is fed by a link holds a stale value that must be
 * chased further. Both are ordinary ways to wire a control, and both dead-ended.
 *
 * Returns undefined when the value cannot be known before the graph runs —
 * anything a node computes — so the caller can fall back to its own widget.
 */
export function literalFrom(node, inputName, depth = 0) {
  if (!node || depth > 16) return undefined;
  const input = (node.inputs || []).find((i) => i.name === inputName);

  // No wire: the widget on this very node holds the value.
  if (!input || input.link == null) {
    const w = (node.widgets || []).find((x) => x.name === inputName);
    return w ? w.value : undefined;
  }

  let graph = node.graph;
  let link = getLink(graph, input.link);
  const parents = parentMap(app?.graph);

  for (let hop = 0; hop < 16; hop++) {
    if (!link) return undefined;

    // Out of a subgraph: the source is on the containing node's matching slot,
    // which is often a promoted WIDGET rather than another link.
    if (link.origin_id < 0) {
      const up = parents.get(graph);
      if (!up) return undefined;
      const slot = (up.node.inputs || [])[link.origin_slot];
      if (!slot) return undefined;
      if (slot.link == null) {
        const w = (up.node.widgets || []).find((x) => x.name === slot.name);
        return w ? w.value : undefined;
      }
      graph = up.graph;
      link = getLink(graph, slot.link);
      continue;
    }

    const origin = nodeById(graph, link.origin_id);
    if (!origin) return undefined;

    // Into a subgraph: keep following the inner link that feeds this output.
    const sub = subgraphOf(origin);
    if (sub) {
      const innerId = (sub.outputs || [])[link.origin_slot]?.linkIds?.[0];
      link = getLink(sub, innerId);
      graph = sub;
      continue;
    }

    // A real node. A primitive's output is its own widget — but that widget may
    // itself be driven by a link, so ask the same question one level up.
    const w = (origin.widgets || []).find((x) => x.name === "value")
      || (origin.widgets || [])[0];
    if (!w) return undefined;
    const own = (origin.inputs || []).find((i) => i.name === w.name);
    if (own && own.link != null) return literalFrom(origin, w.name, depth + 1);
    return w.value;
  }
  return undefined;
}

/** Everything upstream of one input that feeds NOTHING ELSE.
 *
 * Used to mute a branch: a node may only be switched off if every consumer it
 * has is itself being switched off. A resize shared by two slots, or a loader
 * feeding both this branch and another, must keep running — muting it would
 * break the branch that is still wanted.
 *
 * Collect the ancestors, then repeatedly drop any whose output escapes the set,
 * until nothing changes. Dropping propagates: once a node has to run, whatever
 * feeds it has to run too.
 */
export function exclusiveUpstream(node, inputName, ignoreTypes = []) {
  const input = node.inputs?.find((i) => i.name === inputName);
  if (!input || input.link == null) return [];
  const graph = node.graph;

  // 1. every ancestor, breadth-first, bounded against a malformed cycle
  const seen = new Set();
  const stack = [];
  const first = getLink(graph, input.link);
  if (first) stack.push(first.origin_id);
  while (stack.length && seen.size < 512) {
    const id = stack.pop();
    if (seen.has(id)) continue;
    const n = nodeById(graph, id);
    if (!n) continue;
    seen.add(id);
    for (const inp of n.inputs || []) {
      const l = getLink(graph, inp.link);
      if (l) stack.push(l.origin_id);
    }
  }

  // 2. drop anything whose output leaves the set, to a fixed point
  let changed = true;
  while (changed) {
    changed = false;
    for (const id of Array.from(seen)) {
      const n = nodeById(graph, id);
      if (!n) { seen.delete(id); changed = true; continue; }
      for (const out of n.outputs || []) {
        for (const lid of out?.links || []) {
          const l = getLink(graph, lid);
          if (!l) continue;
          if (l.target_id === node.id || seen.has(l.target_id)) continue;
          // A consumer that is itself a control surface does not count as a
          // real consumer: pointing a muter at a node is how you SELECT it, so
          // that link must not make the node look shared and unmutable.
          const consumer = nodeById(graph, l.target_id);
          if (consumer && ignoreTypes.includes(consumer.type)) continue;
          seen.delete(id);
          changed = true;
          break;
        }
      }
    }
  }
  return Array.from(seen).map((id) => nodeById(graph, id)).filter(Boolean);
}
