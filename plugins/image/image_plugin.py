"""
Image Processing Plugin for LiuHao AI OS

Provides image manipulation including resize, crop, rotate, flip,
format conversion, compression, EXIF extraction, thumbnail generation,
and basic filter effects.

Capabilities:
- resize: Resize an image
- crop: Crop an image
- rotate: Rotate an image
- flip: Flip an image (horizontal/vertical)
- convert_format: Convert image format
- compress: Compress an image
- extract_exif: Extract EXIF metadata
- generate_thumbnail: Generate thumbnail
- apply_filter: Apply basic filter effects
- get_info: Get image information
"""

import os
import struct
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from pathlib import Path

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class ImageInfo:
    """Information about an image file."""
    file_path: str
    file_size: int
    width: int
    height: int
    format: str
    mode: str
    aspect_ratio: float
    megapixels: float
    color_depth: int = 0


@dataclass
class EXIFData:
    """EXIF metadata from an image."""
    make: str = ""
    model: str = ""
    datetime: str = ""
    width: int = 0
    height: int = 0
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    gps_altitude: Optional[float] = None
    camera_iso: Optional[int] = None
    exposure_time: str = ""
    f_number: Optional[float] = None
    focal_length: Optional[float] = None
    lens_model: str = ""
    software: str = ""
    artist: str = ""
    copyright: str = ""
    raw_tags: Dict[str, Any] = field(default_factory=dict)


