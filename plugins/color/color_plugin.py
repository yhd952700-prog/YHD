"""
Color Manipulation Plugin for LiuHao AI OS

Provides color format conversion (HEX, RGB, HSL, HSV, CMYK),
color mixing, palette generation, complementary colors,
accessibility (WCAG contrast ratio), and color naming.

Capabilities:
- convert: Convert between color formats
- hex_to_rgb: HEX to RGB
- rgb_to_hex: RGB to HEX
- rgb_to_hsl: RGB to HSL
- hsl_to_rgb: HSL to RGB
- rgb_to_hsv: RGB to HSV
- hsv_to_rgb: HSV to RGB
- rgb_to_cmyk: RGB to CMYK
- cmyk_to_rgb: CMYK to RGB
- mix: Mix two colors
- complementary: Get complementary color
- generate_palette: Generate color palettes
- contrast_ratio: WCAG contrast ratio
- is_accessible: Check WCAG accessibility
- name_color: Find closest named color
"""

import re
import math
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class ColorResult:
    """Result of a color conversion."""
    hex: str = ""
    rgb: Tuple[int, int, int] = (0, 0, 0)
    hsl: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    hsv: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    cmyk: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    source_format: str = ""


@dataclass
class ContrastResult:
    """Result of contrast ratio calculation."""
    ratio: float
    level_aa_normal: bool
    level_aa_large: bool
    level_aaa_normal: bool
    level_aaa_large: bool
    foreground: str
    background: str


@dataclass
class ColorPalette:
    """A color palette."""
    name: str
    colors: List[str]
    count: int


