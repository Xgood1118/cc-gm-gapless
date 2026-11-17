# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gapless as mod  # noqa: E402
from gapless import core  # noqa: E402

gapless = mod


def seg(name, rate=48000, samples=48000):
    return gapless.Segment(name=name, rate=rate, samples=samples)


# ------------------------------------------------------------ 骨架原有测试

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


# ------------------------------------------------------------ 累计毫秒误差

def test_cumulative_ms_error_does_not_grow():
    # 段长不是整毫秒、重叠也不是整采样，制造最苛刻的取整条件
    segs = [seg("s%02d" % i, rate=44100, samples=1001 + i) for i in range(50)]
    plan = gapless.plan_timeline(segs, 7)

    bound_ms = 500.0 / plan["rate"]  # 半个采样周期
    assert plan["max_error_ms"] <= bound_ms + 1e-12

    # 每个边界各自独立取整，误差不允许随位置（段数）累积
    rate = plan["rate"]
    for entry in plan["layout"]:
        idx = entry["start"]
        err = abs(idx * 1000.0 / rate - entry["start_ms"])
        assert err <= bound_ms + 1e-9

    # 总时长与独立用 Fraction 计算的精确值一致
    from fractions import Fraction
    exact = sum((Fraction(s.samples, s.rate) for s in segs), Fraction(0))
    exact -= Fraction(7, 1000) * (len(segs) - 1)
    assert abs(plan["total_ms"] - float(exact * 1000)) < 1e-9


# ------------------------------------------------------------ 接缝坐标反查

def test_seam_reverse_lookup():
    segs = gapless.load_playlist("data/playlist.json")
    plan = gapless.plan_timeline(segs, 0)
    seam = plan["seams"][0]

    # 接缝索引处 -> 后段起点；前一个样本 -> 前段
    here = gapless.locate_sample(plan, seam["index"])
    assert here["occurrence"] == seam["next"]["occurrence"]
    assert here["name"] == seam["next"]["name"]
    assert here["offset_samples"] == 0

    before = gapless.locate_sample(plan, seam["index"] - 1)
    assert before["occurrence"] == seam["prev"]["occurrence"]
    assert before["name"] == seam["prev"]["name"]

    # 反查偏移：接缝前一个样本 = 接缝边界的段内偏移减一
    assert (
        before["offset_samples"]
        == seam["prev"]["offset_samples"] - 1
    )
    # 接缝边界本身精确落在前段末尾（1000ms / 第 48000 个样本）
    assert seam["prev"]["offset_ms"] == 1000.0
    assert seam["prev"]["offset_samples"] == 48000
    assert abs(before["offset_ms"] - (1000.0 - 1000.0 / 48000)) < 1e-9

    # 越界返回 None
    assert gapless.locate_sample(plan, plan["total_samples"]) is None
    assert gapless.locate_sample(plan, -1) is None


def test_seam_index_matches_rendered_samples():
    segs = gapless.load_playlist("data/playlist.json")
    plan = gapless.plan_timeline(segs, 0)
    rendered = gapless.render_timeline(segs, plan)
    assert rendered["total_samples"] == plan["total_samples"]
    for seam in plan["seams"]:
        assert seam["index"] < rendered["total_samples"]


# ------------------------------------------------------------ 同段引用两次

def test_duplicate_reference_not_polluted():
    shared = seg("dup", samples=4800)
    segs = [shared, seg("mid", samples=2400), shared]
    plan = gapless.plan_timeline(segs, 0)

    occurrences = [e["occurrence"] for e in plan["layout"]]
    assert occurrences == [0, 1, 2]  # 两次引用是两个独立条目

    names = [e["name"] for e in plan["layout"]]
    assert names == ["dup", "mid", "dup"]

    # 两个接缝分别指向不同的出现序号，坐标互不相同
    assert plan["seams"][0]["prev"]["occurrence"] == 0
    assert plan["seams"][1]["next"]["occurrence"] == 2
    assert plan["seams"][0]["index"] != plan["seams"][1]["index"]

    # 两个同名条目的绝对坐标不同、段内偏移各自独立
    first, second, third = plan["layout"]
    assert first["start"] == 0 and first["end"] == 4800
    assert second["start"] == 4800 and second["end"] == 7200
    assert third["start"] == 7200 and third["end"] == 12000

    rendered = gapless.render_timeline(segs, plan)
    assert rendered["total_samples"] == 12000


# ------------------------------------------------------------ 重叠交叉淡化

def _patch_waveforms(monkeypatch, values_by_name):
    def fake_waveform(seg):
        return list(values_by_name[seg.name])

    monkeypatch.setattr(core, "_waveform", fake_waveform)