class ImagePlugin(BasePlugin):
    """Image processing plugin with manipulation and analysis capabilities.

    Supports resize, crop, rotate, flip, format conversion, compression,
    EXIF extraction, thumbnail generation, and basic filter effects.

    Example:
        plugin = ImagePlugin()
        plugin.initialize()
        plugin.activate()

        plugin.resize("input.jpg", "output.jpg", width=800, height=600)
    """

    SUPPORTED_FORMATS = ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "webp", "ico"]

    FILTER_EFFECTS = [
        "grayscale", "sepia", "invert", "brightness", "contrast",
        "blur", "sharpen", "vintage", "warm", "cool",
    ]

    def __init__(self) -> None:
        """Initialize the image plugin."""
        metadata = PluginMetadata(
            name="image",
            version="1.0.0",
            description="Image processing and manipulation plugin",
            author="LiuHao AI OS Team",
            categories=["media", "image", "utility"],
            keywords=["image", "photo", "resize", "crop", "filter", "exif"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._has_pillow: bool = False

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the image plugin."""
        try:
            try:
                from PIL import Image
                self._has_pillow = True
            except ImportError:
                self._has_pillow = False

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the image plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the image plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the image plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "resize",
            "crop",
            "rotate",
            "flip",
            "convert_format",
            "compress",
            "extract_exif",
            "generate_thumbnail",
            "apply_filter",
            "get_info",
            "batch_resize",
        ]

    # ==================== Image Processing Methods ====================

    def get_info(self, image_path: str) -> ImageInfo:
        """Get information about an image file.

        Args:
            image_path: Path to the image file.

        Returns:
            ImageInfo with image metadata.
        """
        self._ensure_active()
        self._increment_call_count()

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        file_size = path.stat().st_size

        if self._has_pillow:
            from PIL import Image
            with Image.open(path) as img:
                width, height = img.size
                return ImageInfo(
                    file_path=str(path),
                    file_size=file_size,
                    width=width,
                    height=height,
                    format=img.format or "unknown",
                    mode=img.mode,
                    aspect_ratio=round(width / height, 3) if height > 0 else 0,
                    megapixels=round((width * height) / 1_000_000, 2),
                )
        else:
            # Basic size detection from file header
            width, height, fmt = self._get_image_size_basic(path)
            return ImageInfo(
                file_path=str(path),
                file_size=file_size,
                width=width,
                height=height,
                format=fmt,
                mode="unknown",
                aspect_ratio=round(width / height, 3) if height > 0 else 0,
                megapixels=round((width * height) / 1_000_000, 2),
            )

    def resize(
        self,
        input_path: str,
        output_path: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        maintain_aspect: bool = True,
        resample: str = "lanczos",
    ) -> bool:
        """Resize an image.

        Args:
            input_path: Path to input image.
            output_path: Path to save resized image.
            width: Target width in pixels.
            height: Target height in pixels.
            maintain_aspect: Whether to maintain aspect ratio.
            resample: Resampling method.

        Returns:
            True if resize succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_pillow:
            raise PluginExecutionError("PIL/Pillow library is required for image resizing")

        from PIL import Image

        with Image.open(input_path) as img:
            orig_w, orig_h = img.size

            if maintain_aspect:
                if width and height:
                    # Fit within dimensions
                    ratio = min(width / orig_w, height / orig_h)
                    new_w = int(orig_w * ratio)
                    new_h = int(orig_h * ratio)
                elif width:
                    ratio = width / orig_w
                    new_w = width
                    new_h = int(orig_h * ratio)
                elif height:
                    ratio = height / orig_h
                    new_w = int(orig_w * ratio)
                    new_h = height
                else:
                    raise ValueError("At least one of width or height must be specified")
            else:
                new_w = width or orig_w
                new_h = height or orig_h

            resample_map = {
                "nearest": Image.NEAREST,
                "bilinear": Image.BILINEAR,
                "bicubic": Image.BICUBIC,
                "lanczos": Image.LANCZOS,
            }
            resample_method = resample_map.get(resample.lower(), Image.LANCZOS)

            resized = img.resize((new_w, new_h), resample_method)
            resized.save(output_path)
            return True

    def crop(
        self,
        input_path: str,
        output_path: str,
        left: int,
        top: int,
        right: int,
        bottom: int,
    ) -> bool:
        """Crop an image.

        Args:
            input_path: Path to input image.
            output_path: Path to save cropped image.
            left: Left crop coordinate.
            top: Top crop coordinate.
            right: Right crop coordinate.
            bottom: Bottom crop coordinate.

        Returns:
            True if crop succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_pillow:
            raise PluginExecutionError("PIL/Pillow library is required")

        from PIL import Image

        with Image.open(input_path) as img:
            cropped = img.crop((left, top, right, bottom))
            cropped.save(output_path)
            return True

    def rotate(
        self,
        input_path: str,
        output_path: str,
        angle: float,
        expand: bool = True,
        fill_color: Tuple[int, int, int] = (0, 0, 0),
    ) -> bool:
        """Rotate an image.

        Args:
            input_path: Path to input image.
            output_path: Path to save rotated image.
            angle: Rotation angle in degrees (counter-clockwise).
            expand: Whether to expand the output to fit the rotated image.
            fill_color: Background color for empty areas.

        Returns:
            True if rotation succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_pillow:
            raise PluginExecutionError("PIL/Pillow library is required")

        from PIL import Image

        with Image.open(input_path) as img:
            rotated = img.rotate(angle, expand=expand, fillcolor=fill_color)
            rotated.save(output_path)
            return True

    def flip(
        self,
        input_path: str,
        output_path: str,
        direction: str = "horizontal",
    ) -> bool:
        """Flip an image.

        Args:
            input_path: Path to input image.
            output_path: Path to save flipped image.
            direction: 'horizontal' or 'vertical'.

        Returns:
            True if flip succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_pillow:
            raise PluginExecutionError("PIL/Pillow library is required")

        from PIL import Image, ImageOps

        with Image.open(input_path) as img:
            if direction.lower() == "horizontal":
                flipped = ImageOps.mirror(img)
            elif direction.lower() == "vertical":
                flipped = ImageOps.flip(img)
            else:
                raise ValueError(f"Invalid flip direction: {direction}")
            flipped.save(output_path)
            return True

    def convert_format(
        self,
        input_path: str,
        output_path: str,
        target_format: Optional[str] = None,
        quality: int = 90,
    ) -> bool:
        """Convert image format.

        Args:
            input_path: Path to input image.
            output_path: Path to save converted image.
            target_format: Target format (e.g., 'PNG', 'JPEG').
                Inferred from output extension if None.
            quality: Quality for lossy formats (1-100).

        Returns:
            True if conversion succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_pillow:
            raise PluginExecutionError("PIL/Pillow library is required")

        from PIL import Image

        with Image.open(input_path) as img:
            save_kwargs = {}
            if target_format and target_format.upper() in ("JPEG", "JPG"):
                save_kwargs["quality"] = quality
                save_kwargs["optimize"] = True
                # Convert RGBA to RGB for JPEG
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")

            if target_format:
                img.save(output_path, format=target_format.upper(), **save_kwargs)
            else:
                img.save(output_path, **save_kwargs)
            return True

    def compress(
        self,
        input_path: str,
        output_path: str,
        quality: int = 75,
        max_size_kb: Optional[int] = None,
    ) -> bool:
        """Compress an image.

        Args:
            input_path: Path to input image.
            output_path: Path to save compressed image.
            quality: Compression quality (1-100, lower = smaller).
            max_size_kb: Target maximum file size in KB (iterative).

        Returns:
            True if compression succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_pillow:
            raise PluginExecutionError("PIL/Pillow library is required")

        from PIL import Image

        with Image.open(input_path) as img:
            if max_size_kb:
                # Iterative compression to hit target size
                current_quality = quality
                while current_quality > 10:
                    img.save(output_path, quality=current_quality, optimize=True)
                    file_size = os.path.getsize(output_path) / 1024
                    if file_size <= max_size_kb:
                        return True
                    current_quality -= 10
                return True
            else:
                img.save(output_path, quality=quality, optimize=True)
                return True

    def extract_exif(self, image_path: str) -> EXIFData:
        """Extract EXIF metadata from an image.

        Args:
            image_path: Path to the image file.

        Returns:
            EXIFData with extracted metadata.
        """
        self._ensure_active()
        self._increment_call_count()

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        exif = EXIFData()

        if self._has_pillow:
            try:
                from PIL import Image
                from PIL.ExifTags import TAGS, GPSTAGS

                with Image.open(path) as img:
                    exif_data = img._getexif() if hasattr(img, "_getexif") else None

                    if exif_data:
                        raw_tags = {}
                        for tag_id, value in exif_data.items():
                            tag_name = TAGS.get(tag_id, str(tag_id))
                            raw_tags[tag_name] = str(value)

                            if tag_name == "Make":
                                exif.make = str(value)
                            elif tag_name == "Model":
                                exif.model = str(value)
                            elif tag_name == "DateTime":
                                exif.datetime = str(value)
                            elif tag_name == "ISOSpeedRatings":
                                exif.camera_iso = int(value) if value else None
                            elif tag_name == "ExposureTime":
                                exif.exposure_time = str(value)
                            elif tag_name == "FNumber":
                                exif.f_number = float(value) if value else None
                            elif tag_name == "FocalLength":
                                exif.focal_length = float(value) if value else None
                            elif tag_name == "Software":
                                exif.software = str(value)
                            elif tag_name == "Artist":
                                exif.artist = str(value)
                            elif tag_name == "Copyright":
                                exif.copyright = str(value)
                            elif tag_name == "ExifImageWidth":
                                exif.width = int(value)
                            elif tag_name == "ExifImageHeight":
                                exif.height = int(value)

                        # Parse GPS info
                        if "GPSInfo" in raw_tags:
                            try:
                                gps_info = exif_data.get(34853, {})
                                if gps_info:
                                    gps_tags = {}
                                    for gps_tag_id, gps_value in gps_info.items():
                                        gps_tag_name = GPSTAGS.get(gps_tag_id, str(gps_tag_id))
                                        gps_tags[gps_tag_name] = gps_value

                                    # Convert GPS coordinates
                                    lat = self._gps_to_decimal(
                                        gps_tags.get("GPSLatitude"),
                                        gps_tags.get("GPSLatitudeRef"),
                                    )
                                    lon = self._gps_to_decimal(
                                        gps_tags.get("GPSLongitude"),
                                        gps_tags.get("GPSLongitudeRef"),
                                    )
                                    exif.gps_latitude = lat
                                    exif.gps_longitude = lon

                                    if "GPSAltitude" in gps_tags:
                                        alt = gps_tags["GPSAltitude"]
                                        exif.gps_altitude = float(alt) if alt else None

                                raw_tags["GPSInfo"] = gps_tags
                            except Exception:
                                pass

                        exif.raw_tags = raw_tags
            except Exception:
                pass

        return exif

    def generate_thumbnail(
        self,
        input_path: str,
        output_path: str,
        size: int = 200,
        square: bool = True,
    ) -> bool:
        """Generate a thumbnail from an image.

        Args:
            input_path: Path to input image.
            output_path: Path to save thumbnail.
            size: Thumbnail size (width and height for square, or max dimension).
            square: Whether to make a square thumbnail (center-crop).

        Returns:
            True if thumbnail generation succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_pillow:
            raise PluginExecutionError("PIL/Pillow library is required")

        from PIL import Image

        with Image.open(input_path) as img:
            if square:
                # Center crop to square, then resize
                w, h = img.size
                min_dim = min(w, h)
                left = (w - min_dim) // 2
                top = (h - min_dim) // 2
                right = left + min_dim
                bottom = top + min_dim
                img = img.crop((left, top, right, bottom))

            img.thumbnail((size, size), Image.LANCZOS)
            img.save(output_path)
            return True

    def apply_filter(
        self,
        input_path: str,
        output_path: str,
        filter_name: str,
        intensity: float = 1.0,
    ) -> bool:
        """Apply a filter effect to an image.

        Args:
            input_path: Path to input image.
            output_path: Path to save filtered image.
            filter_name: Name of the filter effect.
            intensity: Filter intensity (0.0 to 2.0).

        Returns:
            True if filter was applied successfully.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_pillow:
            raise PluginExecutionError("PIL/Pillow library is required")

        from PIL import Image, ImageOps, ImageFilter, ImageEnhance

        filter_name = filter_name.lower()

        with Image.open(input_path) as img:
            result = img

            if filter_name == "grayscale":
                result = ImageOps.grayscale(result).convert("RGB")
            elif filter_name == "sepia":
                result = self._apply_sepia(result, intensity)
            elif filter_name == "invert":
                result = ImageOps.invert(result.convert("RGB"))
            elif filter_name == "brightness":
                enhancer = ImageEnhance.Brightness(result)
                result = enhancer.enhance(1.0 + intensity * 0.5)
            elif filter_name == "contrast":
                enhancer = ImageEnhance.Contrast(result)
                result = enhancer.enhance(1.0 + intensity * 0.5)
            elif filter_name == "blur":
                result = result.filter(ImageFilter.GaussianBlur(radius=intensity * 5))
            elif filter_name == "sharpen":
                enhancer = ImageEnhance.Sharpness(result)
                result = enhancer.enhance(1.0 + intensity)
            elif filter_name == "vintage":
                result = self._apply_vintage(result, intensity)
            elif filter_name == "warm":
                result = self._apply_color_tint(result, 1.0 + intensity * 0.2, 1.0, 1.0 - intensity * 0.1)
            elif filter_name == "cool":
                result = self._apply_color_tint(result, 1.0 - intensity * 0.1, 1.0, 1.0 + intensity * 0.2)
            else:
                raise ValueError(f"Unknown filter: {filter_name}")

            result.save(output_path)
            return True

    def batch_resize(
        self,
        input_dir: str,
        output_dir: str,
        width: int,
        height: int,
        pattern: str = "*.jpg",
    ) -> int:
        """Resize all images in a directory.

        Args:
            input_dir: Directory containing input images.
            output_dir: Directory to save resized images.
            width: Target width.
            height: Target height.
            pattern: File pattern to match.

        Returns:
            Number of images processed.
        """
        self._ensure_active()

        input_path = Path(input_dir)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        count = 0
        for img_file in input_path.glob(pattern):
            try:
                out_file = output_path / img_file.name
                self.resize(str(img_file), str(out_file), width=width, height=height)
                count += 1
            except Exception:
                continue

        return count

    # ==================== Internal Helpers ====================

    def _get_image_size_basic(self, path: Path) -> Tuple[int, int, str]:
        """Get image dimensions from file header (without PIL)."""
        try:
            with open(path, "rb") as f:
                header = f.read(32)

                # JPEG
                if header[:2] == b"\xff\xd8":
                    f.seek(2)
                    while True:
                        marker = f.read(2)
                        if len(marker) < 2:
                            break
                        if marker[0] != 0xFF:
                            break
                        segment_length = struct.unpack(">H", f.read(2))[0]
                        if marker[1] in (0xC0, 0xC1, 0xC2):
                            f.read(1)  # precision
                            h = struct.unpack(">H", f.read(2))[0]
                            w = struct.unpack(">H", f.read(2))[0]
                            return w, h, "JPEG"
                        else:
                            f.seek(segment_length - 2, 1)

                # PNG
                elif header[:8] == b"\x89PNG\r\n\x1a\n":
                    w = struct.unpack(">I", header[16:20])[0]
                    h = struct.unpack(">I", header[20:24])[0]
                    return w, h, "PNG"

                # GIF
                elif header[:3] == b"GIF":
                    w = struct.unpack("<H", header[6:8])[0]
                    h = struct.unpack("<H", header[8:10])[0]
                    return w, h, "GIF"

                # BMP
                elif header[:2] == b"BM":
                    w = struct.unpack("<I", header[18:22])[0]
                    h = struct.unpack("<I", header[22:26])[0]
                    return w, h, "BMP"
        except Exception:
            pass

        return 0, 0, "unknown"

    def _gps_to_decimal(
        self,
        coords: Optional[Tuple[Any, Any, Any]],
        ref: Optional[str],
    ) -> Optional[float]:
        """Convert GPS coordinates from DMS to decimal."""
        if not coords or not ref:
            return None

        try:
            degrees = float(coords[0])
            minutes = float(coords[1])
            seconds = float(coords[2])

            decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)

            if ref in ("S", "W"):
                decimal = -decimal

            return round(decimal, 6)
        except Exception:
            return None

    def _apply_sepia(self, img: Any, intensity: float) -> Any:
        """Apply sepia tone effect."""
        from PIL import Image
        img = img.convert("RGB")
        pixels = img.load()
        w, h = img.size

        for i in range(w):
            for j in range(h):
                r, g, b = pixels[i, j]
                tr = 0.393 * r + 0.769 * g + 0.189 * b
                tg = 0.349 * r + 0.686 * g + 0.168 * b
                tb = 0.272 * r + 0.534 * g + 0.131 * b

                # Blend with original based on intensity
                tr = r + (tr - r) * intensity
                tg = g + (tg - g) * intensity
                tb = b + (tb - b) * intensity

                pixels[i, j] = (int(min(255, tr)), int(min(255, tg)), int(min(255, tb)))

        return img

    def _apply_vintage(self, img: Any, intensity: float) -> Any:
        """Apply vintage/retro effect."""
        from PIL import Image, ImageEnhance
        img = img.convert("RGB")

        # Reduce saturation
        enhancer = ImageEnhance.Color(img)
        img = enhancer.enhance(0.7)

        # Add warm tint
        img = self._apply_color_tint(img, 1.1, 1.0, 0.85)

        # Slightly reduce contrast
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(0.9)

        return img

    def _apply_color_tint(self, img: Any, r_mult: float, g_mult: float, b_mult: float) -> Any:
        """Apply color tint by multiplying RGB channels."""
        from PIL import Image
        img = img.convert("RGB")
        pixels = img.load()
        w, h = img.size

        for i in range(w):
            for j in range(h):
                r, g, b = pixels[i, j]
                pixels[i, j] = (
                    int(min(255, r * r_mult)),
                    int(min(255, g * g_mult)),
                    int(min(255, b * b_mult)),
                )

        return img
