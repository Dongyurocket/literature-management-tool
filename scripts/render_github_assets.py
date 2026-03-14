from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SCREENSHOTS = DOCS / "screenshots"


def load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def vertical_gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, top)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    return image


def draw_soft_glow(image: Image.Image, box: tuple[int, int, int, int], color: tuple[int, int, int], blur: int) -> None:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.ellipse(box, fill=color + (120,))
    overlay = overlay.filter(ImageFilter.GaussianBlur(blur))
    image.alpha_composite(overlay)


def rounded_card(
    base: Image.Image,
    image: Image.Image,
    box: tuple[int, int, int, int],
    radius: int = 28,
    shadow_offset: tuple[int, int] = (0, 18),
) -> None:
    x0, y0, x1, y1 = box
    width = x1 - x0
    height = y1 - y0

    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    sx, sy = shadow_offset
    shadow_draw.rounded_rectangle(
        (x0 + sx, y0 + sy, x1 + sx, y1 + sy),
        radius=radius,
        fill=(8, 18, 32, 105),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    base.alpha_composite(shadow)

    resized = image.copy()
    resized.thumbnail((width, height))
    scale = max(width / image.width, height / image.height)
    crop_width = int(width / scale)
    crop_height = int(height / scale)
    left = max(0, (image.width - crop_width) // 2)
    top = max(0, (image.height - crop_height) // 2)
    resized = image.crop((left, top, left + crop_width, top + crop_height)).resize((width, height))

    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, width, height), radius=radius, fill=255)
    card = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    card.paste(resized.convert("RGBA"), (0, 0), mask)

    border = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    border_draw = ImageDraw.Draw(border)
    border_draw.rounded_rectangle(
        (1, 1, width - 2, height - 2),
        radius=radius,
        outline=(255, 255, 255, 120),
        width=2,
    )
    card.alpha_composite(border)

    base.alpha_composite(card, dest=(x0, y0))


def draw_badge(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    fill: tuple[int, int, int, int],
    text_fill: tuple[int, int, int],
    padding: tuple[int, int] = (18, 10),
    radius: int = 20,
) -> tuple[int, int, int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0] + padding[0] * 2
    height = bbox[3] - bbox[1] + padding[1] * 2
    x, y = xy
    draw.rounded_rectangle((x, y, x + width, y + height), radius=radius, fill=fill)
    draw.text((x + padding[0], y + padding[1] - 2), text, font=font, fill=text_fill)
    return (x, y, x + width, y + height)


def draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    max_width: int,
    line_spacing: int = 10,
) -> int:
    x, y = xy
    lines: list[str] = []
    current = ""
    for char in text:
        trial = current + char
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)

    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line, font=font)
        y += bbox[3] - bbox[1] + line_spacing
    return y


def add_panel_label(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    subtitle: str,
) -> None:
    x0, y0, x1, _y1 = box
    width = x1 - x0
    overlay_box = (x0 + 20, y0 + 20, x0 + min(width - 20, 290), y0 + 94)
    draw.rounded_rectangle(overlay_box, radius=18, fill=(10, 20, 34, 170))
    draw.text((overlay_box[0] + 18, overlay_box[1] + 14), title, font=load_font(24, bold=True), fill=(255, 255, 255))
    draw.text((overlay_box[0] + 18, overlay_box[1] + 50), subtitle, font=load_font(15), fill=(199, 213, 228))


