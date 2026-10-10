"""Liquid-glass rendering toolkit for Tkinter.

Tkinter has no per-pixel alpha for child widgets and no GPU. Real frosted glass
is therefore out of reach — but its *appearance* is not, and that is what this
module reconstructs from primitives:

* **Depth** — a vertical gradient rather than a flat fill, so panels look like
  they sit in front of something.
* **Translucency** — panel fills are computed by blending a tint into whatever
  is behind them, so "glass over the background" reads correctly even though
  the pixel is opaque.
* **Bevels** — a 1px lighter line along the top edge and a darker one along the
  bottom, which is how light catches a real sheet of glass.
* **Specular sweep** — a slow, soft highlight travelling across the hero.
* **Liquid** — soft blobs that drift, so the background is never static.

Everything is drawn on a single :class:`tkinter.Canvas`. Shapes are cheap;
gradients are drawn as stacked 1px lines, which is fast enough at panel sizes
and avoids a Pillow dependency entirely.
"""

from __future__ import annotations

import colorsys
import math
import tkinter as tk
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

RGB = Tuple[int, int, int]

#: The console palette. Deep space blues with cyan and violet accents.
#: The background and the panel fills are deliberately far apart in luminance
#: (roughly 18 vs 40): an earlier revision had them within a couple of levels,
#: which made the "glass" panels vanish into the backdrop.
BACKGROUND_TOP: RGB = (6, 9, 18)
BACKGROUND_BOTTOM: RGB = (11, 17, 33)
SURFACE: RGB = (26, 36, 60)
SURFACE_HIGH: RGB = (38, 51, 82)
BORDER: RGB = (64, 86, 128)
BEVEL_TOP: RGB = (128, 166, 222)
BEVEL_BOTTOM: RGB = (5, 8, 16)
ACCENT: RGB = (86, 214, 255)
ACCENT_WARM: RGB = (168, 130, 255)
ACCENT_GREEN: RGB = (96, 226, 176)
ACCENT_AMBER: RGB = (255, 198, 108)
ACCENT_RED: RGB = (255, 118, 138)
TEXT: RGB = (226, 236, 252)
TEXT_DIM: RGB = (134, 152, 184)
TEXT_FAINT: RGB = (86, 102, 132)


def to_hex(rgb: RGB) -> str:
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def from_hex(value: str) -> RGB:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def blend(base: RGB, tint: RGB, amount: float) -> RGB:
    """Mix *tint* into *base*. ``amount=0`` keeps base, ``1`` returns tint."""
    amount = max(0.0, min(1.0, amount))
    return tuple(  # type: ignore[return-value]
        int(round(b + (t - b) * amount)) for b, t in zip(base, tint)
    )


def lighten(rgb: RGB, amount: float) -> RGB:
    return blend(rgb, (255, 255, 255), amount)


def darken(rgb: RGB, amount: float) -> RGB:
    return blend(rgb, (0, 0, 0), amount)


def shift_hue(rgb: RGB, delta: float) -> RGB:
    """Rotate hue by *delta* (0-1). Used for the drifting liquid blobs."""
    r, g, b = (c / 255.0 for c in rgb)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h = (h + delta) % 1.0
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return (int(r * 255), int(g * 255), int(b * 255))


def background_at(y: float, height: float) -> RGB:
    """Sample the base gradient at height *y*.

    Blobs used to be composited against a single constant colour, which made
    them read as flat discs pasted on top. Blending against the real gradient at
    each blob's centre lets the light appear to *come from* the backdrop.
    """
    if height <= 0:
        return BACKGROUND_TOP
    ratio = min(1.0, max(0.0, y / height))
    return blend(BACKGROUND_TOP, BACKGROUND_BOTTOM, ratio)


def glow(tint: RGB, amount: float) -> RGB:
    """A tint that reads as emitted light against the dark background."""
    return blend(SURFACE, tint, amount)


def vignette(
    canvas: tk.Canvas,
    width: int,
    height: int,
    *,
    strength: float = 0.55,
    rings: int = 26,
    tags: Sequence[str] = (),
) -> None:
    """Darken the frame edges so the centre reads as lit and raised.

    Approximated with nested rectangles that get progressively darker toward
    the border. Each ring is drawn as a hollow frame, so the cost is
    proportional to the ring count rather than the pixel count.
    """
    for i in range(rings):
        ratio = i / rings
        amount = strength * ratio ** 2
        if amount <= 0.004:
            continue
        color = to_hex(darken(BACKGROUND_TOP, amount))
        inset = int(ratio * min(width, height) * 0.5)
        canvas.create_rectangle(
            inset, inset, width - inset, height - inset,
            outline=color, width=2, tags=tags,
        )


