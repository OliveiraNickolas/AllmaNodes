# Allma Nodes

General-purpose custom nodes for [ComfyUI](https://github.com/comfyanonymous/ComfyUI).

The pack started as a prompt enhancer for the [Allma](https://github.com/OliveiraNickolas/allma)
LLM backend (or any OpenAI-compatible endpoint) and grew into a small toolbox.
Two groups, independent of each other:

**LLM** (`Allma/llm`) — send a prompt, plus reference images, image metadata and
the LoRAs active in your workflow, to a local model and get engineered prompt
text back into your graph, with the model aware of what the workflow is doing.

**Graph utilities** (`Allma/utils`, `Allma/logic`) — no LLM involved. A universal
null gate for switching branches off at runtime, a VRAM unloader, and a combo
that mirrors whatever dropdown you wire it into.

## Nodes

Eight nodes in three groups. The LLM ones need an Allma backend running; the
utilities do not depend on it at all.

| Node | Category | What it is for |
|---|---|---|
| [Allma Connectivity](#allma-connectivity) | `Allma/llm` | where the backend lives and how to sample from it |
| [Allma Generate](#allma-generate) | `Allma/llm` | the node that calls the model |
| [Allma Preset Selector](#allma-preset-selector) | `Allma/llm` | preset name → its system prompt, as a `STRING` |
| [Allma Live Text](#allma-live-text) | `Allma/llm` | shows a text output while it is still being produced |
| [Allma Stop](#allma-stop) | `Allma/llm` | a stop button you can put anywhere, subgraphs included |
| [Allma Load Image](#allma-load-image) | `Allma/utils` | Load Image that also returns the file's prompt metadata |
| [Clear Allma VRAM](#clear-allma-vram) | `Allma/utils` | unloads the model so another job can have the card |
| [Combo Select (universal)](#combo-select-universal) | `Allma/utils` | a dropdown that becomes a copy of whichever it is wired to |
| [Allma Muter (false = mute)](#allma-muter-false--mute) | `Allma/logic` | one node mutes up to ten branches, with a master switch |
| [Allma Bus In / Out](#allma-bus-in--out) | `Allma/bus` | many wires down one line, unpacked under the same names |

---

### Allma Connectivity

One node holds everything about *the conversation with the model*, so several
Generate nodes on the same backend share a single set of settings.

| Widget | Notes |
|---|---|
| `host` / `port` / `timeout` | `timeout` is per read, not per answer — a long but progressing generation will not trip it |
| `model` | filled from `GET /v1/models`. The last model you actually ran becomes the default for every new Connectivity node, in any workflow |
| `thinking` | ON: the model reasons before answering, and the reasoning lands on Generate's `thinking` output instead of leaking into the prompt |
| `effort` | `low` · `medium` · `xhigh` — how much the model narrates its way to the answer. Not a token budget and not a quality dial |
| `temperature` `top_p` `top_k` `max_tokens` `seed` | standard sampling |
| `show_sampling` | hides the five sampling widgets so you cannot nudge them while dragging the node. Values survive either way |

Outputs one `ALLMA_CONNECTIVITY` link.

> **`max_tokens` is a shared budget.** Thinking and the answer spend the same
> pool. With thinking ON and a tight budget the model can spend all of it
> reasoning and return an empty answer — Generate reports that on its `status`
> output rather than failing.

> **`effort` has three levels because the model distinguishes three.** Templates
> in the wild accept `minimal`, `high`, `max`, `ultra` too, but they fold onto
> the same three — and an unrecognised value silently becomes `medium`.

---

### Allma Generate

Sends the prompt, waits, and hands the answer back into the graph.

**Inputs**

| Widget | Notes |
|---|---|
| `connectivity` | from Allma Connectivity |
| `preset` | picking one fills `system_prompt` below |
| `system_prompt` | the single source of truth for what the model is told |
| `user_prompt` | your brief |
| `enabled` | OFF: the LLM is skipped entirely and `user_prompt` passes straight through, so the graph still runs with the backend down |
| `use_image_metadata` | traces each connected image back to the file it came from and adds that file's prompt metadata |
| `read_lora_metadata` | ON: full LoRA sidecar. OFF: trigger words only |

**Optional links**

- `model` — a native `MODEL`. Connect it and the node reads the LoRAs applied
  upstream (see [LoRA awareness](#lora-awareness)).
- `image_1 … image_9` — grow on demand; only the next empty slot is shown.
- `audio_1 … audio_3` — needs a backend model that accepts audio.
- `duration` — seconds. Adds a target-duration block to the system prompt.

**Outputs**

| Slot | Notes |
|---|---|
| `output_prompt` | the answer. On any failure this carries the raw `user_prompt` instead, never an error message |
| `thinking` | the reasoning channel, empty when thinking is off |
| `assembled_system_prompt` | the exact system prompt that was sent — wire it to a text node to see what the model actually read |
| `status` | empty on a clean run; otherwise says what went wrong (truncation, backend down, no model selected) |

> Diagnostics never travel on `output_prompt`. A node downstream expecting a
> prompt gets a prompt, even when the run failed.

---

### Allma Preset Selector

Turns a preset name into its system prompt text, as a plain `STRING`.

Useful when you want to switch presets from outside the Generate node — through
a switch, from another subgraph, or to feed two Generate nodes the same prompt.

---

### Allma Live Text

A display node. Wire it to Generate's `thinking` or `output_prompt` and it fills
in **while the model is still writing**, instead of staying blank until the run
finishes.

It works out which output it is watching from its own link: slot 1 shows the
reasoning, slot 0 the answer. When the run ends, the authoritative value
replaces whatever was streamed.

---

### Allma Stop

Cancels whatever generation is in flight. The interrupt is global, so this does
not need to sit next to the Generate node it cancels — which is the point: a
Generate buried in a subgraph is not somewhere you can reach quickly mid-run.

The label reports what happened rather than pretending: `✅ stopped` when
something was actually streaming, `· nothing running` when there was not.

---

### Allma Bus In / Out

Many wires down one line. Plug anything into **Bus In** and a new slot appears;
**Bus Out** gives everything back in the same order, under the same names.

A slot's name is inherited from whatever is plugged into it, and can be changed —
open `▸ slot names` on the node, or use the Parameters panel. Renaming reaches
the receiving node as you type.

**Python carries values; the browser carries names.** No name travels in the
payload, which is what keeps a bus working from an API prompt where no frontend
ever named anything. It also means the names are free: renaming is not something
a generation can depend on.

> Collapsed, the name boxes are absent from the node *and* from the Parameters
> panel — Nodes 2.0 draws widgets from one live list and the panel reads the same
> list, so there is no "in the panel but off the node" state to offer. Expand to
> rename.

---

### Allma Load Image

The stock `Load Image` plus the prompt metadata baked into the file.

| Slot | Type | Notes |
|---|---|---|
| `image` | IMAGE | same as the built-in |
| `mask` | MASK | same as the built-in |
| `metadata` | STRING | source, model, positive/negative, LoRAs, sampler |

Reads **ComfyUI PNGs** (walks the embedded graph), **A1111 PNGs**
(`parameters` chunk) and **JPEG EXIF** (best effort). An unknown format returns
an empty string rather than raising.

> Generate does not need this node to see metadata — it traces images back
> through the graph on its own, the stock Load Image included. This one is for
> when you want the metadata *as text* in the graph.

---

### Clear Allma VRAM

Unloads the model from the card mid-graph, so a heavy image or video stage
downstream is not fighting the LLM for memory.

| Widget | Notes |
|---|---|
| `any` | anything at all, returned unchanged — this is what puts the node in the middle of a chain |
| `connectivity` | where the server is. Without it the node cannot ask what is loaded |
| `kill_orphans` | also terminates inference backends still holding memory after the model was unloaded |
| `wait_until_free` | hold the graph until the memory is really released, instead of racing the next node |
| `timeout` | seconds before giving up and letting the graph continue anyway |
| `enabled` | OFF: pure pass-through |

Outputs the `any` value untouched, plus a `status` string reporting how much was
actually freed per GPU.

> It is a pass-through on purpose: put it between two nodes and the graph
> ordering forces it to run at the right moment. A node with no output would
> run whenever ComfyUI felt like it.

---

### Combo Select (universal)

A dropdown that becomes a copy of whatever dropdown it is plugged into. Wire it
to a model loader and it lists models; to a sampler and it lists samplers.

It follows the link through switches and reroutes to find the real target, so it
still works in a graph built out of subgraphs.

---

### Allma Muter (false = mute)

Point it at the branches you want to switch off. Wire any output into a slot, a
toggle appears for it, and switching that toggle off mutes the node it points at
— exactly as `Ctrl+M` does — along with everything that feeds only that node.

Up to ten branches on one node. `Toggle All` sets them all at once; each can
still be changed on its own afterwards.

**Nothing passes through.** There are no outputs: the real wire still runs
straight from the source to whatever consumes it, and this node only *points at*
the branch. That also means it never executes — with nothing downstream it is
pruned before the graph runs, which is right for a control surface.

**Why muting rather than a value.** Bypassing or muting by hand changes the
*graph*, and the graph is frozen the moment you queue; a boolean changes a
*value*. Nothing built-in bridges the two — `ExecutionBlocker` kills the whole
consuming node rather than skipping one input, and `ComfySwitchNode` needs both
branches wired. Muting removes the nodes outright, so an output node downstream
of the same branch cannot drag it back in either.

**A toggle can be driven by a wire**, but only from a literal — a boolean
primitive, or a subgraph input promoted from one. The value is read in the
browser, before the graph is queued, because a value travelling on a link does
not exist until the graph is already running. A boolean some node *computes*
cannot be known in time, and the last clicked state is used instead.

> Nodes you muted by hand are never woken up again by the muter. Only the ones it
> put to sleep come back.

---

## LoRA awareness

Plug your `LoraLoader` (or `LoraLoaderModelOnly`) output into `Allma
Generate`'s `MODEL` slot. The package monkey-patches ComfyUI's LoRA loaders
to record each applied LoRA's file path and strength, then mines every
metadata source it can find:

1. **`lora_hints/<lora-file-stem>.md`** — *your* hand-written guidance, stored
   inside this plugin (not next to the safetensors). Highest authority: when
   present, it replaces the auto-extracted content entirely. Edit freely —
   files are re-read on every generation, no restart needed. Also accepts
   `<parent_dir>_<stem>.md` (to disambiguate) and `<stem>.txt`.
2. **`<name>.safetensors.rgthree-info.json`** — Civitai `trainedWords`
   fetched by rgthree. Most reliable trigger-word source.
3. **`<name>.metadata.json`** — [LoRA Manager](https://github.com/willmiao/ComfyUI-Lora-Manager)
   sidecar: `trigger_words`, `notes`, `usage_tips`, and the full Civitai
   model card (`modelDescription`), which is HTML-stripped and mined for
   prompt-format instructions.

What the LLM receives per LoRA: name, strength, trigger words, notes, usage
tips, and either your curated hints **or** auto-extracted format hints plus
the cleaned description. Precedence:

```
human_curated_hints  >  format_hints_extracted_from_description  >  usage_tips  >  notes  >  description
```

`read_lora_metadata` controls the depth:

- **ON** (default) — full metadata block (~1–5 kB per LoRA). The LLM can pick
  up structural requirements ("this LoRA wants step-by-step beats").
- **OFF** — trigger words only. LoRAs without trigger words are omitted.
  Trigger words are always injected when a `MODEL` is connected — they are
  literal tokens the LoRA needs to activate, and practically free.

### Writing a hint file

```
AllmaNodes/lora_hints/MyLora_v2.md
```

Free-form markdown/text — it's injected verbatim. Example:

```markdown
# MyLora v2 — prompt hints

Format: numbered action beats, present tense. Do NOT write a flowing paragraph.
This LoRA follows prompts literally; use precise motion verbs.
```

Hint files are gitignored — they stay local to your setup.

## Presets

JSON files under `presets/` shaped like
`{"system_prompt": "...", "notes": "..."}`. Managed from the node UI:

- `➕ new` — prompts for a name, saves the current `system_prompt`
- `💾 save` — overwrites the selected preset
- `🔄 reload` — re-reads from disk
- `🗑️ delete` — removes the selected preset

Selecting a preset fills the `system_prompt` widget (client-side only — the
widget is always the source of truth at execution time). If you have unsaved
edits and switch presets or reload, the UI asks before discarding them.

Recommended pattern: keep the preset **generic per model family** (LTX,
Z-Image, ...) and put **per-LoRA specifics** in `lora_hints/*.md`. The preset
just needs one rule saying LoRA guidance overrides its defaults; each LoRA's
requirements live next to the plugin, one file per LoRA.

## Image metadata continuity

When `use_image_metadata` is ON and you connect `image_N_meta`, the block is
appended to the system prompt as `"Image N metadata: ..."` so the model can do
things like *"same group of people, now on a beach"* — it sees the original
prompt/model/LoRAs behind the reference image and can replay them.

## Thinking mode

Both controls live on **Allma Connectivity**, because they describe the
conversation with the model rather than any single prompt — one switch covers
every Generate node on that backend.

`thinking` OFF sends `chat_template_kwargs.enable_thinking = false`, which
Qwen-style templates respect. ON lets the model reason, and the reasoning
arrives on Generate's dedicated `thinking` output — it never pollutes
`output_prompt`.

`effort` rides along as `chat_template_kwargs.reasoning_effort` and picks how
much the model narrates on the way to the answer: `low`, `medium`, `xhigh`.
It is not a token budget and it does not lower answer quality.

**Thinking and the answer share `max_tokens`.** With a tight budget the model
can spend the whole thing reasoning and return nothing — measured: at 96 tokens
with thinking ON, all 96 went to reasoning and the answer came back empty; with
thinking OFF the same question was answered correctly in 12. When that happens
the `status` output says so explicitly instead of leaving you with a blank slot.

Backends that ignore `chat_template_kwargs` will keep reasoning regardless of
the toggle. The quick way to tell: run the same prompt with thinking ON and OFF
— identical output means the field was dropped, and the reasoning has to be
controlled from the system prompt or the server's own flags instead.

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/OliveiraNickolas/AllmaNodes
```

Restart ComfyUI. No pip install needed — stdlib + torch/PIL only, which
ComfyUI already ships.

## Requires

- A running Allma (default `http://127.0.0.1:9000`) or any OpenAI-compatible
  endpoint
- For LoRA sniffing: sidecar metadata from LoRA Manager and/or rgthree, or
  your own `lora_hints/*.md` files
- For audio input (experimental): a backend model that accepts OpenAI
  `input_audio` content parts

## HTTP endpoints (used by the JS extension)

- `GET/POST/DELETE /allma/presets[/name]` — preset CRUD
- `GET /allma/state` — plugin state (`last_model`)

## License

MIT — see `LICENSE`.
