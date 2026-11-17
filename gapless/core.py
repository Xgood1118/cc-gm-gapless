# -*- coding: utf-8 -*-
"""无缝拼接时间轴（仅使用 Python 标准库）。

设计约定：
- 时间轴内部所有时间量都用 ``fractions.Fraction`` 精确表示，毫秒坐标由
  精确时间一次性换算，不做逐段浮点累加，因此累计误差不会随段数增长。
- 采样率不一致时，统一 **按最高采样率重采样**（线性插值），不报错。
- 每个边界只做一次四舍五入得到整数样本索引，单个坐标的误差不超过
  半个采样周期（即 ``500 / rate`` 毫秒），与段数无关。
- 重叠区采用等功率交叉淡化（sin/cos 增益，二者平方和恒为 1），
  接缝处能量不会突然下降。
- 空段 / 零长度段在规划阶段直接跳过，不产生接缝，也不改变总时长。
- 时间轴上的每一段都按其在输入列表中的“出现序号”定位，同一段被引用
  两次时得到两个互相独立的布局条目，接缝坐标互不污染。
"""

import hashlib
import json
import math
from dataclasses import dataclass
from fractions import Fraction

__all__ = [
    "Segment",
    "load_playlist",
    "plan_timeline",
    "render_timeline",
    "locate_sample",
]


@dataclass
class Segment:
    """一段音频：名字、采样率、采样数。"""
    name: str
    rate: int
    samples: int


# ---------------------------------------------------------------- 输入解析

