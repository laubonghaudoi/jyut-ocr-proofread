#!/usr/bin/env python3
"""Persistent PaddleOCR-VL worker: load the pipeline once, OCR many pages.

The parent (``run_paddleocr_vl.py``) owns every filesystem write, the provenance
seal and the atomic promote.  This process only turns a render into normalised
line records, so a crash here can never leave a half-written completion pair.

Protocol: one JSON request object per stdin line, one JSON response object per
stdout line.  PaddleOCR and its dependencies are chatty on stdout, so the real
stdout handle is duplicated away at startup and every library write is funnelled
to stderr; responses are the only bytes that ever reach the parent.

Request  : {"renderFile": "<abs path>"}
Response : {"ok": true, "lines": [...], "regions": [...], "width": W, "height": H}
           {"ok": false, "error": "<message>"}

``regions`` are the layout regions that hold no text (normalise_page).

The parent chooses the device and the weights through the environment:
JYUT_PADDLE_DEVICE (``gpu:N`` or ``cpu``), JYUT_PADDLE_VL_MODEL_DIR and
JYUT_PADDLE_LAYOUT_MODEL_DIR.  For a GPU the worker first checks that this
Paddle build can see that card (PaddleX would otherwise run on the CPU and only
log a warning), and the parent sets PADDLE_PDX_DISABLE_DEVICE_FALLBACK so that
PaddleX raises instead of falling back.  The ready line reports what was
actually loaded: {"ok": true, "ready": true, "execution": {...}}
(loaded_execution).
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import sys
from typing import Any


def emit(channel: Any, payload: dict[str, Any]) -> None:
    channel.write(json.dumps(payload, ensure_ascii=False) + "\n")
    channel.flush()


def _is_vertical(blocks: list[dict[str, Any]], width: int, height: int) -> bool:
    """Whether the page reads in vertical columns, right to left.

    Judged from the blocks themselves: a column of vertical text is tall and
    narrow.  Falls back to False (horizontal, left-to-right) when there is
    nothing to go on, because that is the safer default for a Latin page.
    """
    ratios = []
    for block in blocks:
        bbox = block.get("block_bbox")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            continue
        x0, y0, x1, y1 = (float(v) for v in bbox)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w > 0 and h > 0:
            ratios.append(h / w)
    if not ratios:
        return False
    ratios.sort()
    return ratios[len(ratios) // 2] > 1.4


def _reading_key(block: dict[str, Any], width: int, vertical: bool) -> tuple[float, float]:
    """Sort key placing a block in reading order from its position alone."""
    bbox = block.get("block_bbox")
    if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
        return (0.0, 0.0)
    x0, y0, x1, y1 = (float(v) for v in bbox)
    if vertical:
        # Right to left: the rightmost column is read first.
        return (-(x0 + x1) / 2.0, (y0 + y1) / 2.0)
    return ((y0 + y1) / 2.0, (x0 + x1) / 2.0)


def _interleave(ordered: list[dict[str, Any]], unordered: list[dict[str, Any]],
                width: int, height: int) -> list[dict[str, Any]]:
    """Merge geometrically-placed blocks into the model's ordered sequence."""
    if not ordered:
        return unordered
    if not unordered:
        return ordered
    vertical = _is_vertical(ordered + unordered, width, height)
    merged = list(ordered)
    for block in unordered:
        key = _reading_key(block, width, vertical)
        position = len(merged)
        for index, other in enumerate(merged):
            if _reading_key(other, width, vertical) > key:
                position = index
                break
        merged.insert(position, block)
    return merged


