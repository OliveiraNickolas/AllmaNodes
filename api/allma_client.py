"""HTTP client for talking to the Allma backend (OpenAI-compatible).

Uses only stdlib (urllib + json + base64) so we don't ship extra deps.
Handles 503 "loading" transparently by retrying with a short backoff.
"""
import base64
import io
import json
import time
import urllib.error
import urllib.request
import wave

from .interrupt import clear_response, is_cancelled, register_response

LOG = "[AllmaNodes]"


class Cancelled(Exception):
    """Raised internally when the stop button fires. Carries partial output."""

    def __init__(self, content: str = "", thinking: str = ""):
        super().__init__("generation interrupted")
        self.content = content
        self.thinking = thinking


def _url(host: str, port: int, path: str) -> str:
    return f"http://{host}:{port}{path}"


def list_models(host: str, port: int, timeout: float = 5.0) -> list[str]:
    """Return the list of model IDs the allma exposes, or [] if unreachable."""
    try:
        req = urllib.request.Request(
            _url(host, port, "/v1/models"),
            headers={"Authorization": "Bearer dummy"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        return [m["id"] for m in data.get("data", []) if "id" in m]
    except Exception as e:
        print(f"{LOG} list_models failed: {e}")
        return []


def image_tensor_to_data_url(tensor) -> str:
    """ComfyUI IMAGE tensor (1, H, W, C) in [0,1] → data:image/png;base64,..."""
    import numpy as np
    from PIL import Image

    if tensor is None:
        return ""
    if hasattr(tensor, "detach"):
        arr = tensor.detach().cpu().numpy()
    else:
        arr = np.asarray(tensor)
    if arr.ndim == 4:
        arr = arr[0]
    arr = (arr.clip(0.0, 1.0) * 255.0).round().astype("uint8")
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def frames_to_video_data_url(frames, fps: float = 24.0, send_fps: float = 8.0,
                             max_edge: int = 1280) -> str:
    """ComfyUI IMAGE batch (N, H, W, C) in [0,1] → data:video/mp4;base64,...

    A ComfyUI video is just a stack of frames with no rate attached, so `fps` is
    an assumption: 24, which is MiniMax H3's native rate and what the H3
    workflows run at. It only has to be right for the model's sense of timing.

    Frames are thinned to `send_fps` before encoding, with the output rate set
    to match, so the clip keeps its real duration. vLLM's Qwen3-VL processor
    samples video at 2 fps regardless — measured: a 15 s clip came back as
    exactly one reading per second — so 8 fps is still 4× what it will look at,
    and the payload drops to about a third of the full-rate one (a 15 s 720p
    clip was 13.6 MB of base64 at 24 fps).

    H.264 in yuv420p needs even dimensions; scale=-2 keeps the aspect ratio and
    rounds for us.
    """
    import os
    import subprocess
    import tempfile

    import numpy as np

    if frames is None:
        return ""
    arr = frames.detach().cpu().numpy() if hasattr(frames, "detach") else np.asarray(frames)
    if arr.ndim == 3:
        arr = arr[None]
    if arr.ndim != 4 or arr.shape[0] == 0:
        return ""

    step = max(1, int(round(fps / send_fps))) if send_fps and fps > send_fps else 1
    arr = arr[::step]
    out_fps = fps / step
    n, h, w, _c = arr.shape
    rgb = (arr[..., :3].clip(0.0, 1.0) * 255.0).round().astype("uint8")

    scale = f"scale='if(gte(iw,ih),min({max_edge},iw),-2)':'if(gte(iw,ih),-2,min({max_edge},ih))'"
    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
               "-r", f"{out_fps:.6f}", "-i", "-",
               "-vf", scale, "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-crf", "23", "-preset", "veryfast", "-movflags", "+faststart", path]
        r = subprocess.run(cmd, input=rgb.tobytes(), capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {r.stderr.decode(errors='replace')[:300]}")
        data = open(path, "rb").read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return "data:video/mp4;base64," + base64.b64encode(data).decode()


def audio_dict_to_wav_b64(audio: dict) -> tuple[str, str]:
    """ComfyUI AUDIO ({"waveform": tensor, "sample_rate": int}) → (base64_data, "wav").

    Returns ("", "") if audio is empty.
    """
    if not audio:
        return "", ""
    wf = audio.get("waveform")
    sr = int(audio.get("sample_rate", 16000))
    if wf is None:
        return "", ""
    if hasattr(wf, "detach"):
        arr = wf.detach().cpu().numpy()
    else:
        import numpy as np
        arr = np.asarray(wf)
    if arr.ndim == 3:
        arr = arr[0]
    if arr.ndim == 2 and arr.shape[0] < arr.shape[1]:
        arr = arr[0]
    elif arr.ndim == 2:
        arr = arr.mean(axis=1)
    import numpy as np
    pcm = (arr.clip(-1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return base64.b64encode(buf.getvalue()).decode(), "wav"


def build_user_content(
    text: str,
    image_data_urls: list[str],
    audio_b64: str,
    audio_format: str,
    video_data_url: str = "",
) -> list[dict] | str:
    """Assemble OpenAI-style multimodal content array. Returns a plain string when
    there are no attachments (some backends prefer the simpler form).

    Each image is preceded by a short text part. This is not cosmetic: with
    llama.cpp b10433 and a Qwen3-VL mmproj, ADJACENT image parts collapse in
    pairs — send four and the model receives two, send six and it receives
    three, silently. Measured by sending numbered colour swatches and asking
    the model to name them back: consecutive images returned every other one,
    while the same images separated by any text part all arrived.

    The label doubles as the numbering the prompts rely on, so "Image 3" in a
    system prompt now points at the picture the model actually saw.
    """
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    for idx, url in enumerate(image_data_urls, start=1):
        if url:
            parts.append({"type": "text", "text": f"Image {idx}:"})
            parts.append({"type": "image_url", "image_url": {"url": url}})
    # One video, labelled the same way the images are so "Video 1" in a Ref2VA
    # prompt points at the clip the model actually watched. vLLM takes it as a
    # video_url part; the profile caps it at one per prompt.
    if video_data_url:
        parts.append({"type": "text", "text": "Video 1:"})
        parts.append({"type": "video_url", "video_url": {"url": video_data_url}})
    if audio_b64:
        parts.append(
            {"type": "input_audio", "input_audio": {"data": audio_b64, "format": audio_format}}
        )
    if len(parts) == 0:
        return ""
    if len(parts) == 1 and parts[0]["type"] == "text":
        return parts[0]["text"]
    return parts


def _consume_stream(r, relay=None) -> tuple[str, str, str]:
    """Read an OpenAI-style SSE stream → (content, thinking, finish_reason).

    Checks the cancel token between chunks. On stop, raises Cancelled carrying
    whatever was produced so far, so the node can still return partial text.

    `relay`, when given, receives each piece as it arrives so the UI can show
    the reasoning live instead of only after the answer lands.
    """
    content_parts: list[str] = []
    think_parts: list[str] = []
    finish_reason = ""
    saw_done = False

    def _partial() -> tuple[str, str]:
        return "".join(content_parts), "".join(think_parts)

    try:
        for raw in r:
            if is_cancelled():
                c, t = _partial()
                raise Cancelled(c, t)
            line = raw.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                saw_done = True
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                content_parts.append(piece)
                if relay is not None:
                    relay.add(piece, "content")
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                think_parts.append(reasoning)
                if relay is not None:
                    relay.add(reasoning, "reasoning")
            if choice.get("finish_reason"):
                finish_reason = str(choice["finish_reason"]).lower()
    except Cancelled:
        raise
    except Exception:
        # A read that blows up right after the stop button is the socket being
        # closed under us on purpose — treat it as a clean cancel, not an error.
        if is_cancelled():
            c, t = _partial()
            raise Cancelled(c, t) from None
        raise

    if is_cancelled():
        c, t = _partial()
        raise Cancelled(c, t)

    # A stream that just stops — no [DONE], no finish_reason — means the socket
    # closed mid-answer. Iterating the response yields nothing more and the loop
    # exits silently, so without this the node would hand a half-written prompt
    # downstream as though the model had finished speaking.
    if not saw_done and not finish_reason:
        finish_reason = "incomplete"

    return "".join(content_parts), "".join(think_parts), finish_reason


def chat_completion(
    host: str,
    port: int,
    timeout: float,
    model: str,
    system_prompt: str,
    user_content,
    temperature: float = 1.0,
    top_p: float = 0.95,
    top_k: int = 20,
    max_tokens: int = 2048,
    seed: int | None = None,
    enable_thinking: bool = False,
    reasoning_effort: str = "",
    retry_on_loading: bool = True,
    max_retries: int = 40,
    relay=None,
) -> tuple[str, str, str]:
    """POST /v1/chat/completions. Returns (content, thinking, status).

    `status` is a human-readable note about anything that went wrong (e.g. the
    answer was truncated). It is deliberately kept OUT of `content` so callers
    never emit a diagnostic where a prompt is expected — empty means clean run.

    When enable_thinking is False, we ask the chat template to skip the
    <think>...</think> block via chat_template_kwargs; Qwen3-style models
    respect this. The thinking channel is returned separately so callers can
    surface it in a dedicated output slot without polluting the main response.

    On 503 "loading model" we back off and retry — allma may be starting a
    fresh backend. Raises RuntimeError on unrecoverable errors.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_content})

    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": float(temperature),
        "top_p": float(top_p),
        "max_tokens": int(max_tokens),
        # Streaming is what makes the stop button possible: we get control back
        # between chunks. It also turns `timeout` into an inactivity timeout
        # (per socket read) instead of a deadline for the whole answer, so a
        # long-but-progressing generation no longer dies at the 120s mark.
        "stream": True,
    }
    if top_k > 0:
        body["top_k"] = int(top_k)
    if seed is not None and seed >= 0:
        body["seed"] = int(seed)
    # Both ride in chat_template_kwargs, which is what the model's own chat
    # template reads. Allma's ownership rule: a .allm profile declaring the same
    # directive overrides whatever we send, and a silent profile lets it through
    # — so sending nothing is a real choice, not a missing value.
    ctk: dict = {}
    if not enable_thinking:
        ctk["enable_thinking"] = False
    if reasoning_effort:
        ctk["reasoning_effort"] = reasoning_effort
    if ctk:
        body["chat_template_kwargs"] = ctk

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        _url(host, port, "/v1/chat/completions"),
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer dummy",
        },
        method="POST",
    )

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                register_response(r)
                try:
                    content, thinking, finish_reason = _consume_stream(r, relay)
                finally:
                    clear_response()
            content = content.strip()
            thinking = thinking.strip()
            if not content and thinking:
                if "</think>" in thinking:
                    parts = thinking.rsplit("</think>", 1)
                    thinking, tail = parts[0], parts[1].strip()
                    if tail:
                        content = tail
                        thinking = thinking.replace("<think>", "", 1).strip()
            # Logged on every run: when an answer comes back clipped, the split
            # between the two channels and the finish_reason are what tell a
            # real truncation apart from text that merely landed in 'thinking'.
            print(
                f"{LOG} finish_reason={finish_reason or 'none'} "
                f"content={len(content)} chars  reasoning={len(thinking)} chars"
            )

            status = ""
            if finish_reason == "incomplete":
                status = (
                    "the backend closed the stream mid-answer — no completion "
                    "signal arrived, so the text below stops wherever it stopped. "
                    "Usually the server dropped the connection (idle keep-alive, "
                    "a crash, or a model reload). Re-run; if it repeats, check the "
                    "Allma logs at the moment it cuts."
                )
                print(f"{LOG} ⚠ {status}")
            elif finish_reason == "length":
                if not content and thinking:
                    status = (
                        f"response cut off — max_tokens={max_tokens} was exhausted "
                        f"mid-thinking, so no answer was ever produced. Fix: turn "
                        f"'thinking' OFF, or raise max_tokens to 4096+, or reset "
                        f"sampling to Qwen3 official thinking-mode "
                        f"(temperature=1.0, top_p=0.95, top_k=20)."
                    )
                else:
                    status = (
                        f"response truncated — max_tokens={max_tokens} was reached. "
                        f"The text below is incomplete."
                    )
                print(f"{LOG} ⚠ {status}")
            return content, thinking, status
        except Cancelled:
            # Deliberate stop — must not be retried nor wrapped as a failure.
            raise
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code == 503 and retry_on_loading and "loading" in body_text.lower():
                sleep_for = min(2 + attempt, 10)
                print(f"{LOG} backend loading — retry in {sleep_for}s ({attempt + 1}/{max_retries})")
                time.sleep(sleep_for)
                last_err = e
                continue
            raise RuntimeError(f"HTTP {e.code}: {body_text or e.reason}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Connection failed: {e.reason}") from e
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}") from e

    raise RuntimeError(
        f"Backend never became ready after {max_retries} retries: {last_err}"
    )
    # Unreachable; keeps type checkers happy about the return type.
    return "", "", ""
