#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 OCR 结果里挑「主要信息」，压制地图、截图里的大量次要标签。

为什么不写关键词规则：地图/截图千变万化，规则永远补不完。
换思路——主信息在视觉上必然是「离群」的那一个：字号更大、颜色更艳、笔画更粗、
或者紧挨着唯一的彩色标记点（图钉/选中高亮）。所以对「文字样式」而非「文字内容」
做离群度打分，基准取本图中位数，同一套参数可跨图复用。

依赖：同目录 ocr_image.py；pip install rapidocr onnxruntime pillow numpy

用法：
    from ocr_primary import ocr_primary

    ranked = ocr_primary("map.png", top_k=3)
    print("主信息：", ranked[0]["text"])
    for r in ranked:
        print(r["score"], r["text"], r["signals"])   # signals 用来看是哪一维起的作用
"""

from pathlib import Path
import re
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from .local_ocr import ocr_image

# 权重和为 1。有彩色标记点时，就近程度最重要；没有时退回「大 + 艳」为主的纯样式判断。
_WEIGHTS_MARKER = {"size": 0.34, "vivid": 0.24, "bold": 0.10, "anchor": 0.26, "center": 0.06}
_WEIGHTS_PLAIN = {"size": 0.45, "vivid": 0.33, "bold": 0.12, "center": 0.10}

# 每个维度「差别小于多少就不算信号」。防止全图文字大小一致时，把 10% 的抖动放大成 1.0
# size 用「相对本图中位高度的倍数」，所以门槛是比例而非像素，换分辨率不用改
_SPAN_FLOOR = {"size": 0.15, "vivid": 0.05, "bold": 0.10, "anchor": 0.05, "center": 0.05}

_LUM = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _load_rgb(path: Path) -> np.ndarray:
    """按 ocr_image 相同的方式解码（EXIF 纠正 + 透明铺白），否则 box 坐标对不上。"""
    with Image.open(path) as src:
        img = ImageOps.exif_transpose(src)
        if "A" in img.getbands() or "transparency" in img.info:
            rgba = img.convert("RGBA")
            img = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba)
        return np.asarray(img.convert("RGB"))


def _find_marker(rgb: np.ndarray) -> tuple[float, float] | None:
    """定位图上唯一的饱和色块（地图图钉、选中高亮框），找不到返回 None。

    双重门槛防止误判：彩色像素占比要在 0.08%~10% 之间，且空间上足够紧凑。
    否则一张到处是公园绿地、蓝色水系的地图会把锚点算到画面正中间。
    """
    h, w = rgb.shape[:2]
    # 长边降到 ~320：再低会把十几像素的图钉采样没了，再高则统计变慢
    step = max(1, max(w, h) // 320)
    small = rgb[::step, ::step].astype(np.float32)
    hi = small.max(axis=2)
    chroma = hi - small.min(axis=2)
    lum = small @ _LUM
    sat = chroma / (hi + 1e-6)
    mask = (sat > 0.45) & (chroma > 60) & (lum > 40)   # 排除深色文字与灰阶道路
    count = int(mask.sum())
    # 用绝对像素数当下限：十几个像素的图钉要能过，一两个杂点要挡住
    if count < 12 or count > mask.size * 0.10:
        return None
    ys, xs = np.nonzero(mask)
    sh, sw = small.shape[:2]
    if xs.std() > sw * 0.22 or ys.std() > sh * 0.22:
        return None                          # 彩色像素散落全图，不构成「一个标记点」
    return float(xs.mean() * step), float(ys.mean() * step)


def _signals(crop: np.ndarray) -> tuple[float, float]:
    """算一个文字框的 (鲜艳度, 笔画占比)，均归一到 0~1 量级。

    鲜艳度只认两种「是标注刻意做出来的」颜色，不算底图自带的颜色：
      1. 彩色笔画的字（红色/橙色地名）；
      2. 高亮底色——框内圈比四周边框更艳，说明底图本身没这个色块，是选中效果。
    这样落在绿地/蓝色水系上的普通灰字不会被误判成高亮。
    """
    h, w = crop.shape[:2]
    flat = crop.reshape(-1, 3).astype(np.float32)
    lum = flat @ _LUM
    chroma = flat.max(axis=1) - flat.min(axis=1)

    bg_lum = float(np.median(lum))
    ink = np.abs(lum - bg_lum) > 25          # 与背景亮度差 25 以上算笔画像素
    bold = float(ink.mean())                 # 粗体 / 大字在框内墨迹占比更高
    if not ink.any():
        return 0.0, bold

    # 彩色笔画：只看笔画核心（与背景亮度差 60 以上），避开抗锯齿边缘——
    # 灰字压在绿地上时，边缘混合像素会带出十几点色度，够把噪声放大成信号。
    core = np.abs(lum - bg_lum) > 60
    if int(core.sum()) < 5:
        core = ink
    ink_chroma = float(np.median(chroma[core])) / 128.0

    # 高亮底色：内圈（非笔画）比最外一圈更艳，且本身足够饱和
    highlight = 0.0
    if h >= 6 and w >= 6:
        ring = np.zeros((h, w), dtype=bool)
        r = max(1, min(h, w) // 6)
        ring[:r, :] = ring[-r:, :] = ring[:, :r] = ring[:, -r:] = True
        ring_flat = ring.reshape(-1)
        inner = ~ring_flat & ~ink
        if inner.any():
            inner_chroma = float(np.median(chroma[inner]))
            ring_chroma = float(np.median(chroma[ring_flat]))
            # 双门槛：地图上的河湖绿地是中低饱和（chroma 40~70），真高亮底色才够艳
            if inner_chroma > 70 and inner_chroma - ring_chroma > 40:
                highlight = (inner_chroma - ring_chroma) / 128.0

    return min(max(ink_chroma, highlight), 1.0), bold


def _norm(values: list[float], floor: float) -> list[float]:
    """图内极差归一化；极差小于 floor 说明这一维全图没差别，直接给 0。"""
    lo, hi = min(values), max(values)
    if hi - lo < floor:
        return [0.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def ocr_primary(
    image_path: str | Path,
    *,
    top_k: int = 3,
    exclude: str | None = None,
    roi: tuple[int, int, int, int] | None = None,
    min_height_ratio: float = 0.0,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """识别图片文字并按「是主要信息的可能性」降序返回，用第一条即可。

    Args:
        image_path: 本地图片路径，同 ocr_image 的限制。
        top_k: 返回前 K 个候选，用于人工核对或交给下游模型二次判断。
        exclude: 正则，丢弃命中文本的行，例如图例噪声 ``r"图例|比例尺|千米"``。
        roi: 只看这块区域 (x1, y1, x2, y2)，按框中心判断；已知主信息大概位置时最省事。
        min_height_ratio: 丢弃高度小于「本图中位高度 × 该值」的行。
            密集地图建议 1.1~1.3，只留下明显更大的标注；默认 0 表示不过滤。
        weights: 自定义维度权重，键取自 size/vivid/bold/anchor/center，会按总和归一。

    Returns:
        list[dict]: 每项含 text、score（0~1）、confidence、height、box、signals。
        signals 是该行各维度归一后的得分，用来解释「凭什么排第一」。
        score 可当置信度用：>0.4 说明主次分明，<0.15 说明全图文字一个样式、
        这张图大概率没有「突出显示」的对象，此时别硬取第一名。

    Raises:
        FileNotFoundError: 图片不存在。
        ValueError: 参数非法（同 ocr_image）。
        ImportError: 未安装 rapidocr / onnxruntime。

    Example:
        >>> ranked = ocr_primary("map.png")
        >>> ranked[0]["text"], ranked[0]["score"]
        ('西湖景区', 0.746)
        >>> ocr_primary("map.png", min_height_ratio=1.2)[0]["text"]   # 更激进地丢掉小字
        '西湖景区'
    """
    if top_k < 1:
        raise ValueError("top_k 至少为 1")

    detail = ocr_image(image_path, with_details=True)
    lines = detail["lines"]
    if not lines:
        return []

    path = Path(image_path).expanduser().resolve()
    rgb = _load_rgb(path)
    height, width = rgb.shape[:2]
    marker = _find_marker(rgb)
    pattern = re.compile(exclude) if exclude else None
    diag = (width**2 + height**2) ** 0.5

    heights = [max(1.0, line["box"][2][1] - line["box"][0][1]) for line in lines]
    median_h = float(np.median(heights))

    rows: list[dict[str, Any]] = []
    for line, box_h in zip(lines, heights, strict=True):
        if pattern and pattern.search(line["text"]):
            continue
        if min_height_ratio > 0 and box_h < median_h * min_height_ratio:
            continue

        xs = [p[0] for p in line["box"]]
        ys = [p[1] for p in line["box"]]
        x1, x2 = max(0, int(min(xs))), min(width, int(max(xs)))
        y1, y2 = max(0, int(min(ys))), min(height, int(max(ys)))
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue

        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if roi and not (roi[0] <= cx <= roi[2] and roi[1] <= cy <= roi[3]):
            continue

        vivid, bold = _signals(rgb[y1:y2, x1:x2])
        raw = {
            "size": box_h / median_h,          # 相对本图典型字号，抗分辨率变化
            "vivid": vivid,
            "bold": bold,
            # 距离标记点越近越高；超出对角线 35% 就按 0 处理
            "anchor": (1.0 - min(1.0, (((cx - marker[0]) ** 2 + (cy - marker[1]) ** 2) ** 0.5) / (diag * 0.35)))
            if marker else 0.0,
            "center": 1.0 - min(1.0, (((cx - width / 2) ** 2 + (cy - height / 2) ** 2) ** 0.5) / (diag / 2)),
        }
        rows.append({"line": line, "height": box_h, "raw": raw})

    if not rows:
        return []

    keys = list((weights or (_WEIGHTS_MARKER if marker else _WEIGHTS_PLAIN)).keys())
    norms = {k: _norm([r["raw"][k] for r in rows], _SPAN_FLOOR[k]) for k in keys}
    effective = dict(weights or (_WEIGHTS_MARKER if marker else _WEIGHTS_PLAIN))
    total_w = sum(effective.values()) or 1.0

    for i, row in enumerate(rows):
        row["score"] = round(sum(effective[k] * norms[k][i] for k in keys) / total_w, 4)
        row["signals"] = {k: round(norms[k][i], 3) for k in keys}

    rows.sort(key=lambda r: (-r["score"], -r["line"]["confidence"]))
    return [
        {
            "text": r["line"]["text"],
            "score": r["score"],
            "confidence": r["line"]["confidence"],
            "height": round(float(r["height"]), 1),
            "box": r["line"]["box"],
            "signals": r["signals"],
        }
        for r in rows[:top_k]
    ]
