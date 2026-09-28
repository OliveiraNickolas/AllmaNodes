"""AllmaSaveImage — write images to a folder you choose, not just output/.

ComfyUI's SaveImage is deliberately fenced in: it resolves every path through
folder_paths.get_save_image_path against the output directory, which rejects
anything that climbs out of it. That fence is right for a server other people
reach, and it is in the way on a workstation where the pictures belong in a
project folder, on another disk, or beside the files they were made from.

It draws the same two things SaveImage does and nothing more. PNG and the
workflow metadata are not offered as choices here for the same reason they are
not offered there: they are what the node does, and --disable-metadata is
already the switch for the one case that wants them gone. A format picker was
tried and removed — a node whose whole point is the destination should not be
busier than the one it stands in for.

It keeps the %date% and %node.widget% substitutions and the prompt and
extra_pnginfo blobs, and differs in two ways beyond the destination:

  Names carry the time, not a sequence. SaveImage counts _00001_, _00002_; this
  writes <prefix>_DDMMYYYY_HHMMSS, so a folder sorts by when things were made
  and every name says when without opening anything. Seconds are not unique on
  their own, so a batch gets an index and a genuine clash walks _1, _2 until the
  name is free. Nothing is ever overwritten.

  The thumbnail follows the destination. /view serves only output/, input/ and
  temp/, refusing absolute paths outright, so a file written elsewhere has no
  URL. Inside output/ the node points at the real file; anywhere else it writes
  a small copy to temp/ purely to have something to show — which is exactly what
  ComfyUI's own Preview Image node does, and temp/ cleans itself up.
"""
import json
import os
import re
from datetime import datetime

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import folder_paths
from comfy.cli_args import args
from comfy_api.latest import io, ui as UI

LOG = "[AllmaNodes/save]"


def _expand_tokens(text: str) -> str:
    """%date:yyyy-MM-dd% and friends, the same spelling SaveImage accepts.

    Only the date tokens are handled here. The %node.widget% form SaveImage also
    supports is resolved by the frontend before the value ever reaches us, so
    anything of that shape has already been replaced by the time we look.
    """
    def sub(m):
        fmt = m.group(1)
        for a, b in (("yyyy", "%Y"), ("yy", "%y"), ("MM", "%m"), ("dd", "%d"),
                     ("hh", "%H"), ("HH", "%H"), ("mm", "%M"), ("ss", "%S")):
            fmt = fmt.replace(a, b)
        return datetime.now().strftime(fmt)

    return re.sub(r"%date:([^%]+)%", sub, text)


def _unique_path(folder: str, stem: str, stamp: str, ext: str, index: int | None) -> str:
    """<stem>_DDMMYYYY_HHMMSS[_n].<ext>, guaranteed not to exist yet.

    A timestamp beats SaveImage's _00001_ counter for finding things later: the
    name sorts by when it was made and says so without opening anything. What it
    costs is uniqueness, because seconds are not fine-grained enough — a batch of
    four images all land in the same one, and two runs can too. Hence the index
    for batches, and the loop for everything else: it walks _1, _2, _3 until the
    name is free rather than quietly writing over a picture that is already
    there.
    """
    base = f"{stem}_{stamp}"
    if index is not None:
        base = f"{base}_{index}"
    path = os.path.join(folder, f"{base}.{ext}")
    n = 1
    while os.path.exists(path):
        path = os.path.join(folder, f"{base}_{n}.{ext}")
        n += 1
    return path


