"""Deterministic horizontal Vision observations to conservative layout blocks."""
from __future__ import annotations

import math
import statistics
from typing import Any, Mapping

from _draft_contract import LAYOUT_CONTRACT

LAYOUT_VERSION = "vision-horizontal-v1"


def adapt_observations(record: Mapping[str, Any]) -> dict[str, Any]:
    raw = record.get("lines", [])
    observations = []
    for index, line in enumerate(raw):
        x, bottom, width, height = line["boundingBox"]
        if (not all(math.isfinite(v) for v in (x, bottom, width, height))
                or min(x, bottom) < -0.000001 or min(width, height) <= 0
                or x + width > 1.000001 or bottom + height > 1.000001):
            raise ValueError(f"Vision observation {index} has invalid normalized geometry")
        observations.append({**line, "lineID": line.get("lineID", index),
                             "boundingBox": [max(0.0, x), max(0.0, 1 - bottom - height), width, height]})
    observations.sort(key=lambda line: (line["boundingBox"][1], line["boundingBox"][0], line["lineID"]))
    heights = [line["boundingBox"][3] for line in observations]
    median = statistics.median(heights) if heights else 0
    blocks: list[dict[str, Any]] = []
    # Adjacent similarly sized, left-aligned lines form a paragraph. Keep
    # columns, titles and small notes separate, and never discard margins.
    for observation in observations:
        x, y, width, height = observation["boundingBox"]
        label = "text"
        if median and height > median * 1.45 and y < 0.35:
            label = "title"
        elif median and height < median * 0.78 and y > 0.70:
            label = "vision_footnote"
        candidate = "header" if y + height < 0.09 else "footer" if y > 0.91 else None
        previous = blocks[-1] if blocks else None
        merge = False
        if previous and label == previous["blockLabel"] == "text" and not candidate and not previous.get("furnitureCandidate"):
            px, py, pw, ph = previous["boundingBox"]
            last = previous["sourceLines"][-1]["boundingBox"]
            gap = y - (last[1] + last[3])
            merge = (0 <= gap <= max(height, last[3]) * 0.85
                     and abs(x - px) <= max(height, last[3]) * 1.1
                     and 0.75 <= height / last[3] <= 1.33)
        if merge:
            px, py, pw, ph = previous["boundingBox"]
            previous["boundingBox"] = [min(px, x), min(py, y), max(px + pw, x + width) - min(px, x),
                                       max(py + ph, y + height) - min(py, y)]
            previous["text"] += "\n" + observation["text"]
            previous["confidence"] = min(previous["confidence"], observation["confidence"])
            previous["lineIDs"].append(observation["lineID"])
            previous["sourceLines"].append(observation)
        else:
            block = {"text": observation["text"], "confidence": observation["confidence"],
                     "boundingBox": observation["boundingBox"], "blockLabel": label,
                     "blockOrder": len(blocks), "lineIDs": [observation["lineID"]],
                     "sourceLines": [observation]}
            if candidate:
                block["furnitureCandidate"] = candidate
            blocks.append(block)
    return {**record, "backend": "apple-vision", "layoutVersion": LAYOUT_VERSION,
            "layoutContract": {**LAYOUT_CONTRACT, "orientation": "horizontal", "nativeTableLayout": False},
            "rawObservations": [{**line, "lineID": line.get("lineID", index)} for index, line in enumerate(raw)],
            "rawCoordinateSystem": "normalized-bottom-left-xywh", "lines": blocks}
