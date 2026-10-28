"""Account-scoped coupon classification and readable PNG exports."""
import io
import os
import re
import zipfile
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

from errors import ProviderError


CATEGORIES = frozenset(("8", "10", "15", "20", "30", "all"))
_AMOUNT = re.compile(r"(?<![\d.])(8|10|15|20|30)(?:\.0+)?\s*元(?![\d])")


def matches_category(coupon, category):
    if not isinstance(category, str) or category not in CATEGORIES:
        raise ProviderError("不支持的券面额分类。", 400, "invalid_category")
    return category == "all" or category in _AMOUNT.findall(coupon.get("title") or "")


def copy_text(rows):
    return "\n\n".join(f"{row['title']}\n账号：{row['account_phone']}\n{row['qr_payload']}" for row in rows)


def safe_name(value):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(value or "好礼券")).strip(" .")[:80] or "好礼券"


def _font(size):
    candidates = [os.environ.get("COUPON_FONT_FILE", ""),
                  "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
                  "/system/fonts/NotoSansCJK-Regular.ttc",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]
    for path in candidates:
        if path and Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise ProviderError("缺少中文字体，请设置 COUPON_FONT_FILE 后再导出图片。", 503, "export_font_missing")


def named_png(row):
    title_font, small_font = _font(40), _font(24)
    title = row.get("title") or "好礼券"
    width, margin = 960, 64
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines, current = [], ""
    for char in title:
        if current and probe.textlength(current + char, font=title_font) > width - margin * 2:
            lines.append(current)
            current = ""
        current += char
    lines.append(current)
    title_height = len(lines) * 60
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(row["qr_payload"])
    qr.make(fit=True)
    # Integer module scaling preserves sharp, scannable edges and quiet zones.
    pixels = max(4, 760 // (qr.modules_count + 8))
    qr.box_size = pixels
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    top = margin + title_height + 70
    canvas = Image.new("RGB", (width, top + qr_image.height + 120), "white")
    draw = ImageDraw.Draw(canvas)
    for index, line in enumerate(lines):
        draw.text((width / 2, margin + index * 60), line, font=title_font, fill="#183d31", anchor="mt")
    draw.text((width / 2, margin + title_height + 10), "账号 " + row["account_phone"], font=small_font, fill="#607366", anchor="mt")
    canvas.paste(qr_image, ((width - qr_image.width) // 2, top))
    if row.get("expires_at"):
        draw.text((width / 2, canvas.height - 65), "有效期至 " + row["expires_at"], font=small_font, fill="#607366", anchor="mt")
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()


def image_download(rows, category):
    if len(rows) == 1:
        return named_png(rows[0]), "image/png", safe_name(rows[0]["title"]) + ".png"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, row in enumerate(rows, 1):
            name = f"{index:03d}_{safe_name(row['title'])}_{safe_name(row['account_phone'])}.png"
            archive.writestr(name, named_png(row))
    label = "全部" if category == "all" else category + "元"
    return buffer.getvalue(), "application/zip", "好礼券_" + label + ".zip"