# --------------------------------------------------------------------------- #
# canvas primitives
# --------------------------------------------------------------------------- #
def vertical_gradient(
    canvas: tk.Canvas,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    top: RGB,
    bottom: RGB,
    *,
    tags: Sequence[str] = (),
    steps: Optional[int] = None,
) -> None:
    """Fill a rectangle with a smooth vertical gradient."""
    height = max(1, y1 - y0)
    steps = steps or height
    for i in range(steps):
        ratio = i / max(1, steps - 1)
        y = y0 + int(ratio * height)
        y_next = y0 + int((i + 1) / steps * height)
        color = blend(top, bottom, ratio)
        canvas.create_rectangle(
            x0, y, x1, max(y + 1, y_next), fill=to_hex(color), outline="", tags=tags
        )


def radial_blob(
    canvas: tk.Canvas,
    cx: float,
    cy: float,
    radius: float,
    tint: RGB,
    *,
    intensity: float = 0.5,
    rings: int = 14,
    base: Optional[RGB] = None,
    tags: Sequence[str] = (),
) -> None:
    """A soft circle of light — concentric rings fading outward.

    Drawn as overlapped ovals so the falloff looks like a blurred light source
    rather than a hard disc. *base* is the colour the light sits on; when
    omitted it falls back to :data:`SURFACE`.
    """
    under = base if base is not None else SURFACE
    for i in range(rings, 0, -1):
        ratio = i / rings
        r = radius * ratio
        # Falloff: brightest in the middle, quadratic toward the rim.
        amount = intensity * (1 - ratio) ** 2
        if amount <= 0.004:
            continue
        color = blend(under, tint, amount)
        canvas.create_oval(
            cx - r, cy - r * 0.72, cx + r, cy + r * 0.72,
            fill=to_hex(color), outline="", tags=tags,
        )


def rounded_rect(
    canvas: tk.Canvas,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    radius: float = 16,
    fill: str = "",
    outline: str = "",
    width: int = 1,
    tags: Sequence[str] = (),
) -> int:
    """A rounded rectangle via a smoothed polygon (no Pillow needed)."""
    r = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
    points = [
        x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
        x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
        x0, y1, x0, y1 - r, x0, y0 + r, x0, y0,
    ]
    return canvas.create_polygon(
        points, smooth=True, splinesteps=24,
        fill=fill or "", outline=outline or "", width=width, tags=tags,
    )


@dataclass
class GlassPanel:
    """Geometry + colour of one frosted panel."""

    x0: float
    y0: float
    x1: float
    y1: float
    tint: RGB = SURFACE_HIGH
    alpha: float = 0.55
    radius: float = 16


def draw_glass(
    canvas: tk.Canvas,
    panel: GlassPanel,
    *,
    tags: Sequence[str] = (),
    behind: RGB = BACKGROUND_BOTTOM,
    accent: Optional[RGB] = None,
) -> None:
    """Draw one frosted panel: body, sheen, border, bevels, edge glow.

    The body is not a flat fill. A soft vertical sheen (brighter at the top,
    falling away by about a third of the height) is overlaid so the panel reads
    as a curved sheet catching light from above. When *accent* is given, a
    hairline of that colour is drawn along the top edge — the visual cue that a
    panel is the active one.
    """
    body = blend(behind, panel.tint, panel.alpha)
    rounded_rect(
        canvas, panel.x0, panel.y0, panel.x1, panel.y1,
        radius=panel.radius, fill=to_hex(body), outline=to_hex(BORDER),
        width=1, tags=tags,
    )

    # Sheen: a few horizontal bands near the top, kept inside the corner curve.
    height = panel.y1 - panel.y0
    inset = panel.radius * 0.9
    sheen_depth = max(2, int(height * 0.32))
    for i in range(sheen_depth):
        ratio = i / sheen_depth
        amount = 0.075 * (1 - ratio) ** 2
        if amount <= 0.003:
            continue
        y = panel.y0 + inset * 0.5 + i
        canvas.create_line(
            panel.x0 + inset, y, panel.x1 - inset, y,
            fill=to_hex(lighten(body, amount)), width=1, tags=tags,
        )

    # Top bevel: a short bright line inset from the corners.
    canvas.create_line(
        panel.x0 + inset, panel.y0 + 1, panel.x1 - inset, panel.y0 + 1,
        fill=to_hex(accent or BEVEL_TOP), width=1, tags=tags,
    )
    # Bottom shade grounds the panel against the background.
    canvas.create_line(
        panel.x0 + inset, panel.y1 - 1, panel.x1 - inset, panel.y1 - 1,
        fill=to_hex(BEVEL_BOTTOM), width=1, tags=tags,
    )


def specular_sweep(
    canvas: tk.Canvas,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    phase: float,
    *,
    tint: RGB = ACCENT,
    tags: Sequence[str] = (),
    bands: int = 10,
) -> None:
    """A soft diagonal highlight travelling across a band of the hero.

    *phase* in 0..1 is the sweep position. Intensity fades at the ends so the
    highlight enters and leaves rather than popping.
    """
    travel = x1 - x0
    center = x0 - 260 + phase * (travel + 520)
    # Bell-shaped envelope: the highlight fades in and out at the extremes
    # rather than popping into view.
    envelope = math.sin(math.pi * min(1.0, max(0.0, phase)))
    if envelope <= 0.02:
        return
    for i in range(bands):
        ratio = (i + 1) / bands
        width = 26 * ratio
        amount = 0.16 * envelope * (1 - ratio)
        if amount <= 0.004:
            continue
        color = blend(SURFACE, tint, amount)
        canvas.create_line(
            center - width * 2.4, y1, center + width * 2.4, y0,
            fill=to_hex(color), width=max(1, int(width)), tags=tags,
        )


