# -*- coding: utf-8 -*-
"""命令行入口：规划时间轴并渲染，结果写入 JSON 网格文件。"""

import argparse
import hashlib
import json

import gapless as m


def main():
    ap = argparse.ArgumentParser(description="无缝拼接时间轴")
    ap.add_argument("playlist", help="播放清单 JSON 路径")
    ap.add_argument("--out", required=True, help="输出网格 JSON 路径")
    ap.add_argument(
        "--overlap-ms",
        type=int,
        default=0,
        help="段间重叠毫秒数（默认 0）",
    )
    a = ap.parse_args()

    segments = m.load_playlist(a.playlist)
    plan = m.plan_timeline(segments, a.overlap_ms)
    rendered = m.render_timeline(segments, plan)

    # 渲染样本的字节级校验，用来验证“连续两次逐字节一致”
    packed = b"".join(
        int(v).to_bytes(2, byteorder="little", signed=True)
        for v in rendered["samples"]
    )

    grid = {
        "rate": plan["rate"],
        "segment_count": len(plan["layout"]),
        "total_ms": plan["total_ms"],
        "total_samples": plan["total_samples"],
        "max_error_ms": plan["max_error_ms"],
        "seams": plan["seams"],
        "layout": plan["layout"],
        "rendered_samples": rendered["total_samples"],
        "render_sha256": hashlib.sha256(packed).hexdigest(),
        "sample_preview": rendered["samples"][:16],
    }

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(grid, f, ensure_ascii=False, indent=2, sort_keys=True)

    print("rate          : %d Hz" % plan["rate"])
    print("segments      : %d (空段已跳过)" % len(plan["layout"]))
    print("total_ms      : %.3f" % plan["total_ms"])
    print("total_samples : %d" % plan["total_samples"])
    print("max_error_ms  : %.6f" % plan["max_error_ms"])
    for seam in plan["seams"]:
        print(
            "seam @ sample=%d  %.3f ms  %s -> %s  overlap=%d samples"
            % (
                seam["index"],
                seam["offset_ms"],
                seam["prev"]["name"],
                seam["next"]["name"],
                seam["overlap_samples"],
            )
        )
    print("written       : %s" % a.out)


if __name__ == "__main__":
    main()
