# -*- coding: utf-8 -*-
"""无缝拼接时间轴"""

from dataclasses import dataclass


@dataclass
class Segment:
    """一段音频：路径、采样率、采样数。"""
    name: str
    rate: int
    samples: int


def load_playlist(path):
    """读播放清单"""
    raise NotImplementedError("load_playlist 尚未实现")


def plan_timeline(segments, overlap_ms):
    """算出每段起点与接缝坐标"""
    raise NotImplementedError("plan_timeline 尚未实现")


def render_timeline(segments, plan):
    """按计划拼接采样"""
    raise NotImplementedError("render_timeline 尚未实现")

