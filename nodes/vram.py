"""AllmaClearVRAM — free the GPU that the Allma backend is occupying.

AllmaGenerate holds no VRAM of its own: it talks HTTP to the Allma server,
which runs the model in a separate process. ComfyUI's own cache-clearing nodes
cannot touch that memory — only the owning process can release it.

Two stages, because one is not enough in practice:

  1. Ask Allma to unload what it tracks (/v1/ps -> /v1/unload).
  2. Terminate backend processes still holding VRAM. Allma loses track of a
     server that outlived its bookkeeping — a manual launch, a reload, a
     crash — and such an orphan can sit on 20+ GB that nothing else frees.

Stage 2 only ever signals processes whose command line names a known inference
backend, and never the ComfyUI process running this node.
"""
import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request

from comfy_api.latest import io

from .connectivity import AllmaConnectivityType

LOG = "[ComfyUI-Allma/vram]"

# A process is only ever signalled if its command line contains one of these.
# Anything else on the GPU — ComfyUI included — is left strictly alone.
BACKEND_HINTS = ("llama-server", "llama_server", "vllm", "sglang")

AnyType = io.Custom("*")


def _req(url: str, payload: dict | None = None, timeout: float = 60.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer dummy"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _loaded(base: str, timeout: float = 10.0) -> list[dict]:
    """Servers Allma currently tracks. /v1/ps is the running-process list;
    /v1/models is only the config catalogue."""
    try:
        return _req(f"{base}/v1/ps", timeout=timeout).get("servers") or []
    except Exception as e:
        print(f"{LOG} could not read /v1/ps: {e}")
        return []


def _nvidia(args: list[str]) -> list[str]:
    try:
        out = subprocess.run(["nvidia-smi", *args], capture_output=True,
                             text=True, timeout=20)
        return [l for l in out.stdout.strip().splitlines() if l.strip()]
    except Exception as e:
        print(f"{LOG} nvidia-smi failed: {e}")
        return []


def _cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except Exception:
        return ""


def _gpu_procs() -> list[dict]:
    """Every process holding VRAM, with its GPU index and usage in MiB."""
    idx_by_uuid = {}
    for line in _nvidia(["--query-gpu=index,uuid", "--format=csv,noheader"]):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2:
            idx_by_uuid[parts[1]] = parts[0]

    procs = []
    for line in _nvidia(["--query-compute-apps=pid,used_memory,gpu_uuid",
                         "--format=csv,noheader,nounits"]):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[0])
            mem = int(parts[1])
        except ValueError:
            continue
        procs.append({
            "pid": pid, "mem": mem,
            "gpu": idx_by_uuid.get(parts[2], "?"),
            "cmd": _cmdline(pid),
        })
    return procs


def _backend_orphans() -> list[dict]:
    """GPU processes that are inference backends and are not this process."""
    me = os.getpid()
    out = []
    for p in _gpu_procs():
        if p["pid"] == me:
            continue
        low = p["cmd"].lower()
        if any(h in low for h in BACKEND_HINTS):
            out.append(p)
    return out