def make_social_preview() -> None:
    main = Image.open(SCREENSHOTS / "main-overview.png").convert("RGB")
    editor = Image.open(SCREENSHOTS / "literature-editor.png").convert("RGB")
    settings = Image.open(SCREENSHOTS / "settings-dialog.png").convert("RGB")

    canvas = vertical_gradient((1280, 640), (6, 18, 36), (21, 73, 112)).convert("RGBA")
    draw_soft_glow(canvas, (760, -60, 1240, 360), (36, 181, 214), 70)
    draw_soft_glow(canvas, (-100, 360, 340, 760), (70, 98, 255), 80)
    draw = ImageDraw.Draw(canvas)

    draw.text((84, 86), "Literature management tool", font=load_font(52, bold=True), fill=(255, 255, 255))
    draw.text((88, 148), "Local-first Qt reference manager for Windows", font=load_font(24), fill=(202, 222, 239))
    draw_wrapped_text(
        draw,
        (88, 205),
        "面向本地文献管理场景，支持 GB/T 7714 元数据、BibTeX 导出、PDF 批量重命名、docx 笔记与 Setup.exe 安装版。",
        load_font(26),
        (235, 244, 252),
        510,
        line_spacing=12,
    )

    badges = [
        "GB/T 7714",
        "BibTeX / CSL JSON",
        "PDF Rename",
        "docx Notes",
        "PySide6 / Qt",
    ]
    bx = 88
    by = 365
    for index, badge in enumerate(badges):
        box = draw_badge(
            draw,
            (bx, by),
            badge,
            load_font(16, bold=True),
            fill=(255, 255, 255, 42),
            text_fill=(245, 249, 253),
        )
        bx = box[2] + 14
        if index == 1:
            bx = 88
            by = box[3] + 14

    rounded_card(canvas, main, (698, 74, 1210, 390), radius=30)
    rounded_card(canvas, editor, (756, 300, 1088, 584), radius=26)
    rounded_card(canvas, settings, (1078, 336, 1232, 520), radius=24, shadow_offset=(0, 12))
    overlay = ImageDraw.Draw(canvas)
    add_panel_label(overlay, (698, 74, 1210, 390), "Main workspace", "Navigation, grid, notes")
    add_panel_label(overlay, (756, 300, 1088, 584), "Metadata editor", "GB/T 7714 + custom fields")
    add_panel_label(overlay, (1078, 336, 1232, 520), "Settings", "PDF reader + library root")

    canvas.convert("RGB").save(DOCS / "social-preview.png", quality=95)


def make_readme_hero() -> None:
    main = Image.open(SCREENSHOTS / "main-overview.png").convert("RGB")
    editor = Image.open(SCREENSHOTS / "literature-editor.png").convert("RGB")
    settings = Image.open(SCREENSHOTS / "settings-dialog.png").convert("RGB")

    canvas = vertical_gradient((1600, 920), (245, 249, 252), (224, 236, 245)).convert("RGBA")
    draw_soft_glow(canvas, (1030, -40, 1570, 400), (49, 130, 206), 80)
    draw_soft_glow(canvas, (-120, 590, 420, 1020), (73, 180, 167), 90)
    draw = ImageDraw.Draw(canvas)

    draw.rounded_rectangle((48, 38, 1552, 882), radius=34, fill=(255, 255, 255, 196), outline=(255, 255, 255, 160), width=2)
    draw.text((90, 88), "Literature management tool", font=load_font(54, bold=True), fill=(18, 46, 76))
    draw.text((92, 154), "Windows 本地优先的 Qt 文献管理工具", font=load_font(28, bold=True), fill=(23, 89, 139))
    draw_wrapped_text(
        draw,
        (92, 206),
        "覆盖 GB/T 7714 元数据、BibTeX / CSL JSON 导出、PDF 批量重命名、docx 笔记、自定义 PDF 阅读器，以及导入 / 查重 / 备份恢复等完整桌面流程。",
        load_font(24),
        (56, 84, 112),
        650,
    )

    meta = [
        "Qt-only GUI",
        "Background tasks",
        "Setup.exe release",
    ]
    bx = 92
    by = 348
    for badge in meta:
        box = draw_badge(
            draw,
            (bx, by),
            badge,
            load_font(17, bold=True),
            fill=(32, 98, 172, 230),
            text_fill=(255, 255, 255),
        )
        bx = box[2] + 12

    rounded_card(canvas, main, (740, 86, 1478, 480), radius=28)
    rounded_card(canvas, editor, (756, 496, 1138, 826), radius=26)
    rounded_card(canvas, settings, (1160, 540, 1468, 778), radius=24)

    overlay = ImageDraw.Draw(canvas)
    add_panel_label(overlay, (740, 86, 1478, 480), "Main workspace", "Import, search, dedupe, maintenance")
    add_panel_label(overlay, (756, 496, 1138, 826), "Metadata editor", "GB/T 7714 + subject / keywords / summary")
    add_panel_label(overlay, (1160, 540, 1468, 778), "Settings", "Library root, import mode, PDF reader")

    highlight_boxes = [
        ("拖拽导入", "支持文件 / 文件夹拖拽，后台扫描导入", (96, 478, 520, 610)),
        ("查重与维护", "查重合并、路径修复、备份恢复、索引重建", (96, 632, 520, 764)),
        ("文献与笔记", "原文、翻译、补充材料、docx / md / txt 笔记", (540, 632, 912, 764)),
    ]
    for title, body, box in highlight_boxes:
        draw.rounded_rectangle(box, radius=24, fill=(255, 255, 255, 228), outline=(189, 214, 236), width=2)
        draw.text((box[0] + 20, box[1] + 18), title, font=load_font(24, bold=True), fill=(15, 77, 122))
        draw_wrapped_text(draw, (box[0] + 20, box[1] + 54), body, load_font(18), (75, 95, 116), box[2] - box[0] - 40, line_spacing=8)

    canvas.convert("RGB").save(SCREENSHOTS / "github-home-v023.png", quality=95)