class AllmaSaveImage(io.ComfyNode):
    """Save images to any folder on this machine."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaSaveImage",
            display_name="Allma Save Image",
            category="Allma",
            search_aliases=["save", "save image", "export", "write image",
                            "custom folder", "path", "outside output"],
            description=(
                "Saves PNGs to a folder you choose instead of ComfyUI's "
                "output directory. An absolute path is used as given; a "
                "relative one is taken from inside output/. Missing folders are "
                "created. Keeps the workflow metadata, so the file still "
                "reloads onto the canvas when dropped on it."
            ),
            inputs=[
                io.Image.Input("images", tooltip="The images to save."),
                io.String.Input(
                    "directory",
                    default="",
                    tooltip="Where to write. Absolute (/home/you/renders, "
                            "D:\\\\art) goes there directly; relative "
                            "(project/today) is taken from inside output/. "
                            "Empty means output/ itself. Supports "
                            "%date:yyyy-MM-dd%.",
                ),
                io.String.Input(
                    "filename_prefix",
                    default="ComfyUI",
                    tooltip="Start of the filename. The save time is appended as "
                            "_DDMMYYYY_HHMMSS, so files sort by when they "
                            "were made and nothing is overwritten. Supports "
                            "%date:yyyy-MM-dd%.",
                ),
            ],
            outputs=[
                io.Image.Output(display_name="images"),
                io.String.Output(display_name="paths"),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, images, directory="",
                filename_prefix="ComfyUI") -> io.NodeOutput:
        out_root = folder_paths.get_output_directory()

        directory = _expand_tokens((directory or "").strip())
        stem = _expand_tokens((filename_prefix or "ComfyUI").strip()) or "ComfyUI"
        # A prefix may carry folders of its own ("video/clip"), exactly as
        # SaveImage allows; they join the directory rather than the filename.
        sub, stem = os.path.split(stem)
        stem = stem or "ComfyUI"

        folder = os.path.expanduser(directory) if directory else out_root
        if not os.path.isabs(folder):
            folder = os.path.join(out_root, folder)
        if sub:
            folder = os.path.join(folder, sub)
        folder = os.path.normpath(folder)

        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            raise RuntimeError(
                f"AllmaSaveImage: cannot create '{folder}': {e}"
            ) from e
        if not os.access(folder, os.W_OK):
            raise RuntimeError(f"AllmaSaveImage: no write permission on '{folder}'")

        # One stamp for the whole batch, so a set of images saved together
        # reads as a set instead of straddling a second boundary.
        stamp = datetime.now().strftime("%d%m%Y_%H%M%S")
        many = len(images) > 1

        metadata = None
        if not args.disable_metadata:
            metadata = PngInfo()
            if cls.hidden.prompt is not None:
                metadata.add_text("prompt", json.dumps(cls.hidden.prompt))
            info = cls.hidden.extra_pnginfo
            if isinstance(info, dict):
                for k in info:
                    metadata.add_text(k, json.dumps(info[k]))

        written: list[str] = []
        results: list[UI.SavedResult] = []
        for batch_number, image in enumerate(images, start=1):
            arr = 255.0 * image.cpu().numpy()
            img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

            path = _unique_path(folder, stem, stamp, "png",
                                batch_number if many else None)
            img.save(path, pnginfo=metadata, compress_level=4)

            written.append(path)

            # /view only serves output/, input/ and temp/ — it refuses absolute
            # paths outright — so a file written elsewhere has no URL of its own.
            # When the destination IS inside output/, point straight at the real
            # file and nothing is duplicated.
            rel = os.path.relpath(path, out_root)
            if not rel.startswith(os.pardir):
                results.append(UI.SavedResult(os.path.basename(path),
                                              os.path.dirname(rel), io.FolderType.output))

        print(f"{LOG} wrote {len(written)} file(s) to {folder}")

        joined = "\n".join(written)

        # Inside output/: show the real files, nothing duplicated.
        if results and len(results) == len(written):
            return io.NodeOutput(images, joined, ui=UI.SavedImages(results))

        # Anywhere else: a small copy in temp/ buys a real thumbnail, which is
        # how ComfyUI's own Preview Image node has always worked. It is the only
        # way to see the picture at all when the original lives somewhere /view
        # will not go, and temp/ is cleaned up on its own.
        try:
            return io.NodeOutput(images, joined,
                                 ui=UI.PreviewImage(images, cls=cls))
        except Exception as e:  # noqa: BLE001
            # The files are already on disk; a preview that will not render is
            # no reason to fail the run. Fall back to naming what was written.
            print(f"{LOG} preview unavailable ({e}); reporting paths instead")
            return io.NodeOutput(images, joined, ui=UI.PreviewText(joined))