def _terminate(pid: int, grace: float = 15.0) -> bool:
    """SIGTERM, then SIGKILL if it will not go. True when the pid is gone."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        print(f"{LOG} not allowed to signal pid {pid}")
        return False

    deadline = time.time() + grace
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.5)

    try:
        os.kill(pid, signal.SIGKILL)
        time.sleep(1.0)
    except ProcessLookupError:
        return True
    except Exception as e:
        print(f"{LOG} SIGKILL on {pid} failed: {e}")
        return False
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True


class AllmaClearVRAM(io.ComfyNode):
    """Free the GPU memory held by the Allma backend."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaClearVRAM",
            display_name="Clear Allma VRAM",
            category="Allma/utils",
            description=(
                "Unloads the Allma backend and frees its GPU, so the card can be "
                "used by ComfyUI. Pass-through: wire it anywhere and it fires at "
                "that point in the run."
            ),
            inputs=[
                AnyType.Input(
                    "any",
                    tooltip="Anything at all — returned unchanged. This is the "
                    "trigger: the cleanup happens when this value is needed.",
                ),
                AllmaConnectivityType.Input(
                    "connectivity", optional=True,
                    tooltip="Where the Allma server lives. Without it, "
                    "127.0.0.1:9000 is assumed.",
                ),
                io.Boolean.Input(
                    "kill_orphans", optional=True, default=True,
                    tooltip="Also terminate inference-backend processes still "
                    "holding VRAM after the unload — the ones Allma has lost track "
                    "of. Only processes whose command line names a known backend "
                    "(llama-server, vllm, sglang) are ever signalled; ComfyUI is "
                    "never touched. OFF: only the polite unload is attempted.",
                ),
                io.Boolean.Input(
                    "wait_until_free", optional=True, default=True,
                    tooltip="Hold the graph until the memory is actually released, "
                    "so the next node does not start loading into VRAM that is "
                    "still occupied.",
                ),
                io.Int.Input(
                    "timeout", optional=True, default=90, min=5, max=600,
                    tooltip="Seconds to wait before giving up and letting the graph "
                    "continue anyway.",
                ),
                io.Boolean.Input(
                    "enabled", optional=True, default=True,
                    tooltip="OFF: pure pass-through, nothing is freed. Lets you keep "
                    "the node wired while iterating.",
                ),
            ],
            outputs=[
                AnyType.Output(display_name="any"),
                io.String.Output(display_name="status"),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs):
        # Always run: this is a side effect, and a cached "result" would mean
        # the memory silently stays allocated.
        return float("nan")

    @classmethod
    def execute(cls, any, connectivity=None, kill_orphans=True,
                wait_until_free=True, timeout=90, enabled=True) -> io.NodeOutput:
        if not enabled:
            return io.NodeOutput(any, "")

        host, port = "127.0.0.1", 9000
        if isinstance(connectivity, dict):
            host = connectivity.get("host") or host
            port = int(connectivity.get("port") or port)
        base = f"http://{host}:{port}"

        before = {p["gpu"]: 0 for p in _gpu_procs()}
        for p in _gpu_procs():
            before[p["gpu"]] = before.get(p["gpu"], 0) + p["mem"]

        notes: list[str] = []

        # ── 1. the polite path ────────────────────────────────────────────
        servers = _loaded(base)
        for name in [s.get("name") for s in servers if s.get("name")]:
            try:
                res = _req(f"{base}/v1/unload", {"model": name}, timeout=timeout)
                if isinstance(res, dict) and res.get("error"):
                    notes.append(f"unload {name}: {res['error']}")
                else:
                    notes.append(f"unloaded {name}")
            except Exception as e:
                notes.append(f"unload {name} failed: {e}")

        if servers and wait_until_free:
            deadline = time.time() + timeout
            while time.time() < deadline and _loaded(base, timeout=5.0):
                time.sleep(1.0)

        # ── 2. whatever the unload could not reach ────────────────────────
        if kill_orphans:
            for p in _backend_orphans():
                gone = _terminate(p["pid"], grace=min(15.0, timeout))
                label = f"pid {p['pid']} on GPU {p['gpu']} ({p['mem']} MiB)"
                notes.append(f"killed {label}" if gone else f"could not kill {label}")

        if wait_until_free:
            deadline = time.time() + timeout
            while time.time() < deadline and _backend_orphans():
                time.sleep(1.0)

        after: dict = {}
        for p in _gpu_procs():
            after[p["gpu"]] = after.get(p["gpu"], 0) + p["mem"]
        freed = {
            g: before.get(g, 0) - after.get(g, 0)
            for g in set(before) | set(after)
            if before.get(g, 0) - after.get(g, 0) > 0
        }
        if freed:
            notes.append("freed " + ", ".join(
                f"{mb} MiB on GPU {g}" for g, mb in sorted(freed.items())))
        elif not notes:
            notes.append("nothing was loaded")

        status = " | ".join(notes)
        print(f"{LOG} {status}")
        return io.NodeOutput(any, status)
