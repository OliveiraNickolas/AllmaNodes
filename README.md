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
| [Allma Bypasser (false = bypass)](#allma-bypasser-false--bypass) | `Allma/logic` | the same, stepping over a stage instead of removing it |
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

**Nothing passes through.** The real wire still runs straight from the source to
whatever consumes it, and this node only *points at* the branch. With none of
its outputs wired it never executes — it is pruned before the graph runs, which
is right for a control surface.

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

**`solo` — only this one.** The dropdown lists the branches that have a wire.
Pick a number and that branch goes on while every other goes off, which is the
thing you do constantly when comparing alternatives and which otherwise costs
one click per branch. Flip any toggle by hand afterwards and the dropdown falls
back to `none`, because the promise it makes — exactly one branch live — stopped
being true. It is a widget like any other, so it serializes with the workflow
and survives a reload.

**Switches for unwired branches are hidden with a flag, never removed.** Taking
a widget out of `node.widgets` makes ComfyUI prune the matching INPUT — and its
prune loop walks forward while splicing, so it eats every other one: a bypasser
with 25 toggles came back as `on_1, on_3, on_5 …`. Any promotion attached to a
casualty died with it, and every promoted widget on the parent slid one place
(the famous `model = 2`). Measured A/B on a damaged file: with removal, loading
gave 13 toggles; with `hidden = true`, 25 — and ComfyUI even puts back the ones
already lost. The node still collapses to the wired branches (726px → 94px in
the test); only the mechanism changed.

**`toggleRestriction` — how many branches may be on at once.** In the
Properties panel (right-click → Properties), the same control rgthree's Fast
Groups Muter has:

| valor | o que faz |
|---|---|
| `default` | nada é imposto, as chaves são livres |
| `max one` | ligar uma desliga todas as outras; desligar a última é permitido |
| `always one` | idem, e a última ligada se recusa a desligar |

Choosing a restriction while several branches are on keeps the first live one
and drops the rest; `Toggle All` under a restriction lands on the first branch
instead of turning everything on. It lives in `properties`, not in the widget
row, so adding it cannot shift `widgets_values` on workflows already saved —
the accident that broke every older muter when `solo` was inserted between the
master and the switches.

**The `fallback` output.** Leave it unwired and nothing changes: the node has no
consumer, so it is pruned before the graph runs and stays a pure control
surface. Wire it and the same switches become a fallback chain — branch 1 if it
is on, else branch 2, else branch 3 — so three ways of making the same image can
sit side by side with nothing to rewire. The slots are lazy and asked for one at
a time, so a branch that is switched off, or that comes after the one that
answered, never runs. Measured on three four-second branches: 4s, not 12s.
[Allma Fallback](#allma-fallback) does the same on its own, for when the choice
has nothing to do with muting.

**One BOOLEAN output per switch.** Each switch has an output carrying its state
(`True` = on), named like the switch — rename the switch and the output follows.
On the canvas it sits on the switch's own row, at the right edge; in Nodes 2.0
the outputs are listed on the right. Only the wired branches get one (plus any
output that already has a wire), and `fallback` stays output 0, so workflows
wired before keep their links. Wiring only these booleans never makes a branch
run: the node answers them without asking for any branch value.

**Show / Hide.** Right-click → **Show / Hide** (also in the Properties panel)
hides `Toggle All`, `Solo`, the branch switches or the switch outputs. Hidden
switches keep working — `Toggle All` alone can drive them — and their outputs
go with them, except one that is already wired. The node takes exactly the
height its rows need, when created and when an option changes, and keeps the
size it was saved with on reload.

**`solo` shows the names.** The dropdown lists each branch by its switch's name
(`Turbo LoRa`, `LoRa 1`…); the value underneath stays the number, which is what
the backend validates.

**Wireless button in Nodes 2.0.** The title-bar wifi button is drawn on the
canvas; Nodes 2.0 draws the title in HTML, so there the same button is placed in
the node's header.

---

### Allma Bypasser (false = bypass)

Identical to the muter — same slots, same master, same wiring — except a branch
switched off is **bypassed** rather than muted.

The difference is what happens to the chain. Muting takes the node out of the
graph, so whatever it fed sees an unconnected input: right for a branch that
should simply not be there, like a reference image you are leaving out of this
run. Bypassing leaves the node in place and passes its input straight to its
output, so the chain stays whole: right for a stage you are stepping over, like
an upscaler in the middle of a pipeline that still has to hand its frames on.

Everything else in the muter's section applies here unchanged.

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

## Only what the branch is wired to

A branch switches off the node it points at. Nothing upstream of it, ever.

It used to sweep the target's private ancestors as well, to save the work of a
loader feeding a muted node. Two things killed that. Switch every branch off and
the sweep has nothing live left to stop it, so it walks up and paints the whole
graph — sampler, scheduler, upscaler and all, which is what a full bypass looked
like on screen. And the saving was imaginary: ComfyUI runs only what reaches an
output, so a node a mute orphans never executes anyway.

The whole node is still decided before anything is applied, because the same
node can be wired to two branches and a chain is the normal wiring here (six
Load LoRA in series, each feeding the next and its own branch). Off wins over
on; applying branch by branch let a later ON branch switch an earlier OFF
branch's target back on, so the switch read "bypassed" while the node kept
running.

## Allma Fallback

Many inputs in, the first one that actually arrived out — slot 1, else slot 2,
else slot 3. Slots grow as you fill them, like Allma Bus In, and anything plugs
into anything.

What it pairs with is the point: a branch switched off by Allma Muter is removed
from the prompt, so its wire never arrives and the switch falls through. Three
ways of producing the same image can sit side by side and whichever is left
running comes out, with nothing to rewire.

Only `None` is skipped. An empty string, a zero and an all-black image are
values someone chose to send, and the value is never tested for truthiness — a
tensor has none. With every slot empty it raises instead of passing nothing on.

The slots are **lazy**, asked for one at a time: an alternative that is never
chosen is never produced. Wire three upscalers into it and only the one that
comes out ever runs. (ComfyUI needed a small patch for that to be true of any
node with growing slots — see [Lazy on a slot that
grew](#lazy-on-a-slot-that-grew).)

## Slot order: ComfyUI shuffles it, and how to put it back

Grown slots are appended after the declared ones, so a family gets cut in half:
a bus comes back as IMAGE 1, IMAGE 4 … IMAGE 9, bus, Width, Height, slot_13,
slot_14, and only then IMAGE 2 and IMAGE 3. `MiniMaxH3ReferenceToVideo` does it
too — after a refresh its ref_image_3..8 sit below ref_video_0 and ref_audio_0.
Nothing about the node causes it.

`tidyDynamicSlots(node)` in `web/allma_slotfix.js` puts a node's slots back in
order — families in the order they first appear, numbers ascending inside each.
It runs automatically on workflow load (if enabled in settings), and can be
run manually on any node: right-click and pick "Fix slot order (Allma)".
It works on third-party nodes too. (It never touches Bus nodes or connection
changes, preserving custom bus labels and avoiding racing Autogrow compaction).

Two rules it was rewritten to obey, both learned by breaking a real workflow:

- **A wire belongs to the INDEX, not to the slot object.** `input.link` is
  derived (`linkIdOf(this)`, and its setter only accepts null), so moving slot
  objects renames positions while the wires stay where they were.
- **Move a wire only with `connect()`.** Writing `target_slot` by hand is
  refused by this frontend — "Failed to update link endpoints" in the console,
  and two wires gone from an AllmaGenerate. The tidy now disconnects and
  reconnects each wire to its slot by name, then verifies every one of them
  against a snapshot and reports the node if anything did not come back.

The old automatic pass that sorted by name is gone for good: Autogrow renames
slots when it compacts, so sorting raced it and dropped wires onto slots that
had just been renamed.

## Muter and Bypasser reach through the bus

A muter wired to Allma Bus Out is pointing at a junction: switching it off there
would take out every slot travelling on that bus, not the one branch the switch
names. So it does not stop at the bus — it steps across to the node that fills
that slot on Allma Bus In and switches off THAT one, leaving the bus and every
other slot alone.

Bus Out's output *k* is Bus In's slot *k+1*, which is how the mirrored names are
built, so the hop is exact. Buses that feed buses are followed to the far end. A
branch pointing at an empty slot switches off nothing at all — there is nothing
behind it, and muting the bus in its place is precisely the accident this avoids.

## Slots that grow, and the wires that slide off them

A link is stored by INDEX — "target_slot 27" — and that only survives a reload
if the input array is rebuilt exactly as it was saved. On a node whose inputs
grow it is not: the schema lays out `value_1`, then `on_1..on_25`, and Autogrow
appends `value_2`, `value_3` … after the toggles. Loading a workflow whose muter
sits in a subgraph drops one link and slides the rest up a slot, so every toggle
drives its neighbour. It is not particular to this pack — `MiniMaxH3ReferenceToVideo`
loses its `ref_image` wires the same way when it is grouped into a subgraph.

`web/allma_slotfix.js` repairs this for the whole workflow. Before the graph is
built it reads, from the file, the NAME of the slot each link was saved on; once
everything is up it compares that with where the wires landed and puts back the
ones that moved, using the ordinary connect API, then removes the duplicate the
load invented. It is registered once and walks every node of every type, so
**a node added to this pack later is covered without being listed anywhere**,
and so is a node from another pack.

Two things it learned the hard way, both from real files:

- **The plan comes from each node's own `inputs` array, never from the links
  array's `target_slot`.** The H3 Fun ControlNet examples ship hand-built, with
  every link written as `target_slot: 0`; loading them by index piles four wires
  onto slot 0 and drops the rest — 24 links in the file, 11 on the canvas. The
  `inputs` array is the one place every kind of file agrees.
- **The root graph is keyed by the word "root", not by its id.** A saved
  workflow carries `00000000-0000-0000-0000-000000000000` for the root and is
  given a fresh uuid on load, so keying by id worked inside subgraphs and
  silently skipped every node in the main graph.

It is conservative by design: a slot the file names but that no longer exists is
left alone (renamed inputs stay the loader's business), a node whose wiring
already matches is never touched, and only a node that actually had a wire moved
gets its strays cleaned.

### The same damage with no file involved

Grouping nodes into a subgraph breaks the wires the same way, and there the plan
above is empty — nothing is being loaded. Watched live: three sources wired into
a bypasser's `value_1..value_3`, then "Convert to Subgraph", and inside the new
subgraph the wires sit on `value_1`, `enabled` and `solo`. The links kept
`target_slot` 0,1,2 while the rebuilt node put its grown slots at the end.

The rebuild goes through `node.configure`, and what configure is handed is still
correct: it names every slot and the link id on it. So the same idea runs one
step earlier — remember what configure was told, and 150 ms later, once the
links exist, check where each one landed and move the ones that are wrong. It
hooks `LGraphNode.prototype`, so grouping, ungrouping, pasting and undo are all
covered, for every node type, ours or anyone's. While a workflow is loading it
stands down: the file's own plan is better, and `afterConfigureGraph` already
uses it.

Only wires whose link id was recorded are ever removed, and only from a slot the
record disagrees with — anything wired *after* configure (a subgraph's input
proxy, say) is none of its business.

Two rules for anything written here from now on:

- **Address a slot by name, never by index.** `node.inputs[3]` is not a promise;
  `node.inputs.find(i => i.name === "values.value_3")` is. The same goes for
  pairing widgets with slots — match `on_3` to `values.value_3` by their number,
  not by their position in the array.
- **Need to react to a repair?** `onRepair(fn)` from `allma_slotfix.js` fires
  after the sweep, and `repairNode(node)` fixes a single node on demand. The
  muter uses both to re-apply its mutes once the branches are back in place.

## Lazy on a slot that grew

ComfyUI reads an input's flags in two places, and only one of them knows about
dynamic inputs. `execution.get_input_data` expands the schema against the node's
actual inputs first, so `values.value_3` is found with all its flags. But
`TopologicalSort.get_input_info` — the one the **scheduler** uses to decide what
has to run — calls `INPUT_TYPES()` raw, which in a V3 node returns the template
rather than the grown slots. There is no `values.value_3` in it, `lazy` reads as
false, and every wired branch is scheduled as a hard dependency.

The effect is quiet: `check_lazy_status` is still called and still answers, but
by then everything it might have skipped has already run. Three four-second
branches with one switched off took 12.3 s where laziness gives 4.

`api/lazy_dynamic.py` wraps that one method to expand the schema the same way
the executor does, for V3 nodes only, cached per execution. Static inputs are
untouched — they were always found by name — and a node that does not ask for
`lazy` keeps running eagerly (Allma Bus still evaluates all its slots, as it
should). If the patch cannot be installed it prints and gives up: the slots go
back to being eager, which is how ComfyUI behaves without it.

One detail worth knowing if you write a `check_lazy_status` for a dynamic
input: there, and only there, each slot arrives as `(value, key)` — ComfyUI sets
`create_dynamic_tuple` so the slot can tell you the flat name the executor knows
it by, `values.value_3`. Hand that name back instead of composing it yourself.

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