def make_release_overview() -> None:
    main = Image.open(SCREENSHOTS / "main-overview.png").convert("RGB")
    editor = Image.open(SCREENSHOTS / "literature-editor.png").convert("RGB")
    settings = Image.open(SCREENSHOTS / "settings-dialog.png").convert("RGB")

    canvas = vertical_gradient((1600, 900), (8, 19, 38), (24, 72, 104)).convert("RGBA")
    draw_soft_glow(canvas, (980, 60, 1540, 520), (35, 169, 194), 80)
    draw_soft_glow(canvas, (-80, 560, 360, 980), (52, 93, 255), 90)
    draw = ImageDraw.Draw(canvas)

    draw.text((86, 82), "v0.2.3", font=load_font(30, bold=True), fill=(150, 222, 228))
    draw.text((86, 130), "Phase 4 Qt migration release", font=load_font(50, bold=True), fill=(255, 255, 255))
    draw_wrapped_text(
        draw,
        (90, 206),
        "旧版导入、查重、备份恢复、PDF 批量重命名等能力已完整迁到 Qt。拖拽导入与元数据抓取改为后台任务，并继续提供 Setup.exe 安装版。",
        load_font(24),
        (219, 231, 242),
        620,
        line_spacing=10,
    )

    features = [
        "Qt-only GUI",
        "Import / dedupe / maintenance",
        "PDF rename + preview",
        "docx notes + custom PDF reader",
        "Setup.exe verified",
    ]
    by = 360
    for feature in features:
        draw_badge(
            draw,
            (90, by),
            feature,
            load_font(18, bold=True),
            fill=(255, 255, 255, 38),
            text_fill=(244, 249, 252),
            padding=(18, 11),
            radius=18,
        )
        by += 58

    rounded_card(canvas, main, (760, 86, 1504, 476), radius=30)
    rounded_card(canvas, editor, (776, 500, 1154, 826), radius=26)
    rounded_card(canvas, settings, (1176, 548, 1494, 800), radius=24)

    overlay = ImageDraw.Draw(canvas)
    add_panel_label(overlay, (760, 86, 1504, 476), "Main workspace", "Phase 4 utilities integrated into Qt")
    add_panel_label(overlay, (776, 500, 1154, 826), "Metadata editor", "Fields for GB/T 7714 and research workflow")
    add_panel_label(overlay, (1176, 548, 1494, 800), "Settings", "Library root and custom PDF reader")

    canvas.convert("RGB").save(SCREENSHOTS / "release-v023-overview.png", quality=95)


def main() -> None:
    make_social_preview()
    make_readme_hero()
    make_release_overview()


if __name__ == "__main__":
    main()