class ColorPlugin(BasePlugin):
    """Color manipulation plugin.

    Provides color format conversion, mixing, palette generation,
    accessibility checking, and color naming.

    Example:
        plugin = ColorPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.convert("#ff5733", to_fmt="rgb")
        print(result.rgb)
    """

    # Named colors (CSS standard colors)
    NAMED_COLORS: Dict[str, str] = {
        "aliceblue": "#f0f8ff",
        "antiquewhite": "#faebd7",
        "aqua": "#00ffff",
        "aquamarine": "#7fffd4",
        "azure": "#f0ffff",
        "beige": "#f5f5dc",
        "bisque": "#ffe4c4",
        "black": "#000000",
        "blanchedalmond": "#ffebcd",
        "blue": "#0000ff",
        "blueviolet": "#8a2be2",
        "brown": "#a52a2a",
        "burlywood": "#deb887",
        "cadetblue": "#5f9ea0",
        "chartreuse": "#7fff00",
        "chocolate": "#d2691e",
        "coral": "#ff7f50",
        "cornflowerblue": "#6495ed",
        "cornsilk": "#fff8dc",
        "crimson": "#dc143c",
        "cyan": "#00ffff",
        "darkblue": "#00008b",
        "darkcyan": "#008b8b",
        "darkgoldenrod": "#b8860b",
        "darkgray": "#a9a9a9",
        "darkgreen": "#006400",
        "darkgrey": "#a9a9a9",
        "darkkhaki": "#bdb76b",
        "darkmagenta": "#8b008b",
        "darkolivegreen": "#556b2f",
        "darkorange": "#ff8c00",
        "darkorchid": "#9932cc",
        "darkred": "#8b0000",
        "darksalmon": "#e9967a",
        "darkseagreen": "#8fbc8f",
        "darkslateblue": "#483d8b",
        "darkslategray": "#2f4f4f",
        "darkslategrey": "#2f4f4f",
        "darkturquoise": "#00ced1",
        "darkviolet": "#9400d3",
        "deeppink": "#ff1493",
        "deepskyblue": "#00bfff",
        "dimgray": "#696969",
        "dimgrey": "#696969",
        "dodgerblue": "#1e90ff",
        "firebrick": "#b22222",
        "floralwhite": "#fffaf0",
        "forestgreen": "#228b22",
        "fuchsia": "#ff00ff",
        "gainsboro": "#dcdcdc",
        "ghostwhite": "#f8f8ff",
        "gold": "#ffd700",
        "goldenrod": "#daa520",
        "gray": "#808080",
        "green": "#008000",
        "greenyellow": "#adff2f",
        "grey": "#808080",
        "honeydew": "#f0fff0",
        "hotpink": "#ff69b4",
        "indianred": "#cd5c5c",
        "indigo": "#4b0082",
        "ivory": "#fffff0",
        "khaki": "#f0e68c",
        "lavender": "#e6e6fa",
        "lavenderblush": "#fff0f5",
        "lawngreen": "#7cfc00",
        "lemonchiffon": "#fffacd",
        "lightblue": "#add8e6",
        "lightcoral": "#f08080",
        "lightcyan": "#e0ffff",
        "lightgoldenrodyellow": "#fafad2",
        "lightgray": "#d3d3d3",
        "lightgreen": "#90ee90",
        "lightgrey": "#d3d3d3",
        "lightpink": "#ffb6c1",
        "lightsalmon": "#ffa07a",
        "lightseagreen": "#20b2aa",
        "lightskyblue": "#87cefa",
        "lightslategray": "#778899",
        "lightslategrey": "#778899",
        "lightsteelblue": "#b0c4de",
        "lightyellow": "#ffffe0",
        "lime": "#00ff00",
        "limegreen": "#32cd32",
        "linen": "#faf0e6",
        "magenta": "#ff00ff",
        "maroon": "#800000",
        "mediumaquamarine": "#66cdaa",
        "mediumblue": "#0000cd",
        "mediumorchid": "#ba55d3",
        "mediumpurple": "#9370db",
        "mediumseagreen": "#3cb371",
        "mediumslateblue": "#7b68ee",
        "mediumspringgreen": "#00fa9a",
        "mediumturquoise": "#48d1cc",
        "mediumvioletred": "#c71585",
        "midnightblue": "#191970",
        "mintcream": "#f5fffa",
        "mistyrose": "#ffe4e1",
        "moccasin": "#ffe4b5",
        "navajowhite": "#ffdead",
        "navy": "#000080",
        "oldlace": "#fdf5e6",
        "olive": "#808000",
        "olivedrab": "#6b8e23",
        "orange": "#ffa500",
        "orangered": "#ff4500",
        "orchid": "#da70d6",
        "palegoldenrod": "#eee8aa",
        "palegreen": "#98fb98",
        "paleturquoise": "#afeeee",
        "palevioletred": "#db7093",
        "papayawhip": "#ffefd5",
        "peachpuff": "#ffdab9",
        "peru": "#cd853f",
        "pink": "#ffc0cb",
        "plum": "#dda0dd",
        "powderblue": "#b0e0e6",
        "purple": "#800080",
        "rebeccapurple": "#663399",
        "red": "#ff0000",
        "rosybrown": "#bc8f8f",
        "royalblue": "#4169e1",
        "saddlebrown": "#8b4513",
        "salmon": "#fa8072",
        "sandybrown": "#f4a460",
        "seagreen": "#2e8b57",
        "seashell": "#fff5ee",
        "sienna": "#a0522d",
        "silver": "#c0c0c0",
        "skyblue": "#87ceeb",
        "slateblue": "#6a5acd",
        "slategray": "#708090",
        "slategrey": "#708090",
        "snow": "#fffafa",
        "springgreen": "#00ff7f",
        "steelblue": "#4682b4",
        "tan": "#d2b48c",
        "teal": "#008080",
        "thistle": "#d8bfd8",
        "tomato": "#ff6347",
        "turquoise": "#40e0d0",
        "violet": "#ee82ee",
        "wheat": "#f5deb3",
        "white": "#ffffff",
        "whitesmoke": "#f5f5f5",
        "yellow": "#ffff00",
        "yellowgreen": "#9acd32",
    }

    def __init__(self) -> None:
        """Initialize the color plugin."""
        metadata = PluginMetadata(
            name="color",
            version="1.0.0",
            description="Color format conversion, mixing, palettes, and accessibility",
            author="LiuHao AI OS Team",
            categories=["utility", "design", "visual"],
            keywords=["color", "rgb", "hex", "hsl", "cmyk", "palette", "accessibility"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_format: str = "hex"

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the color plugin."""
        try:
            if config:
                self._default_format = config.get("default_format", "hex")

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the color plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the color plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the color plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "convert",
            "hex_to_rgb",
            "rgb_to_hex",
            "rgb_to_hsl",
            "hsl_to_rgb",
            "rgb_to_hsv",
            "hsv_to_rgb",
            "rgb_to_cmyk",
            "cmyk_to_rgb",
            "mix",
            "complementary",
            "generate_palette",
            "contrast_ratio",
            "is_accessible",
            "name_color",
            "get_named_colors",
            "lighten",
            "darken",
            "saturate",
            "desaturate",
        ]

    # ==================== Color Parsing ====================

    def _parse_color(self, color: Union[str, Tuple[int, int, int], List[int]]) -> Tuple[int, int, int]:
        """Parse various color formats to RGB tuple."""
        if isinstance(color, (tuple, list)) and len(color) == 3:
            r, g, b = [int(c) for c in color]
            return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))

        if isinstance(color, str):
            color = color.strip().lower()

            # Named color
            if color in self.NAMED_COLORS:
                color = self.NAMED_COLORS[color]

            # Hex color
            if color.startswith("#"):
                hex_str = color[1:]
                if len(hex_str) == 3:
                    hex_str = "".join(c * 2 for c in hex_str)
                if len(hex_str) == 6:
                    try:
                        r = int(hex_str[0:2], 16)
                        g = int(hex_str[2:4], 16)
                        b = int(hex_str[4:6], 16)
                        return (r, g, b)
                    except ValueError:
                        pass

            # RGB string: rgb(r, g, b)
            rgb_match = re.match(r"rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", color)
            if rgb_match:
                r = int(rgb_match.group(1))
                g = int(rgb_match.group(2))
                b = int(rgb_match.group(3))
                return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))

            # HSL string: hsl(h, s%, l%)
            hsl_match = re.match(r"hsl\s*\(\s*(\d+)\s*,\s*(\d+)%\s*,\s*(\d+)%\s*\)", color)
            if hsl_match:
                h = int(hsl_match.group(1))
                s = int(hsl_match.group(2)) / 100.0
                l = int(hsl_match.group(3)) / 100.0
                return self._hsl_to_rgb(h, s, l)

        raise ValueError(f"Unable to parse color: {color}")

    # ==================== Conversion ====================

    def convert(
        self,
        color: Union[str, Tuple[int, int, int]],
        to_fmt: str = "hex",
    ) -> ColorResult:
        """Convert a color to all formats.

        Args:
            color: Color in any supported format.
            to_fmt: Target format name (for reference).

        Returns:
            ColorResult with all format representations.
        """
        self._ensure_active()
        self._increment_call_count()

        r, g, b = self._parse_color(color)

        result = ColorResult(source_format=to_fmt)
        result.rgb = (r, g, b)
        result.hex = self._rgb_to_hex(r, g, b)
        result.hsl = self._rgb_to_hsl(r, g, b)
        result.hsv = self._rgb_to_hsv(r, g, b)
        result.cmyk = self._rgb_to_cmyk(r, g, b)

        return result

    def hex_to_rgb(self, hex_color: str) -> Tuple[int, int, int]:
        """Convert HEX color to RGB tuple.

        Args:
            hex_color: Hex color string.

        Returns:
            RGB tuple (r, g, b).
        """
        self._ensure_active()
        self._increment_call_count()
        return self._parse_color(hex_color)

    def rgb_to_hex(self, r: int, g: int, b: int) -> str:
        """Convert RGB values to HEX string.

        Args:
            r: Red (0-255).
            g: Green (0-255).
            b: Blue (0-255).

        Returns:
            Hex color string with # prefix.
        """
        self._ensure_active()
        self._increment_call_count()
        return self._rgb_to_hex(r, g, b)

    def _rgb_to_hex(self, r: int, g: int, b: int) -> str:
        """Internal RGB to HEX conversion."""
        r = max(0, min(255, int(r)))
        g = max(0, min(255, int(g)))
        b = max(0, min(255, int(b)))
        return f"#{r:02x}{g:02x}{b:02x}"

    # RGB <-> HSL
    def rgb_to_hsl(self, r: int, g: int, b: int) -> Tuple[float, float, float]:
        """Convert RGB to HSL.

        Returns:
            HSL tuple (h: 0-360, s: 0-100%, l: 0-100%).
        """
        self._ensure_active()
        self._increment_call_count()
        h, s, l = self._rgb_to_hsl(r, g, b)
        return (round(h, 1), round(s * 100, 1), round(l * 100, 1))

    def _rgb_to_hsl(self, r: int, g: int, b: int) -> Tuple[float, float, float]:
        """Internal RGB to HSL (h:0-360, s:0-1, l:0-1)."""
        r_norm = r / 255.0
        g_norm = g / 255.0
        b_norm = b / 255.0

        max_val = max(r_norm, g_norm, b_norm)
        min_val = min(r_norm, g_norm, b_norm)
        l = (max_val + min_val) / 2.0

        if max_val == min_val:
            h = 0.0
            s = 0.0
        else:
            d = max_val - min_val
            s = d / (2.0 - max_val - min_val) if l > 0.5 else d / (max_val + min_val)

            if max_val == r_norm:
                h = ((g_norm - b_norm) / d + (6 if g_norm < b_norm else 0)) / 6
            elif max_val == g_norm:
                h = ((b_norm - r_norm) / d + 2) / 6
            else:
                h = ((r_norm - g_norm) / d + 4) / 6

        return (h * 360, s, l)

    def hsl_to_rgb(self, h: float, s: float, l: float) -> Tuple[int, int, int]:
        """Convert HSL to RGB.

        Args:
            h: Hue (0-360).
            s: Saturation (0-100%).
            l: Lightness (0-100%).

        Returns:
            RGB tuple.
        """
        self._ensure_active()
        self._increment_call_count()
        return self._hsl_to_rgb(h, s / 100.0, l / 100.0)

    def _hsl_to_rgb(self, h: float, s: float, l: float) -> Tuple[int, int, int]:
        """Internal HSL to RGB (h:0-360, s:0-1, l:0-1)."""
        h = h % 360 / 360.0

        if s == 0:
            r = g = b = l
        else:
            def hue2rgb(p: float, q: float, t: float) -> float:
                if t < 0:
                    t += 1
                if t > 1:
                    t -= 1
                if t < 1/6:
                    return p + (q - p) * 6 * t
                if t < 1/2:
                    return q
                if t < 2/3:
                    return p + (q - p) * (2/3 - t) * 6
                return p

            q = l * (1 + s) if l < 0.5 else l + s - l * s
            p = 2 * l - q
            r = hue2rgb(p, q, h + 1/3)
            g = hue2rgb(p, q, h)
            b = hue2rgb(p, q, h - 1/3)

        return (round(r * 255), round(g * 255), round(b * 255))

    # RGB <-> HSV
    def rgb_to_hsv(self, r: int, g: int, b: int) -> Tuple[float, float, float]:
        """Convert RGB to HSV.

        Returns:
            HSV tuple (h: 0-360, s: 0-100%, v: 0-100%).
        """
        self._ensure_active()
        self._increment_call_count()
        h, s, v = self._rgb_to_hsv(r, g, b)
        return (round(h, 1), round(s * 100, 1), round(v * 100, 1))

    def _rgb_to_hsv(self, r: int, g: int, b: int) -> Tuple[float, float, float]:
        """Internal RGB to HSV (h:0-360, s:0-1, v:0-1)."""
        r_norm = r / 255.0
        g_norm = g / 255.0
        b_norm = b / 255.0

        max_val = max(r_norm, g_norm, b_norm)
        min_val = min(r_norm, g_norm, b_norm)
        v = max_val
        d = max_val - min_val
        s = 0 if max_val == 0 else d / max_val

        if max_val == min_val:
            h = 0.0
        else:
            if max_val == r_norm:
                h = ((g_norm - b_norm) / d + (6 if g_norm < b_norm else 0)) / 6
            elif max_val == g_norm:
                h = ((b_norm - r_norm) / d + 2) / 6
            else:
                h = ((r_norm - g_norm) / d + 4) / 6

        return (h * 360, s, v)

    def hsv_to_rgb(self, h: float, s: float, v: float) -> Tuple[int, int, int]:
        """Convert HSV to RGB.

        Args:
            h: Hue (0-360).
            s: Saturation (0-100%).
            v: Value (0-100%).

        Returns:
            RGB tuple.
        """
        self._ensure_active()
        self._increment_call_count()
        return self._hsv_to_rgb(h, s / 100.0, v / 100.0)

    def _hsv_to_rgb(self, h: float, s: float, v: float) -> Tuple[int, int, int]:
        """Internal HSV to RGB."""
        h = (h % 360) / 60.0
        hi = int(h) % 6
        f = h - int(h)
        p = v * (1 - s)
        q = v * (1 - f * s)
        t = v * (1 - (1 - f) * s)

        if hi == 0:
            r, g, b = v, t, p
        elif hi == 1:
            r, g, b = q, v, p
        elif hi == 2:
            r, g, b = p, v, t
        elif hi == 3:
            r, g, b = p, q, v
        elif hi == 4:
            r, g, b = t, p, v
        else:
            r, g, b = v, p, q

        return (round(r * 255), round(g * 255), round(b * 255))

    # RGB <-> CMYK
    def rgb_to_cmyk(self, r: int, g: int, b: int) -> Tuple[float, float, float, float]:
        """Convert RGB to CMYK.

        Returns:
            CMYK tuple (0-100% each).
        """
        self._ensure_active()
        self._increment_call_count()
        c, m, y, k = self._rgb_to_cmyk(r, g, b)
        return (round(c, 1), round(m, 1), round(y, 1), round(k, 1))

    def _rgb_to_cmyk(self, r: int, g: int, b: int) -> Tuple[float, float, float, float]:
        """Internal RGB to CMYK (percentages)."""
        if r == 0 and g == 0 and b == 0:
            return (0.0, 0.0, 0.0, 100.0)

        r_norm = r / 255.0
        g_norm = g / 255.0
        b_norm = b / 255.0

        k = 1 - max(r_norm, g_norm, b_norm)
        if k == 1:
            c = m = y = 0.0
        else:
            c = (1 - r_norm - k) / (1 - k) * 100
            m = (1 - g_norm - k) / (1 - k) * 100
            y = (1 - b_norm - k) / (1 - k) * 100

        return (c, m, y, k * 100)

    def cmyk_to_rgb(self, c: float, m: float, y: float, k: float) -> Tuple[int, int, int]:
        """Convert CMYK to RGB.

        Args:
            c: Cyan (0-100%).
            m: Magenta (0-100%).
            y: Yellow (0-100%).
            k: Key/Black (0-100%).

        Returns:
            RGB tuple.
        """
        self._ensure_active()
        self._increment_call_count()

        c_norm = c / 100.0
        m_norm = m / 100.0
        y_norm = y / 100.0
        k_norm = k / 100.0

        r = 255 * (1 - c_norm) * (1 - k_norm)
        g = 255 * (1 - m_norm) * (1 - k_norm)
        b = 255 * (1 - y_norm) * (1 - k_norm)

        return (round(r), round(g), round(b))

    # ==================== Color Mixing ====================

    def mix(
        self,
        color1: Union[str, Tuple[int, int, int]],
        color2: Union[str, Tuple[int, int, int]],
        weight: float = 0.5,
    ) -> str:
        """Mix two colors together.

        Args:
            color1: First color.
            color2: Second color.
            weight: Mix weight (0.0 = all color1, 1.0 = all color2).

        Returns:
            Mixed color as hex string.
        """
        self._ensure_active()
        self._increment_call_count()

        r1, g1, b1 = self._parse_color(color1)
        r2, g2, b2 = self._parse_color(color2)

        w = max(0.0, min(1.0, weight))
        r = round(r1 * (1 - w) + r2 * w)
        g = round(g1 * (1 - w) + g2 * w)
        b = round(b1 * (1 - w) + b2 * w)

        return self._rgb_to_hex(r, g, b)

    def complementary(self, color: Union[str, Tuple[int, int, int]]) -> str:
        """Get the complementary color.

        Args:
            color: Input color.

        Returns:
            Complementary color as hex string.
        """
        self._ensure_active()
        self._increment_call_count()

        r, g, b = self._parse_color(color)
        return self._rgb_to_hex(255 - r, 255 - g, 255 - b)

    def lighten(self, color: Union[str, Tuple[int, int, int]], amount: float) -> str:
        """Lighten a color by amount (percentage).

        Args:
            color: Input color.
            amount: Amount to lighten (0-100).

        Returns:
            Lightened color as hex string.
        """
        self._ensure_active()
        self._increment_call_count()

        r, g, b = self._parse_color(color)
        h, s, l = self._rgb_to_hsl(r, g, b)
        l = min(1.0, l + amount / 100.0)
        r2, g2, b2 = self._hsl_to_rgb(h, s, l)
        return self._rgb_to_hex(r2, g2, b2)

    def darken(self, color: Union[str, Tuple[int, int, int]], amount: float) -> str:
        """Darken a color by amount (percentage).

        Args:
            color: Input color.
            amount: Amount to darken (0-100).

        Returns:
            Darkened color as hex string.
        """
        self._ensure_active()
        self._increment_call_count()

        r, g, b = self._parse_color(color)
        h, s, l = self._rgb_to_hsl(r, g, b)
        l = max(0.0, l - amount / 100.0)
        r2, g2, b2 = self._hsl_to_rgb(h, s, l)
        return self._rgb_to_hex(r2, g2, b2)

    def saturate(self, color: Union[str, Tuple[int, int, int]], amount: float) -> str:
        """Increase saturation of a color.

        Args:
            color: Input color.
            amount: Amount to increase (0-100).

        Returns:
            Saturated color as hex string.
        """
        self._ensure_active()
        self._increment_call_count()

        r, g, b = self._parse_color(color)
        h, s, l = self._rgb_to_hsl(r, g, b)
        s = min(1.0, s + amount / 100.0)
        r2, g2, b2 = self._hsl_to_rgb(h, s, l)
        return self._rgb_to_hex(r2, g2, b2)

    def desaturate(self, color: Union[str, Tuple[int, int, int]], amount: float) -> str:
        """Decrease saturation of a color.

        Args:
            color: Input color.
            amount: Amount to decrease (0-100).

        Returns:
            Desaturated color as hex string.
        """
        self._ensure_active()
        self._increment_call_count()

        r, g, b = self._parse_color(color)
        h, s, l = self._rgb_to_hsl(r, g, b)
        s = max(0.0, s - amount / 100.0)
        r2, g2, b2 = self._hsl_to_rgb(h, s, l)
        return self._rgb_to_hex(r2, g2, b2)

    # ==================== Palette Generation ====================

    def generate_palette(
        self,
        base_color: Union[str, Tuple[int, int, int]],
        palette_type: str = "analogous",
        count: int = 5,
    ) -> ColorPalette:
        """Generate a color palette.

        Args:
            base_color: Base color for the palette.
            palette_type: Type of palette: analogous, complementary,
                triadic, tetradic, monochromatic, shades, tints.
            count: Number of colors in the palette.

        Returns:
            ColorPalette with generated colors.
        """
        self._ensure_active()
        self._increment_call_count()

        r, g, b = self._parse_color(base_color)
        h, s, l = self._rgb_to_hsl(r, g, b)
        colors: List[str] = []

        if palette_type == "analogous":
            step = 30
            half = count // 2
            for i in range(-half, count - half):
                new_h = (h + i * step) % 360
                r2, g2, b2 = self._hsl_to_rgb(new_h, s, l)
                colors.append(self._rgb_to_hex(r2, g2, b2))

        elif palette_type == "complementary":
            colors.append(self._rgb_to_hex(r, g, b))
            comp_h = (h + 180) % 360
            r2, g2, b2 = self._hsl_to_rgb(comp_h, s, l)
            colors.append(self._rgb_to_hex(r2, g2, b2))
            # Add intermediate colors
            for i in range(1, count - 1):
                t = i / (count - 1)
                mixed_h = h + (comp_h - h) * t
                r3, g3, b3 = self._hsl_to_rgb(mixed_h % 360, s, l)
                colors.insert(i, self._rgb_to_hex(r3, g3, b3))

        elif palette_type == "triadic":
            for i in range(3):
                new_h = (h + i * 120) % 360
                r2, g2, b2 = self._hsl_to_rgb(new_h, s, l)
                colors.append(self._rgb_to_hex(r2, g2, b2))

        elif palette_type == "tetradic":
            for i in range(4):
                new_h = (h + i * 90) % 360
                r2, g2, b2 = self._hsl_to_rgb(new_h, s, l)
                colors.append(self._rgb_to_hex(r2, g2, b2))

        elif palette_type == "monochromatic":
            for i in range(count):
                new_l = 0.2 + (0.6 * i / max(1, count - 1))
                r2, g2, b2 = self._hsl_to_rgb(h, s, new_l)
                colors.append(self._rgb_to_hex(r2, g2, b2))

        elif palette_type == "shades":
            for i in range(count):
                factor = i / max(1, count - 1)
                new_l = l * (1 - factor * 0.8)
                r2, g2, b2 = self._hsl_to_rgb(h, s, new_l)
                colors.append(self._rgb_to_hex(r2, g2, b2))

        elif palette_type == "tints":
            for i in range(count):
                factor = i / max(1, count - 1)
                new_l = l + (1 - l) * factor
                r2, g2, b2 = self._hsl_to_rgb(h, s, new_l)
                colors.append(self._rgb_to_hex(r2, g2, b2))

        else:
            raise ValueError(
                f"Unknown palette type: {palette_type}. "
                f"Use: analogous, complementary, triadic, tetradic, monochromatic, shades, tints"
            )

        return ColorPalette(
            name=palette_type,
            colors=colors,
            count=len(colors),
        )

    # ==================== Accessibility ====================

    def contrast_ratio(
        self,
        foreground: Union[str, Tuple[int, int, int]],
        background: Union[str, Tuple[int, int, int]],
    ) -> ContrastResult:
        """Calculate WCAG contrast ratio between two colors.

        Args:
            foreground: Foreground (text) color.
            background: Background color.

        Returns:
            ContrastResult with ratio and WCAG levels.
        """
        self._ensure_active()
        self._increment_call_count()

        r1, g1, b1 = self._parse_color(foreground)
        r2, g2, b2 = self._parse_color(background)

        l1 = self._relative_luminance(r1, g1, b1)
        l2 = self._relative_luminance(r2, g2, b2)

        lighter = max(l1, l2)
        darker = min(l1, l2)
        ratio = (lighter + 0.05) / (darker + 0.05)

        # WCAG 2.1 levels
        # AA normal: 4.5:1, AA large: 3:1
        # AAA normal: 7:1, AAA large: 4.5:1
        return ContrastResult(
            ratio=round(ratio, 2),
            level_aa_normal=ratio >= 4.5,
            level_aa_large=ratio >= 3.0,
            level_aaa_normal=ratio >= 7.0,
            level_aaa_large=ratio >= 4.5,
            foreground=self._rgb_to_hex(r1, g1, b1),
            background=self._rgb_to_hex(r2, g2, b2),
        )

    def _relative_luminance(self, r: int, g: int, b: int) -> float:
        """Calculate relative luminance per WCAG 2.1."""
        def channel_luminance(channel: int) -> float:
            srgb = channel / 255.0
            if srgb <= 0.03928:
                return srgb / 12.92
            else:
                return ((srgb + 0.055) / 1.055) ** 2.4

        return (
            0.2126 * channel_luminance(r)
            + 0.7152 * channel_luminance(g)
            + 0.0722 * channel_luminance(b)
        )

    def is_accessible(
        self,
        foreground: Union[str, Tuple[int, int, int]],
        background: Union[str, Tuple[int, int, int]],
        level: str = "AA",
        size: str = "normal",
    ) -> bool:
        """Check if color pair meets WCAG accessibility standard.

        Args:
            foreground: Text color.
            background: Background color.
            level: 'AA' or 'AAA'.
            size: 'normal' or 'large'.

        Returns:
            True if accessible.
        """
        self._ensure_active()
        self._increment_call_count()

        result = self.contrast_ratio(foreground, background)

        if level == "AA":
            return result.level_aa_normal if size == "normal" else result.level_aa_large
        elif level == "AAA":
            return result.level_aaa_normal if size == "normal" else result.level_aaa_large
        else:
            raise ValueError(f"Unknown level: {level}. Use 'AA' or 'AAA'.")

    # ==================== Color Naming ====================

    def name_color(self, color: Union[str, Tuple[int, int, int]]) -> Dict[str, Any]:
        """Find the closest named color.

        Args:
            color: Input color.

        Returns:
            Dict with 'name', 'hex', 'distance', and 'exact_match'.
        """
        self._ensure_active()
        self._increment_call_count()

        r, g, b = self._parse_color(color)
        input_hex = self._rgb_to_hex(r, g, b)

        best_name = ""
        best_distance = float("inf")
        best_hex = ""

        for name, hex_val in self.NAMED_COLORS.items():
            if hex_val == input_hex:
                return {
                    "name": name,
                    "hex": hex_val,
                    "distance": 0,
                    "exact_match": True,
                }

            nr, ng, nb = self._parse_color(hex_val)
            distance = self._color_distance(r, g, b, nr, ng, nb)

            if distance < best_distance:
                best_distance = distance
                best_name = name
                best_hex = hex_val

        return {
            "name": best_name,
            "hex": best_hex,
            "distance": round(best_distance, 2),
            "exact_match": False,
        }

    def _color_distance(self, r1: int, g1: int, b1: int, r2: int, g2: int, b2: int) -> float:
        """Calculate Euclidean distance between two colors."""
        return math.sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2)

    def get_named_colors(self, category: Optional[str] = None) -> Dict[str, str]:
        """Get list of named colors.

        Args:
            category: Optional category filter (not fully implemented).

        Returns:
            Dict of color name -> hex value.
        """
        self._ensure_active()
        self._increment_call_count()
        return dict(self.NAMED_COLORS)

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "default_format": self._default_format,
            "named_colors": len(self.NAMED_COLORS),
            "supported_formats": ["hex", "rgb", "hsl", "hsv", "cmyk"],
            "palette_types": [
                "analogous", "complementary", "triadic", "tetradic",
                "monochromatic", "shades", "tints",
            ],
            "accessibility_standards": ["WCAG 2.1 AA", "WCAG 2.1 AAA"],
        }