def test_crossfade_equal_power_gain_identity(monkeypatch):
    n = 4000
    overlap_ms = 25  # 48000Hz -> 1200 采样
    ones = [1.0] * n
    zeros = [0.0] * n

    # A 有声 B 静音 -> 重叠区只剩 sin 淡入增益
    _patch_waveforms(monkeypatch, {"a": ones, "b": zeros})
    plan = gapless.plan_timeline(
        [seg("a", samples=n), seg("b", samples=n)], overlap_ms
    )
    only_a = gapless.render_timeline(
        [seg("a", samples=n), seg("b", samples=n)], plan
    )["samples"]

    # A 静音 B 有声 -> 重叠区只剩 cos 淡出增益
    _patch_waveforms(monkeypatch, {"a": zeros, "b": ones})
    only_b = gapless.render_timeline(
        [seg("a", samples=n), seg("b", samples=n)], plan
    )["samples"]

    seam = plan["seams"][0]
    L = seam["overlap_samples"]
    assert L == 1200
    for j in range(L):
        idx = seam["index"] + j
        # 等功率恒等式：sin^2 + cos^2 = 1（量化后允许 1 个量化级误差）
        assert abs(only_a[idx] ** 2 + only_b[idx] ** 2 - 32767 ** 2) <= 65536

    # 接缝处能量不会突然下降：两条增益在重叠区内都严格大于 0
    assert min(only_a[seam["index"]:seam["index"] + L]) > 0
    assert min(only_b[seam["index"]:seam["index"] + L]) > 0


def test_crossfade_energy_no_dip(monkeypatch):
    # 真实伪噪声源：重叠区 MSE 应与普通区接近（等功率保持能量）
    segs = [seg("x", samples=9600), seg("y", samples=9600)]
    plan = gapless.plan_timeline(segs, 50)
    rendered = gapless.render_timeline(segs, plan)["samples"]
    seam = plan["seams"][0]
    L = seam["overlap_samples"]

    interior = slice(seam["index"] + L // 4, seam["index"] + 3 * L // 4)
    plain = slice(100, 100 + L // 2)
    mse_overlap = sum(v * v for v in rendered[interior]) / (L // 2)
    mse_plain = sum(v * v for v in rendered[plain]) / (L // 2)
    assert abs(mse_overlap - mse_plain) / mse_plain < 0.1


def test_overlap_longer_than_segment_is_clamped():
    segs = [seg("long", samples=48000), seg("tiny", samples=480)]
    plan = gapless.plan_timeline(segs, 1000)  # 重叠远长于 tiny
    seam = plan["seams"][0]
    assert seam["overlap_samples"] == 480  # 夹到短段长度
    rendered = gapless.render_timeline(segs, plan)
    assert rendered["total_samples"] == plan["total_samples"]


# ------------------------------------------------------------ 空段 / 零长度段

def test_empty_segments_skipped():
    segs = [
        seg("a", samples=48000),
        seg("empty", samples=0),
        seg("b", samples=24000),
    ]
    plan = gapless.plan_timeline(segs, 0)
    assert [e["name"] for e in plan["layout"]] == ["a", "b"]
    assert plan["total_ms"] == 1500.0  # 1000ms + 500ms，空段不占时长
    assert len(plan["seams"]) == 1

    rendered = gapless.render_timeline(segs, plan)
    assert rendered["total_samples"] == 72000


def test_all_empty_playlist():
    plan = gapless.plan_timeline(
        [seg("e1", samples=0), seg("e2", samples=0)], 0
    )
    assert plan["total_ms"] == 0
    assert plan["total_samples"] == 0
    assert plan["seams"] == [] and plan["layout"] == []
    rendered = gapless.render_timeline(
        [seg("e1", samples=0), seg("e2", samples=0)], plan
    )
    assert rendered["samples"] == []


def test_data_playlist_zero_segment_skipped():
    segs = gapless.load_playlist("data/playlist.json")
    plan = gapless.plan_timeline(segs, 0)
    assert len(plan["layout"]) == 2  # c 的 samples=0 被跳过
    assert plan["total_ms"] == 1500.0


# ------------------------------------------------------------ 两次输出一致

def test_render_byte_identical_across_runs():
    segs = gapless.load_playlist("data/playlist.json")
    plan = gapless.plan_timeline(segs, 30)
    first = gapless.render_timeline(segs, plan)
    second = gapless.render_timeline(segs, plan)
    assert first["samples"] == second["samples"]

    packed1 = b"".join(v.to_bytes(2, "little", signed=True)
                       for v in first["samples"])
    packed2 = b"".join(v.to_bytes(2, "little", signed=True)
                       for v in second["samples"])
    assert packed1 == packed2
    assert len(packed1) == first["total_samples"] * 2


# ------------------------------------------------------------ 采样率策略

def test_mixed_rates_resample_to_highest():
    segs = [seg("lo", rate=24000, samples=12000),   # 0.5s
            seg("hi", rate=48000, samples=24000)]   # 0.5s
    plan = gapless.plan_timeline(segs, 0)
    assert plan["rate"] == 48000
    assert plan["total_samples"] == 48000
    assert plan["total_ms"] == 1000.0

    rendered = gapless.render_timeline(segs, plan)
    assert rendered["rate"] == 48000
    assert len(rendered["samples"]) == 48000

    # 重采样后布局条目保留原始采样率信息，可反查回原段
    assert plan["layout"][0]["rate"] == 24000
    hit = gapless.locate_sample(plan, 100)
    assert hit["name"] == "lo" and hit["occurrence"] == 0


def test_negative_overlap_rejected():
    import pytest
    with pytest.raises(ValueError):
        gapless.plan_timeline([seg("a")], -1)
