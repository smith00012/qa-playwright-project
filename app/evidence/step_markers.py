from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


MARKER_COLOR = (230, 81, 0, 255)
MARKER_TEXT = (255, 255, 255, 255)
BOX_PADDING = 4
BADGE_SIZE = 30


def _font(size: int):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_step_markers(
    source: Path,
    target: Path,
    markers: list[tuple[int, dict]],
    viewport_width: int | None,
) -> Path:
    """
    Copia la captura y dibuja un recuadro con el número de cada paso.

    Las cajas llegan en píxeles CSS (bounding_box de Playwright); se
    escalan al tamaño real de la imagen, que en móviles es mayor por
    la densidad de pantalla.
    """

    image = Image.open(source).convert("RGBA")
    scale = image.width / viewport_width if viewport_width else 1
    draw = ImageDraw.Draw(image)
    line_width = max(3, round(2 * scale))
    badge = round(BADGE_SIZE * max(scale, 1))
    font = _font(round(badge * 0.55))

    for number, box in markers:
        left = max(0, (box["x"] - BOX_PADDING) * scale)
        top = max(0, (box["y"] - BOX_PADDING) * scale)
        right = min(image.width - 1, (box["x"] + box["width"] + BOX_PADDING) * scale)
        bottom = min(image.height - 1, (box["y"] + box["height"] + BOX_PADDING) * scale)
        if right <= left or bottom <= top:
            continue

        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=round(6 * scale),
            outline=MARKER_COLOR,
            width=line_width,
        )

        # El número va a la izquierda del recuadro para no tapar la etiqueta;
        # si no hay espacio, sobre la esquina superior izquierda.
        gap = round(6 * scale)
        if left - badge - gap >= 0:
            badge_left = left - badge - gap
            badge_top = (top + bottom - badge) / 2
        else:
            badge_left = max(0, left - badge / 2)
            badge_top = top - badge / 2
        badge_left = min(badge_left, image.width - badge)
        badge_top = min(max(0, badge_top), image.height - badge)
        draw.ellipse(
            (badge_left, badge_top, badge_left + badge, badge_top + badge),
            fill=MARKER_COLOR,
        )
        draw.text(
            (badge_left + badge / 2, badge_top + badge / 2),
            str(number),
            font=font,
            fill=MARKER_TEXT,
            anchor="mm",
        )

    image.convert("RGB").save(target)
    return target
