"""
模块名称:  local_ocr.py
所属项目:  job_search_agent
创建时间:  2026/11/6 23:14
作者:      刘双喜 <1604030114@qq.com>
功能描述:
"""
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单函数本地图片文字识别（RapidOCR + ONNXRuntime，纯 CPU，图片不出本机）。

依赖：pip install rapidocr onnxruntime     # Pillow/numpy 会随 rapidocr 一起装上
要求：Python >= 3.10（用到了 zip(strict=) 与 str | Path 语法）

用法：
    from ocr_image import ocr_image
    print(ocr_image("screenshot.png"))
"""

from pathlib import Path
import threading
from typing import Any
import warnings

from PIL import Image, ImageOps

# 进程级单例：ONNX session 构建一次约几百毫秒，不能每次调用都重建
_ENGINE: Any = None
_LOCK = threading.Lock()


def _map_target_lines(image: Image.Image, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在整图找下窄上宽的红色图钉，再找正上方包含 OCR 文字的白色气泡。"""
    import cv2
    import numpy as np

    if not lines:
        return []
    # 形状检测使用缩略图，返回的 OCR 行保留原图坐标。
    preview = ImageOps.contain(image, (min(1600, image.width), min(1600, image.height)))
    rgb = np.asarray(preview)
    scale_x, scale_y = preview.width / image.width, preview.height / image.height
    red = cv2.inRange(rgb, (155, 0, 0), (255, 150, 145))
    white = cv2.inRange(rgb, (249, 249, 249), (255, 255, 255))
    contours, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    matches = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < 5 or not 1.1 * w <= h <= 2.8 * w:
            continue
        pin = red[y:y + h, x:x + w]
        quarter = max(1, h // 4)
        if np.count_nonzero(pin[-quarter:]) >= np.count_nonzero(pin[:quarter]):
            continue

        # 气泡可以随图钉移动。用相对于图钉尺寸的窗口查找，开运算断开白色细路。
        left, top = max(0, x - 25 * w), max(0, y - 8 * h)
        area = white[top:y + h, left:x + 26 * w]
        k = max(3, round(h / 2) | 1)
        opened = cv2.morphologyEx(area, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
        regions = cv2.connectedComponentsWithStats(opened)[2][1:]
        for bx, by, bw, bh, count in regions:
            bx, by = left + int(bx), top + int(by)
            if bw < 2 * w or not w <= bh <= 6 * h or count < bw * bh * 0.5:
                continue
            if abs((bx + bw / 2) - (x + w / 2)) > h or not -quarter <= y - (by + bh) <= h:
                continue
            selected = []
            for line in lines:
                box = line['box']
                cx = scale_x * sum(point[0] for point in box) / len(box)
                cy = scale_y * sum(point[1] for point in box) / len(box)
                if bx <= cx < bx + bw and by <= cy < by + bh:
                    selected.append(line)
            if selected:
                matches.append(selected)
    return matches[0] if len(matches) == 1 else []


def ocr_image(
    image_path: str | Path,
    *,
    min_confidence: float = 0.9,
    with_details: bool = False,
    map_target_only: bool = False,
) -> str | dict[str, Any]:
    """在本地识别图片中的中文、英文与数字，返回全文（或含坐标的明细）。

    Args:
        image_path: 本地图片路径，支持 PNG/JPEG/WebP/BMP/TIFF；不传 URL 或 data URL。
        min_confidence: 文字行最低置信度（0~1），低于该值的行被丢弃，默认 0.9。
        with_details: 为 True 时返回 dict（全文 + 逐行置信度 + 四角坐标），默认只返回全文。
        map_target_only: 为 True 时只返回红色图钉上方白色气泡内的文字；没有唯一匹配时返回空结果。

    Returns:
        str: 全文，行间以 "\\n" 分隔；图片无文字时返回空字符串 ""。
        dict: {"text": str, "lines": [{"text": str, "confidence": float, "box": [[x, y] * 4]}],
               "width": int, "height": int}，坐标为左上、右上、右下、左下，单位像素。

    Raises:
        FileNotFoundError: 路径不存在或不是文件。
        ValueError: min_confidence 越界、文件超过 48 MiB、像素超过 4000 万或多帧图片。
        ImportError: 未安装 rapidocr / onnxruntime。
        RuntimeError: 模型加载或推理失败。

    Example:
        >>> ocr_image("invoice.png")
        '发票金额：128.00'
        >>> detail = ocr_image("invoice.png", min_confidence=0.3, with_details=True)
        >>> detail["lines"][0]["box"][0]        # 首行左上角坐标 (x, y)
    """
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence 必须在 0 到 1 之间")

    max_bytes = 48 * 1024 * 1024   # 单文件上限，挡住误传的视频、PSD 等大文件
    max_pixels = 40_000_000        # 解码后像素上限，防 OOM 与解压炸弹

    path = Path(image_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"图片不存在或不是文件：{path}")
    path = path.resolve()
    if path.stat().st_size > max_bytes:
        raise ValueError("图片超过 48 MiB，请先压缩")

    # 解码两个坑：手机照片需按 EXIF 纠正方向；透明 PNG 必须铺白底，
    # 直接 RGBA→RGB 会把透明区变黑底白字，识别率骤降。
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as source:
            if source.width * source.height > max_pixels:
                raise ValueError("图片像素超过 4000 万，请先缩小")
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("仅支持单帧图片，请先拆分动图或多页文件")
            image = ImageOps.exif_transpose(source)
            if "A" in image.getbands() or "transparency" in image.info:
                rgba = image.convert("RGBA")
                white = Image.new("RGBA", rgba.size, "white")
                image = Image.alpha_composite(white, rgba).convert("RGB")
            else:
                image = image.convert("RGB")

    global _ENGINE
    engine = _ENGINE
    if engine is None:
        # 双重检查锁：被 FastAPI 线程池并发首次调用时，只建一个 session
        with _LOCK:
            engine = _ENGINE
            if engine is None:
                try:
                    import onnxruntime
                    import rapidocr
                except ImportError as exc:  # 延迟导入，未用 OCR 时不影响模块 import
                    raise ImportError("请先安装依赖：pip install rapidocr onnxruntime") from exc

                onnxruntime.disable_telemetry_events()
                params: dict[str, Any] = {
                    "Global.log_level": "warning",
                    "Det.limit_type": "max",
                    "Det.limit_side_len": 2048,   # 长边上限：兼顾小字召回与 CPU 耗时
                    "EngineConfig.onnxruntime.intra_op_num_threads": 4,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                }
                # 优先用包内自带的 small 模型（体积小、CPU 快）；版本升级改名则回退库默认模型
                model_dir = Path(rapidocr.__file__).parent / "models"
                pinned = {
                    "Det": "PP-OCRv6_det_small.onnx",
                    "Cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
                    "Rec": "PP-OCRv6_rec_small.onnx",
                }
                if all((model_dir / name).is_file() for name in pinned.values()):
                    for task, name in pinned.items():
                        params[f"{task}.model_path"] = str(model_dir / name)

                try:
                    engine = _ENGINE = rapidocr.RapidOCR(params=params)
                except Exception as exc:
                    raise RuntimeError("OCR 引擎初始化失败，请检查 rapidocr/onnxruntime 安装") from exc

    try:
        output = engine(image, text_score=min_confidence)
    except Exception as exc:
        raise RuntimeError("OCR 推理失败") from exc

    lines: list[dict[str, Any]] = []
    if output.txts is not None:
        # strict=True：texts/scores/boxes 必须等长，否则宁可报错也不静默错位
        for text, score, box in zip(output.txts, output.scores, output.boxes, strict=True):
            lines.append({
                "text": text,
                "confidence": round(float(score), 4),
                "box": [[round(float(x), 1), round(float(y), 1)] for x, y in box],
            })

    if map_target_only:
        lines = _map_target_lines(image, lines)
    full_text = "\n".join(line["text"] for line in lines)
    if not with_details:
        return full_text
    return {
        "text": full_text,
        "lines": lines,
        "width": image.width,
        "height": image.height,
    }
