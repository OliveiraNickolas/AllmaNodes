# ComfyUI-Allma

Custom nodes for wiring [ComfyUI](https://github.com/comfyanonymous/ComfyUI) to the [Allma](https://github.com/nickolasarthur/allma) LLM backend (or any OpenAI-compatible endpoint).

One purpose: send a prompt (plus reference images, image metadata, and the
LoRAs active in your workflow) to a local LLM and get engineered prompt text
back into your graph — with the LLM fully aware of what your workflow is doing.

## Nodes

### `Allma Connectivity`
Says *how* to reach the backend and *how* to sample.

- `host` / `port` / `timeout`
- `model` — dropdown fed by `GET /v1/models`. **The last model you actually
  ran becomes the default** for every new Connectivity node you add, in any
  workflow (persisted in `state.json`).
- `temperature` / `top_p` / `top_k` / `max_tokens` / `seed`
- `show_sampling` — toggle that hides/shows the five sampling widgets so you
  don't nudge them by accident. Values are preserved either way.

Outputs an `ALLMA_CONNECTIVITY` slot that plugs into `Allma Generate`. One
Connectivity node can feed several Generate nodes.

### `Allma Load Image`
Drop-in replacement for the stock `Load Image` that *also* extracts the
image's embedded prompt metadata as a `STRING`.

| Slot     | Type   | Notes                                                        |
|----------|--------|--------------------------------------------------------------|
| image    | IMAGE  | identical to the built-in `Load Image`                       |
| mask     | MASK   | idem                                                         |
| metadata | STRING | formatted block: source, model, positive/negative, LoRAs, sampler |

Understands **ComfyUI PNGs** (walks the embedded workflow graph), **A1111
PNGs** (`parameters` chunk), **JPEG EXIF** (best effort). Unknown formats
return an empty string — nothing breaks downstream.

### `Allma Generate`
The node that actually calls the LLM.

Required inputs:

| Widget              | Notes                                                          |
|---------------------|----------------------------------------------------------------|
| connectivity        | from `Allma Connectivity`                                      |
| preset              | dropdown; selecting one fills `system_prompt` (client-side)    |
| system_prompt       | the single source of truth for the system prompt               |
| user_prompt         | your actual brief                                              |
| use_image_metadata  | inject connected `image_N_meta` blocks into the system prompt  |
| thinking            | ON: model reasons first; reasoning goes to the `thinking` output |
| read_lora_metadata  | ON: inject full LoRA metadata; OFF: trigger words only (see below) |

Optional inputs: `model` (native `MODEL` — enables LoRA sniffing),
`image_1..3` + `image_1..3_meta`, `audio` (**experimental** — needs an
audio-input-capable backend model; text/vision models reject it).

Outputs:

| Slot                    | Notes                                                    |
|-------------------------|----------------------------------------------------------|
| response                | the model's answer (your engineered prompt)              |
| thinking                | the reasoning channel when `thinking` is ON, else empty  |
| assembled_system_prompt | the exact system prompt sent to the backend — wire it to a Show Text node to debug what the LLM saw |

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
ComfyUI-Allma/lora_hints/MyLora_v2.md
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

`thinking` OFF asks the chat template to skip the `<think>` block
(`chat_template_kwargs.enable_thinking=false` — Qwen-style models respect
this). ON lets the model reason; the reasoning arrives on the dedicated
`thinking` output and never pollutes `response`. If `max_tokens` is exhausted
mid-thinking, the response slot carries an explicit warning instead of
silently returning nothing.

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/nickolasarthur/ComfyUI-Allma
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