def load_playlist(path):
    """读取播放清单，返回 ``Segment`` 列表（保留原始顺序，不过滤空段）。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ValueError("播放清单格式错误：缺少 segments 列表")
    segments = []
    for i, item in enumerate(data["segments"]):
        if not isinstance(item, dict):
            raise ValueError("第 %d 段格式错误：应为对象" % i)
        try:
            segments.append(
                Segment(
                    name=str(item["name"]),
                    rate=int(item["rate"]),
                    samples=int(item["samples"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("第 %d 段格式错误：%s" % (i, exc))
    return segments


# ---------------------------------------------------------------- 工具函数

def _round_half_up(fraction):
    """对非负 Fraction 做四舍五入（half up），返回 int。"""
    return (fraction.numerator * 2 + fraction.denominator) // (
        2 * fraction.denominator
    )


def _ms(fraction):
    """精确秒数（Fraction）-> 毫秒浮点数。"""
    return float(fraction * 1000)


# ---------------------------------------------------------------- 时间轴规划

def plan_timeline(segments, overlap_ms):
    """算出每段起点、终点与接缝坐标。

    返回的 plan 字典：
      rate           输出采样率（各段最高采样率）
      total_ms       总时长（毫秒）
      total_samples  输出总样本数
      max_error_ms   所有边界上“毫秒坐标 vs 样本索引”的最大误差
      layout         每个有效段出现一次的布局条目（含正反向定位信息）
      seams          相邻两段之间的接缝坐标（可反查回原段与段内偏移）
    """
    if overlap_ms < 0:
        raise ValueError("overlap_ms 不能为负数")

    # 跳过空段 / 零长度段；occ 保留该段在输入列表中的原始下标
    kept = []
    for occ, seg in enumerate(segments):
        if not isinstance(seg.rate, int) or seg.rate <= 0:
            raise ValueError("第 %d 段采样率非法：%r" % (occ, seg.rate))
        if seg.samples is None or seg.samples <= 0:
            continue
        kept.append((occ, seg))

    if not kept:
        return {
            "rate": 0,
            "total_ms": 0.0,
            "total_samples": 0,
            "max_error_ms": 0.0,
            "layout": [],
            "seams": [],
        }

    rate = max(seg.rate for _, seg in kept)
    overlap = Fraction(overlap_ms, 1000)  # 配置的重叠秒数

    durations = [Fraction(seg.samples, seg.rate) for _, seg in kept]

    # 精确起点时间：t_k = t_{k-1} + dur_{k-1} - 实际重叠
    exact_starts = [Fraction(0)]
    gap_overlaps = []  # 每个接缝实际生效的重叠时长（Fraction 秒）
    for k in range(1, len(kept)):
        # 重叠不能超过任何一侧段长，避免交叉淡化区域越界
        ov = min(overlap, durations[k - 1], durations[k])
        gap_overlaps.append(ov)
        exact_starts.append(exact_starts[-1] + durations[k - 1] - ov)

    exact_end = exact_starts[-1] + durations[-1]

    # 每个精确边界只做一次取整 -> 误差有界且不累积
    sample_starts = [_round_half_up(t * rate) for t in exact_starts]
    sample_ends = [
        _round_half_up((t + d) * rate)
        for t, d in zip(exact_starts, durations)
    ]
    total_samples = sample_ends[-1]

    layout = []
    for k, (occ, seg) in enumerate(kept):
        layout.append(
            {
                "occurrence": occ,
                "name": seg.name,
                "rate": seg.rate,
                "source_samples": seg.samples,
                "start": sample_starts[k],
                "end": sample_ends[k],
                "start_ms": _ms(exact_starts[k]),
                "end_ms": _ms(exact_starts[k] + durations[k]),
                "duration_ms": _ms(durations[k]),
            }
        )

    seams = []
    for k in range(1, len(kept)):
        prev_occ, prev_seg = kept[k - 1]
        next_occ, next_seg = kept[k]
        ov = gap_overlaps[k - 1]
        index = sample_starts[k]
        overlap_samples = sample_ends[k - 1] - sample_starts[k]

        # 反查信息：接缝点落在前段的 dur-ov 处、后段的 0 处
        prev_local_time = durations[k - 1] - ov
        prev_native_offset = _round_half_up(prev_local_time * prev_seg.rate)

        seams.append(
            {
                "index": index,
                "offset_ms": _ms(exact_starts[k]),
                "overlap_samples": overlap_samples,
                "overlap_ms": _ms(ov),
                "prev": {
                    "occurrence": prev_occ,
                    "name": prev_seg.name,
                    "offset_samples": prev_native_offset,
                    "offset_ms": _ms(prev_local_time),
                },
                "next": {
                    "occurrence": next_occ,
                    "name": next_seg.name,
                    "offset_samples": 0,
                    "offset_ms": 0.0,
                },
            }
        )

    # 最大毫秒误差：每个边界 |取整后毫秒 - 精确毫秒|，上界 500/rate ms
    max_error = Fraction(0)
    for exact in exact_starts + [exact_end]:
        rounded = _round_half_up(exact * rate)
        err = abs(Fraction(rounded * 1000, rate) - exact * 1000)
        if err > max_error:
            max_error = err

    return {
        "rate": rate,
        "total_ms": _ms(exact_end),
        "total_samples": total_samples,
        "max_error_ms": float(max_error),
        "layout": layout,
        "seams": seams,
    }


def locate_sample(plan, index):
    """把渲染输出中的样本索引反查回原段出现序号与段内偏移。

    返回 ``{"occurrence", "name", "offset_samples", "offset_ms"}``；
    索引越界时返回 ``None``。
    """
    if index < 0 or index >= plan["total_samples"]:
        return None
    rate = plan["rate"]
    for entry in plan["layout"]:
        if entry["start"] <= index < entry["end"]:
            local = index - entry["start"]
            native_offset = _round_half_up(
                Fraction(local, rate) * entry["rate"]
            )
            return {
                "occurrence": entry["occurrence"],
                "name": entry["name"],
                "offset_samples": native_offset,
                "offset_ms": float(Fraction(local * 1000, rate)),
            }
    return None


# ---------------------------------------------------------------- 渲染

def _waveform(seg):
    """按段名生成确定性的伪噪声波形（同一段永远得到相同样本）。"""
    digest = hashlib.sha256(seg.name.encode("utf-8")).digest()
    state = int.from_bytes(digest[:8], "little")
    mask = (1 << 64) - 1
    out = []
    scale = 2.0 / (1 << 64)
    for _ in range(seg.samples):
        state = (state * 6364136223846793005 + 1442695040888963407) & mask
        out.append(state * scale - 1.0)
    return out


def _resample_linear(src, src_rate, out_rate, length):
    """线性插值重采样到输出采样率，输出恰好 length 个样本。"""
    n = len(src)
    out = []
    for j in range(length):
        pos = j * src_rate / out_rate
        i = int(math.floor(pos))
        if i >= n - 1:
            out.append(src[-1])
            continue
        frac = pos - i
        out.append(src[i] * (1.0 - frac) + src[i + 1] * frac)
    return out


def _quantize_int16(value):
    """浮点样本量化为 16-bit 有符号整数并限幅。"""
    q = int(round(value * 32767.0))
    if q > 32767:
        return 32767
    if q < -32768:
        return -32768
    return q


def render_timeline(segments, plan):
    """按 plan 拼接采样，返回 ``{"rate", "samples", "total_samples"}``。

    纯函数：同样的输入连续渲染两次，输出逐字节一致。
    """
    rate = plan["rate"]
    total_samples = plan["total_samples"]
    layout = plan["layout"]

    if total_samples == 0 or not layout:
        return {"rate": rate, "samples": [], "total_samples": 0}

    mix = [0.0] * total_samples

    for k, entry in enumerate(layout):
        seg = segments[entry["occurrence"]]
        start, end = entry["start"], entry["end"]
        src = _waveform(seg)
        buf = _resample_linear(src, seg.rate, rate, end - start)

        # 与前一段的重叠区（本段淡入）和与后一段的重叠区（本段淡出）
        head_end = layout[k - 1]["end"] if k > 0 else start
        tail_start = layout[k + 1]["start"] if k + 1 < len(layout) else end

        for j, value in enumerate(buf):
            abs_index = start + j
            gain = 1.0

            if abs_index < head_end:  # 淡入：sin
                overlap_len = head_end - start
                theta = (math.pi / 2.0) * (j + 0.5) / overlap_len
                gain *= math.sin(theta)

            if abs_index >= tail_start:  # 淡出：cos
                overlap_len = end - tail_start
                p = j - (tail_start - start)
                theta = (math.pi / 2.0) * (p + 0.5) / overlap_len
                gain *= math.cos(theta)

            mix[abs_index] += value * gain

    samples = [_quantize_int16(v) for v in mix]
    return {
        "rate": rate,
        "samples": samples,
        "total_samples": len(samples),
    }