def normalise_lines(result: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    """The page's text blocks alone, in the runner's line contract (normalise_page)."""
    lines, _, width, height = normalise_page(result)
    return lines, width, height


def normalise_page(
    result: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    """Map a PaddleOCR-VL page result onto the runner's line contract.

    ``parsing_res_list`` carries the text and the reading order that layout
    analysis resolved; ``layout_det_res.boxes`` carries the detector score for
    the same blocks.  They are joined on the shared ``order`` value rather than
    on list position, because merged blocks make the two lists differ in length.

    Returns the text blocks (``lines``, whose text is the page's text), the
    layout regions that hold no text (``regions``), and the page size.  A
    region with no text is retained for visual review instead of disappearing
    from the draft. These regions are kept apart from recognized text lines.
    """
    width = int(result.get("width") or 0)
    height = int(result.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("page result is missing usable width/height")

    scores: dict[int, float] = {}
    detection = result.get("layout_det_res")
    if isinstance(detection, dict):
        for box in detection.get("boxes") or []:
            if not isinstance(box, dict):
                continue
            try:
                scores[int(box["order"])] = float(box["score"])
            except (KeyError, TypeError, ValueError):
                continue

    blocks = [b for b in (result.get("parsing_res_list") or []) if isinstance(b, dict)]
    # Reading order is the layout model's job wherever it did the job.  But
    # PaddleX lists table, image, chart, header, footer and several other labels
    # in SKIP_ORDER_LABELS, so those blocks arrive with block_order None and the
    # old sort dumped every one of them at the end of the page.
    #
    # Where the model gives no order, fall back to geometry. This is only a
    # draft ordering heuristic; the agent verifies reading order against images.
    ordered = [b for b in blocks
               if _as_int(b.get("block_order"), default=-1) >= 0]
    unordered = [b for b in blocks
                 if _as_int(b.get("block_order"), default=-1) < 0]
    if unordered:
        vertical = _is_vertical(blocks, width, height)
        for block in unordered:
            block["_geometric_order"] = _reading_key(block, width, vertical)
    ordered.sort(key=lambda b: _as_int(b.get("block_order"), default=1 << 30))
    unordered.sort(key=lambda b: b.get("_geometric_order", (0.0, 0.0)))
    # Ordered blocks keep the model's sequence; unordered ones are placed by
    # geometry rather than appended blindly.
    blocks = _interleave(ordered, unordered, width, height)

    lines: list[dict[str, Any]] = []
    regions: list[dict[str, Any]] = []
    # A VL server whose chat template does not place the image (the model's own template takes
    # only {"type": "image"} parts; PaddleX sends "image_url") opens some blocks with a newline
    # the native and MLX readers never write, which led the repair's line pairing astray
    # (doukeoi-zingkeoi/s0583 lost its bibliography's Western lines).  The server should run with
    # runtime/paddleocr-vl-server-chat-template.jinja (references/local-ocr.md); this guard keeps
    # such a newline out of the drafts all the same.  It is not text: it goes.
    served = os.environ.get("JYUT_PADDLE_VL_BACKEND") in ("sglang-server", "vllm-server")
    for block in blocks:
        text = block.get("block_content")
        if served and isinstance(text, str):
            text = text.lstrip("\r\n")
        order = _as_int(block.get("block_order"), default=-1)
        bbox = block.get("block_bbox")
        if not isinstance(text, str) or not text.strip():
            # Where it falls in the reading order: after this many lines.
            regions.append(
                {
                    "confidence": float(scores.get(order, 0.0)),
                    "boundingBox": normalise_bbox(bbox, width, height),
                    "blockLabel": str(block.get("block_label") or "unknown"),
                    "blockOrder": order,
                    "afterLine": len(lines),
                }
            )
            continue
        lines.append(
            {
                "text": text,
                # Detector confidence for the block, NOT a per-character
                # recognition confidence.  Low values mean "this region may be
                # mis-segmented", not "these glyphs may be misread".
                "confidence": float(scores.get(order, 0.0)),
                "boundingBox": normalise_bbox(bbox, width, height),
                "blockLabel": str(block.get("block_label") or "unknown"),
                "blockOrder": order,
            }
        )
    return lines, regions, width, height


def _as_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalise_bbox(bbox: Any, width: int, height: int) -> list[float]:
    """Return [x, y, w, h] normalised to 0..1 with a TOP-LEFT origin.

    PaddleOCR emits absolute pixel [x1, y1, x2, y2].  The origin convention is
    recorded in the runner's OCR profile so this is never confused with Apple
    Vision's bottom-left normalised rect.
    """
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return [0.0, 0.0, 0.0, 0.0]
    left, right = min(x1, x2), max(x1, x2)
    top, bottom = min(y1, y2), max(y1, y2)
    return [
        round(left / width, 6),
        round(top / height, 6),
        round((right - left) / width, 6),
        round((bottom - top) / height, 6),
    ]


def gpu_unavailable(device: str) -> str | None:
    """Why this Paddle build cannot run on DEVICE (``gpu:N``), or None.

    The same test PaddleX applies before it silently switches to the CPU
    (is_compiled_with_cuda and cuda.device_count), made before any weights load.
    """
    import paddle

    index = int(device.partition(":")[2] or 0)
    if not paddle.device.is_compiled_with_cuda():
        return (f"{device} is configured, but this Paddle build has no CUDA (a CPU "
                "package); Paddle would run on the CPU instead")
    count = int(paddle.device.cuda.device_count())
    if count <= index:
        return (f"{device} is configured, but Paddle sees {count} CUDA device(s); "
                "Paddle would run on the CPU instead")
    return None


def _attribute(value: Any, *names: str) -> Any:
    for name in names:
        try:
            value = getattr(value, name)
        except Exception:  # noqa: BLE001 - PaddleX internals vary by version
            return None
    return value


def loaded_execution(pipeline: Any, device: str | None) -> dict[str, Any]:
    """The device and dtype PaddleX actually loaded the two models with.

    Read from PaddleX's predictors (the VL recogniser's ``device``/``dtype``,
    the layout detector's static-runner config); None where this PaddleX
    version does not expose them.
    """
    inner = _attribute(pipeline, "paddlex_pipeline")
    recogniser = _attribute(inner, "vl_rec_model")
    vl_device = _attribute(recogniser, "device")
    dtype = _attribute(recogniser, "dtype")
    config = _attribute(inner, "layout_det_model", "runner", "_config")
    layout_device = None
    if isinstance(config, dict) and config.get("device_type"):
        layout_device = str(config["device_type"])
        if config.get("device_id") is not None:
            layout_device += f":{config['device_id']}"
    execution = {
        "requestedDevice": device,
        "vlDevice": str(vl_device) if vl_device is not None else None,
        "layoutDevice": layout_device,
        "dtype": str(dtype) if dtype is not None else None,
    }
    config = _attribute(recogniser, "_genai_config")
    client = _attribute(recogniser, "genai_client")
    if config is not None:
        execution.update({
            "vlBackend": _attribute(config, "backend"),
            "vlServerUrl": _attribute(config, "server_url"),
            "vlApiModel": _attribute(client, "_model_name"),
            "vlMaxConcurrency": _attribute(client, "_max_concurrency"),
        })
    return execution


USAGE = "usage: paddleocr_vl_worker.py [--help]"


def main(argv: list[str] | None = None) -> int:
    # --help before anything is loaded: it needs neither paddleocr nor a GPU.
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["--help"], ["-h"]):
        print(f"{USAGE}\n\nStarted by run_paddleocr_vl.py, which talks to it on stdin and stdout; "
              "it takes no other argument.\n\n" + (__doc__ or "").strip())
        return 0
    if argv:
        print(f"{USAGE}\npaddleocr_vl_worker.py: no arguments are taken (got {' '.join(argv)}); "
              "run_paddleocr_vl.py starts it", file=sys.stderr)
        return 2
    # Take the real stdout away from every library that might print to it.
    response_fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    responses = os.fdopen(response_fd, "w", encoding="utf-8")

    os.environ.setdefault("FLAGS_use_cuda_managed_memory", "false")
    device = os.environ.get("JYUT_PADDLE_DEVICE") or None
    options: dict[str, Any] = {}
    if device:
        options["device"] = device
    for option, variable in (("vl_rec_model_dir", "JYUT_PADDLE_VL_MODEL_DIR"),
                             ("layout_detection_model_dir", "JYUT_PADDLE_LAYOUT_MODEL_DIR")):
        if os.environ.get(variable):
            options[option] = os.environ[variable]
    if os.environ.get("JYUT_PADDLE_VL_BACKEND") == "mlx-vlm-server":
        options.pop("vl_rec_model_dir", None)
        options.update(device="cpu", vl_rec_backend="mlx-vlm-server",
                       vl_rec_server_url=os.environ["JYUT_PADDLE_VL_SERVER_URL"],
                       vl_rec_api_model_name=os.environ["JYUT_PADDLE_VL_API_MODEL"],
                       vl_rec_max_concurrency=1)
    elif os.environ.get("JYUT_PADDLE_VL_BACKEND") in ("sglang-server", "vllm-server"):
        # A server someone else runs reads the blocks; the layout detector stays on the device.
        options.pop("vl_rec_model_dir", None)
        options.update(vl_rec_backend=os.environ["JYUT_PADDLE_VL_BACKEND"],
                       vl_rec_server_url=os.environ["JYUT_PADDLE_VL_SERVER_URL"],
                       vl_rec_api_model_name=os.environ["JYUT_PADDLE_VL_API_MODEL"],
                       vl_rec_max_concurrency=int(os.environ.get("JYUT_PADDLE_VL_CONCURRENCY", "16")))

    try:
        with contextlib.redirect_stdout(sys.stderr):
            if device and device.startswith("gpu"):
                reason = gpu_unavailable(device)
                if reason:
                    emit(responses, {"ok": False, "error": f"pipeline load refused: {reason}"})
                    return 3
            from paddleocr import PaddleOCRVL

            pipeline = PaddleOCRVL(
                pipeline_version=os.environ.get("JYUT_PADDLE_PIPELINE_VERSION", "v1.6"),
                **options)
            execution = loaded_execution(pipeline, device)
    except Exception as error:  # noqa: BLE001 - reported to the parent verbatim
        emit(responses, {"ok": False, "error": f"pipeline load failed: {error}"})
        return 3

    emit(responses, {"ok": True, "ready": True, "execution": execution})

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
            render_file = request["renderFile"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            emit(responses, {"ok": False, "error": f"malformed request: {error}"})
            continue

        try:
            with contextlib.redirect_stdout(sys.stderr):
                pages = pipeline.predict(str(render_file))
            results = [p.json["res"] if hasattr(p, "json") else dict(p) for p in pages]
            if len(results) != 1:
                raise ValueError(
                    f"expected exactly 1 page result, got {len(results)}"
                )
            lines, regions, width, height = normalise_page(copy.deepcopy(results[0]))
            emit(
                responses,
                {"ok": True, "lines": lines, "regions": regions,
                 "width": width, "height": height, "rawResult": results[0]},
            )
        except Exception as error:  # noqa: BLE001 - one bad page must not kill the pool
            emit(responses, {"ok": False, "error": str(error)})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
