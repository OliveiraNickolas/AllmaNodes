import { app } from "../../scripts/app.js";
import { allNodes, rootGraph, literalFrom, promotedWidget } from "./allma_graph.js";

/* Allma RandomNoise — A Subgraph-friendly RandomNoise clone.
 *
 * Why this exists:
 * ComfyUI's built-in RandomNoise puts control_after_generate as a canvas-only
 * decoration on the noise_seed widget. It is NOT an input, so it cannot be
 * promoted to the Subgraph node.
 *
 * AllmaRandomNoise exposes control_after_generate as a first-class schema input,
 * allowing BOTH noise_seed and control_after_generate to be promoted to Subgraphs.
 *
 * Before prompt queue, this extension steps the seed according to
 * control_after_generate (randomize, increment, decrement, fixed), updating both
 * the inner node widget and any promoted Subgraph widget.
 */

const NODE = "AllmaRandomNoise";

function generateRandomSeed() {
  if (window.crypto && window.crypto.getRandomValues) {
    const arr = new Uint32Array(2);
    window.crypto.getRandomValues(arr);
    // Safe JS integer range (up to 2^53 - 1)
    return (arr[0] * 0x100000000 + arr[1]) % 0x10000000000000;
  }
  return Math.floor(Math.random() * 0x10000000000000);
}

function stepSeed(currentVal, mode) {
  const num = Number(currentVal) || 0;
  if (mode === "randomize") {
    return generateRandomSeed();
  } else if (mode === "increment") {
    return (num + 1) % 0x10000000000000;
  } else if (mode === "decrement") {
    return num <= 0 ? 0xffffffffffff : num - 1;
  }
  return currentVal; // "fixed"
}

function updateNodeSeed(node) {
  // Read mode, following promotions out through containing Subgraph nodes
  const mode = literalFrom(node, "control_after_generate")
    ?? node.widgets?.find((w) => w.name === "control_after_generate")?.value;
  if (!mode || mode === "fixed") return;

  const currentSeed = literalFrom(node, "noise_seed")
    ?? node.widgets?.find((w) => w.name === "noise_seed")?.value
    ?? 0;

  const newSeed = stepSeed(currentSeed, mode);

  // 1. If noise_seed was promoted to a subgraph, write to the parent's widget
  const outerSeed = promotedWidget(node, "noise_seed");
  if (outerSeed) {
    outerSeed.value = newSeed;
    outerSeed.callback?.(newSeed);
  }

  // 2. Also update the inner widget on this node
  const innerSeed = node.widgets?.find((w) => w.name === "noise_seed");
  if (innerSeed) {
    innerSeed.value = newSeed;
    innerSeed.callback?.(newSeed);
  }

  node.setDirtyCanvas?.(true, true);
  rootGraph()?.setDirtyCanvas?.(true, true);
  app.canvas?.setDirty?.(true, true);
}

let queueHooked = false;
function ensureQueueHook() {
  if (queueHooked || !app.queuePrompt) return;
  queueHooked = true;

  const origQueue = app.queuePrompt;
  app.queuePrompt = async function (...args) {
    try {
      const g = rootGraph();
      if (g) {
        for (const n of allNodes(g)) {
          if (n.type === NODE) {
            updateNodeSeed(n);
          }
        }
      }
    } catch (err) {
      console.error("[AllmaRandomNoise] Error updating seed before queue:", err);
    }
    return origQueue.apply(this, args);
  };
}

app.registerExtension({
  name: "allma.random_noise",

  async setup() {
    ensureQueueHook();
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE) return;

    const origCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = origCreated?.apply(this, arguments);
      const node = this;

      // Deduplicate control_after_generate if ComfyUI injected a canvas-only twin
      const controls = (node.widgets || []).filter((w) => w.name === "control_after_generate");
      if (controls.length > 1) {
        node.widgets = (node.widgets || []).filter((w) => w !== controls[0]);
      }

      // Remove any leftover _allma_rnd_btn
      node.widgets = (node.widgets || []).filter((w) => w.name !== "_allma_rnd_btn");

      // Attach beforeQueued on the control widget
      const ctrl = node.widgets?.find((w) => w.name === "control_after_generate");
      if (ctrl) {
        ctrl.beforeQueued = () => updateNodeSeed(node);
      }

      return r;
    };
  },
});
