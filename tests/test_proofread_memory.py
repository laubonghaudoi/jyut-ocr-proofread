#!/usr/bin/env python3
"""Offline checks for proofread_pages host RAM optimizations."""

from __future__ import annotations

import concurrent.futures as cf
import io
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import proofread_pages as pp  # noqa: E402


def _reference_measure_columns(
    render: Path, box: tuple[float, float, float, float], turned: bool = False
) -> list[tuple[int, int, int, int]] | None:
    with Image.open(render) as image:
        x, y, w, h = box
        if turned:
            image = image.transpose(Image.ROTATE_270)
            x, y, w, h = 1 - y - h, x, h, w
        width, height = image.size
        left, top = int(x * width), int(y * height)
        region = image.convert("L").crop(
            (left, top, int((x + w) * width), int((y + h) * height))
        )
    if region.width < 20 or region.height < 20:
        return None
    ink = pp.ink_of(region)
    cols = pp.ink_columns(ink)
    return [(left + a, left + b, top + y0, top + y1) for a, b, y0, y1 in cols] or None


class ProofreadMemoryTests(unittest.TestCase):
    def test_measure_columns_crop_first_matches_reference_turned_and_upright(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            render = Path(tmp) / "page.png"
            img = Image.new("RGB", (240, 180), (255, 255, 255))
            draw = ImageDraw.Draw(img)
            # Vertical columns for upright crop (box x in 0.1..0.9, y in 0.1..0.9)
            draw.rectangle((45, 30, 58, 120), fill=(10, 10, 10))
            draw.rectangle((80, 40, 94, 140), fill=(10, 10, 10))
            # Horizontal bands inside rotated coordinates become vertical columns after ROTATE_270
            draw.rectangle((140, 25, 210, 42), fill=(10, 10, 10))
            draw.rectangle((145, 70, 215, 86), fill=(10, 10, 10))
            img.save(render, "PNG")

            upright_box = (0.12, 0.11, 0.40, 0.78)
            got_upright = pp.measure_columns(render, upright_box, turned=False)
            want_upright = _reference_measure_columns(render, upright_box, turned=False)
            self.assertIsNotNone(got_upright)
            self.assertEqual(got_upright, want_upright)

            turned_box = (0.54, 0.08, 0.41, 0.56)
            got_turned = pp.measure_columns(render, turned_box, turned=True)
            want_turned = _reference_measure_columns(render, turned_box, turned=True)
            self.assertIsNotNone(got_turned)
            self.assertEqual(got_turned, want_turned)

    def test_fit_media_budget_caches_scaled_bytes_and_caps_lru_entries(self) -> None:
        def make_png(color: tuple[int, int, int]) -> bytes:
            buf = io.BytesIO()
            Image.new("RGB", (200, 100), color).save(buf, "PNG")
            return buf.getvalue()

        with pp._MEDIA_BUDGET_CACHE_LOCK:
            saved = dict(pp._MEDIA_BUDGET_CACHE)
            pp._MEDIA_BUDGET_CACHE.clear()
        try:
            png = make_png((20, 30, 40))
            real_resize = Image.Image.resize
            resize_calls = 0

            def counted_resize(self, *args, **kwargs):
                nonlocal resize_calls
                resize_calls += 1
                return real_resize(self, *args, **kwargs)

            with mock.patch.object(Image.Image, "resize", counted_resize):
                first = pp.fit_media_budget(png, limit=5_000)
                second = pp.fit_media_budget(png, limit=5_000)
                self.assertEqual(first, second)
                self.assertEqual(resize_calls, 1)

            for i in range(pp._MEDIA_BUDGET_CACHE_MAX + 3):
                pp.fit_media_budget(make_png((i, i + 10, i + 20)), limit=5_000)
            with pp._MEDIA_BUDGET_CACHE_LOCK:
                self.assertEqual(len(pp._MEDIA_BUDGET_CACHE), pp._MEDIA_BUDGET_CACHE_MAX)
        finally:
            with pp._MEDIA_BUDGET_CACHE_LOCK:
                pp._MEDIA_BUDGET_CACHE.clear()
                pp._MEDIA_BUDGET_CACHE.update(saved)

    def test_run_windowed_bounds_pending_futures_and_collects_all_jobs(self) -> None:
        jobs = list(range(25))
        window = 5
        lock = threading.Lock()
        submitted = 0
        yielded = 0
        max_uncollected = 0

        def worker(item: int) -> int:
            nonlocal submitted, max_uncollected
            with lock:
                submitted += 1
                max_uncollected = max(max_uncollected, submitted - yielded)
            time.sleep(0.01)
            if item == 17:
                raise ValueError("boom")
            return item * 2

        results: dict[int, int] = {}
        errors: list[int] = []
        with cf.ThreadPoolExecutor(4) as pool:
            for job, future in pp._run_windowed(pool, worker, jobs, window):
                with lock:
                    yielded += 1
                try:
                    results[job] = future.result()
                except ValueError:
                    errors.append(job)

        self.assertLessEqual(max_uncollected, window)
        self.assertEqual(errors, [17])
        self.assertEqual(len(results) + len(errors), len(jobs))
        for j in jobs:
            if j != 17:
                self.assertEqual(results[j], j * 2)


if __name__ == "__main__":
    unittest.main()