def current_phase(phase: float) -> float:
    """Clamp for callers that want a named helper."""
    return min(1.0, max(0.0, phase))


class LiquidBackground:
    """Animated drifting blobs behind the glass panels.

    The gradient is static and drawn once; only the blobs are redrawn per
    frame, which keeps the animation cheap enough for Tkinter.
    """

    def __init__(
        self,
        canvas: tk.Canvas,
        width: int,
        height: int,
        *,
        tag: str = "liquid",
    ) -> None:
        self.canvas = canvas
        self.width = width
        self.height = height
        self.tag = tag
        self.t = 0.0
        #: (cx ratio, cy ratio, radius ratio, tint, intensity, speed, phase)
        self._blobs = [
            (0.18, 0.16, 0.42, ACCENT, 0.16, 0.24, 0.0),
            (0.82, 0.24, 0.38, ACCENT_WARM, 0.14, 0.17, 1.7),
            (0.62, 0.86, 0.46, ACCENT_GREEN, 0.10, 0.13, 3.1),
            (0.30, 0.72, 0.34, ACCENT, 0.08, 0.21, 4.6),
        ]

    def paint_static(self) -> None:
        """Diagonal-ish gradient base plus the hero glow, drawn once.

        The glow is a broad, dim pool of accent light centred slightly above the
        middle. It is static so the expensive part is paid a single time; the
        blobs animate over the top of it.
        """
        vertical_gradient(
            self.canvas, 0, 0, self.width, self.height,
            BACKGROUND_TOP, BACKGROUND_BOTTOM, tags=(self.tag, "bg"),
        )
        radial_blob(
            self.canvas,
            self.width * 0.52, self.height * 0.34,
            min(self.width, self.height) * 0.85,
            ACCENT, intensity=0.045, rings=18, base=background_at(self.height * 0.34, self.height),
            tags=(self.tag, "bg"),
        )

    def paint_vignette(self) -> None:
        """Redraw the edge darkening above the blobs so the frame stays framed."""
        self.canvas.delete("liquid_vignette")
        vignette(
            self.canvas, self.width, self.height,
            strength=0.5, rings=22, tags=("liquid_vignette",),
        )

    def paint_blobs(self) -> None:
        """Redraw the drifting blobs for the current time step.

        Each blob is composited against the *gradient colour underneath it*,
        and a wider, dimmer halo is drawn after it so the light blooms into the
        backdrop instead of ending at a hard rim.
        """
        self.canvas.delete("liquid_blob")
        for cx_r, cy_r, r_r, tint, intensity, speed, phase in self._blobs:
            drift = self.t * speed + phase
            cx = (cx_r + 0.045 * math.sin(drift)) * self.width
            cy = (cy_r + 0.055 * math.cos(drift * 0.8)) * self.height
            radius = r_r * min(self.width, self.height) * (1 + 0.06 * math.sin(drift * 1.3))
            hue_shifted = shift_hue(tint, 0.02 * math.sin(drift * 0.5))
            under = background_at(cy, self.height)
            # Bloom first, so the core sits on top of its own haze.
            radial_blob(
                self.canvas, cx, cy, radius * 1.9, hue_shifted,
                intensity=intensity * 0.28, rings=9, base=under,
                tags=("liquid_blob",),
            )
            radial_blob(
                self.canvas, cx, cy, radius, hue_shifted,
                intensity=intensity, rings=13, base=under,
                tags=("liquid_blob",),
            )

    def step(self) -> None:
        """Advance time and repaint the moving layer."""
        self.t += 0.016
        self.paint_blobs()
        self.paint_vignette()


def scrolled_text_style() -> dict:
    """Shared look for Text widgets so raw output reads as console, not prose."""
    return {
        "bg": to_hex(blend(BACKGROUND_TOP, SURFACE, 0.5)),
        "fg": to_hex(TEXT),
        "insertbackground": to_hex(ACCENT),
        "selectbackground": to_hex(blend(SURFACE, ACCENT, 0.35)),
        "selectforeground": to_hex(TEXT),
        "relief": "flat",
        "borderwidth": 0,
        "highlightthickness": 0,
        "padx": 14,
        "pady": 10,
        "wrap": "word",
        "spacing1": 1,
        "spacing3": 4,
    }


def mono_font(size: int = 11) -> Tuple[str, int]:
    return ("TkFixedFont", size)


def ui_font(size: int = 11, weight: str = "normal") -> Tuple[str, int, str]:
    return ("TkDefaultFont", size, weight)
