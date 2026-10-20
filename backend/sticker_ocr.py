"""Чтение двух чисел со стикера WB по фото (инференс), не QR."""
from __future__ import annotations

import base64
import io
import json
import os
import re

from .qwen_client import QwenClient, QwenError

MAX_BYTES = 3_500_000
MAX_SIDE = 1280
SYSTEM = (
    "На фото этикетка Wildberries. Нужны только два печатных числа справа "
    "от большого QR: верхнее 7–9 цифр и нижнее 4 цифры. "
    "Игнорируй QR, TRBX, зоны из 1–3 цифр и полоски штрихкода. "
    "Ответ строго JSON: {\"part_a\":\"5690311\",\"part_b\":\"8348\"} "
    "или {\"code\":\"56903118348\"}. Если чисел нет — {\"code\":\"\"}."
)


def vl_model_name() -> str:
    env = (os.getenv("QWEN_VL_MODEL") or "").strip()
    if env:
        return env
    current = (os.getenv("QWEN_MODEL") or "").strip()
    if "vl" in current.lower():
        return current
    return "qwen-vl-plus"


def parse_sticker_ocr(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    blob = s
    if "```" in blob:
        blob = re.sub(r"^```(?:json)?\s*", "", blob.strip(), flags=re.I)
        blob = re.sub(r"\s*```$", "", blob)
    start, end = blob.find("{"), blob.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(blob[start:end + 1])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            code = str(data.get("code") or "").strip()
            if code:
                return _glue(code)
            a = re.sub(r"\D", "", str(data.get("part_a") or data.get("partA") or ""))
            b = re.sub(r"\D", "", str(data.get("part_b") or data.get("partB") or ""))
            if a and b:
                return a + b
    nums = re.findall(r"\d{4,14}", s)
    seven = [n for n in nums if 7 <= len(n) <= 9]
    four = [n for n in nums if len(n) == 4]
    if seven and four:
        return seven[0] + four[0]
    glued = [n for n in nums if 11 <= len(n) <= 14]
    if glued:
        return sorted(glued, key=len, reverse=True)[0]
    digits = re.sub(r"\D", "", s)
    return digits if len(digits) >= 7 else ""


def _glue(raw: str) -> str:
    parts = re.findall(r"\d+", raw)
    if len(parts) == 2:
        return parts[0] + parts[1]
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) >= 7 else ""


def jpeg_data_url(raw: bytes, content_type: str = "") -> str:
    from PIL import Image

    if len(raw) > MAX_BYTES:
        raise ValueError("too_large")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:
        raise ValueError("not_image") from exc
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, MAX_SIDE / max(w, h, 1))
    if scale < 1:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=72, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def read_sticker_from_image(raw: bytes, content_type: str = "") -> str:
    url = jpeg_data_url(raw, content_type)
    text = QwenClient(model=vl_model_name(), timeout=45).chat_sync(
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": url}},
                {"type": "text", "text": "Какие два числа напечатаны на стикере WB справа от QR?"},
            ],
        }],
        temperature=0,
        max_tokens=80,
        mask_user_text=False,
    )
    if isinstance(text, list):
        text = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in text
        )
    return parse_sticker_ocr(str(text or ""))
