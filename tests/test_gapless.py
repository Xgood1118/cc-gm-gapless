# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gapless as mod  # noqa: E402
gapless = mod

def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_hex(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def test_timeline_covers_samples():
    segs = gapless.load_playlist("data/playlist.json")
    plan = gapless.plan_timeline(segs, 0)
    assert plan["seams"], "应当有接缝坐标"
    assert plan["total_ms"] > 0


def test_seam_maps_back():
    segs = gapless.load_playlist("data/playlist.json")
    plan = gapless.plan_timeline(segs, 0)
    for seam in plan["seams"]:
        assert seam["index"] >= 0 and seam["offset_ms"] >= 0
