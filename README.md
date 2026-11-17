# 无缝拼接时间轴

一个能把多段音频拼成连续时间轴、并给出精确接缝坐标的拼接器

## 保留签名

- `Segment`
- `load_playlist(path)`
- `plan_timeline(segments, overlap_ms)`
- `render_timeline(segments, plan)`

## 自测

```
python -m pytest tests -q
python -m gapless.cli data/playlist.json --out grid.json
```
