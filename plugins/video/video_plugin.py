"""
Video Processing Plugin for LiuHao AI OS

Provides video info extraction, frame extraction, thumbnail generation,
format conversion interface, and subtitle extraction.

Capabilities:
- get_info: Get video file information
- extract_frame: Extract a single frame at a specific time
- extract_frames: Extract multiple frames
- generate_thumbnail: Generate a thumbnail image
- extract_subtitles: Extract subtitles from video
- get_thumbnail: Get thumbnail from video
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
class VideoInfo:
    """Information about a video file."""
    file_path: str
    file_size: int
    duration_seconds: float
    width: int
    height: int
    resolution: str
    codec: str
    fps: float
    bitrate: int
    audio_codec: str = ""
    audio_channels: int = 0
    audio_sample_rate: int = 0
    format: str = ""
    frame_count: int = 0


@dataclass
class FrameExtractionResult:
    """Result of frame extraction."""
    video_path: str
    output_dir: str
    frames_extracted: int
    frame_files: List[str]
    timestamps: List[float]


class VideoPlugin(BasePlugin):
    """Video processing plugin with analysis and manipulation capabilities.

    Supports info extraction, frame extraction, thumbnail generation,
    format conversion, and subtitle extraction.

    Example:
        plugin = VideoPlugin()
        plugin.initialize()
        plugin.activate()

        info = plugin.get_info("video.mp4")
        print(info.duration_seconds)
    """

    SUPPORTED_FORMATS = [
        "mp4", "avi", "mkv", "mov", "wmv", "flv", "webm", "m4v",
        "mpg", "mpeg", "3gp", "ts", "ogv",
    ]

    def __init__(self) -> None:
        """Initialize the video plugin."""
        metadata = PluginMetadata(
            name="video",
            version="1.0.0",
            description="Video processing and analysis plugin",
            author="LiuHao AI OS Team",
            categories=["media", "video", "utility"],
            keywords=["video", "frame", "thumbnail", "extract", "subtitle"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._has_opencv: bool = False
        self._has_ffmpeg: bool = False

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the video plugin."""
        try:
            try:
                import cv2
                self._has_opencv = True
            except ImportError:
                self._has_opencv = False

            # Check for ffmpeg
            self._has_ffmpeg = self._check_ffmpeg()

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the video plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the video plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the video plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "get_info",
            "extract_frame",
            "extract_frames",
            "generate_thumbnail",
            "extract_subtitles",
            "get_supported_formats",
            "is_available",
        ]

    # ==================== Video Processing Methods ====================

    def is_available(self) -> bool:
        """Check if video processing is available."""
        return self._has_opencv or self._has_ffmpeg

    def get_info(self, video_path: str) -> VideoInfo:
        """Get information about a video file.

        Args:
            video_path: Path to the video file.

        Returns:
            VideoInfo with video metadata.
        """
        self._ensure_active()
        self._increment_call_count()

        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        file_size = path.stat().st_size
        ext = path.suffix.lower().lstrip(".")

        if self._has_opencv:
            return self._get_info_opencv(path, ext, file_size)
        elif self._has_ffmpeg:
            return self._get_info_ffmpeg(path, ext, file_size)
        else:
            # Basic info from file header
            return self._get_info_basic(path, ext, file_size)

    def extract_frame(
        self,
        video_path: str,
        output_path: str,
        timestamp_seconds: float,
    ) -> bool:
        """Extract a single frame at a specific timestamp.

        Args:
            video_path: Path to the video file.
            output_path: Path to save the frame image.
            timestamp_seconds: Time in seconds to extract frame.

        Returns:
            True if frame extraction succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_opencv:
            raise PluginExecutionError(
                "OpenCV is required for frame extraction"
            )

        import cv2

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise PluginExecutionError(f"Could not open video: {video_path}")

        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps > 0:
                frame_num = int(timestamp_seconds * fps)
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)

            ret, frame = cap.read()
            if ret:
                cv2.imwrite(output_path, frame)
                return True
            return False
        finally:
            cap.release()

    def extract_frames(
        self,
        video_path: str,
        output_dir: str,
        interval_seconds: float = 1.0,
        max_frames: Optional[int] = None,
        start_seconds: float = 0.0,
        end_seconds: Optional[float] = None,
        output_format: str = "jpg",
    ) -> FrameExtractionResult:
        """Extract multiple frames at regular intervals.

        Args:
            video_path: Path to the video file.
            output_dir: Directory to save extracted frames.
            interval_seconds: Time between frames in seconds.
            max_frames: Maximum number of frames to extract.
            start_seconds: Start time in seconds.
            end_seconds: End time in seconds.
            output_format: Output image format (jpg, png).

        Returns:
            FrameExtractionResult with extracted frame info.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_opencv:
            raise PluginExecutionError("OpenCV is required for frame extraction")

        import cv2

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise PluginExecutionError(f"Could not open video: {video_path}")

        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = total_frames / fps if fps > 0 else 0

            if end_seconds is None:
                end_seconds = duration

            frame_files: List[str] = []
            timestamps: List[float] = []
            count = 0

            current_time = start_seconds
            while current_time <= end_seconds:
                if max_frames and count >= max_frames:
                    break

                frame_num = int(current_time * fps)
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)

                ret, frame = cap.read()
                if not ret:
                    break

                frame_filename = f"frame_{count:06d}.{output_format}"
                frame_filepath = output_path / frame_filename
                cv2.imwrite(str(frame_filepath), frame)

                frame_files.append(str(frame_filepath))
                timestamps.append(round(current_time, 3))
                count += 1

                current_time += interval_seconds

            return FrameExtractionResult(
                video_path=video_path,
                output_dir=str(output_path),
                frames_extracted=count,
                frame_files=frame_files,
                timestamps=timestamps,
            )
        finally:
            cap.release()

    def generate_thumbnail(
        self,
        video_path: str,
        output_path: str,
        timestamp_seconds: Optional[float] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> bool:
        """Generate a thumbnail image from a video.

        Args:
            video_path: Path to the video file.
            output_path: Path to save the thumbnail.
            timestamp_seconds: Frame time to use. Default: 10% of duration.
            width: Thumbnail width. Maintains aspect if only one is set.
            height: Thumbnail height.

        Returns:
            True if thumbnail generation succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_opencv:
            raise PluginExecutionError("OpenCV is required for thumbnail generation")

        import cv2

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise PluginExecutionError(f"Could not open video: {video_path}")

        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = total_frames / fps if fps > 0 else 0

            # Default to 10% of duration
            if timestamp_seconds is None:
                timestamp_seconds = duration * 0.1

            frame_num = int(timestamp_seconds * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)

            ret, frame = cap.read()
            if not ret:
                # Try middle frame
                cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames // 2)
                ret, frame = cap.read()

            if ret:
                # Resize if requested
                if width or height:
                    h, w = frame.shape[:2]
                    if width and height:
                        new_size = (width, height)
                    elif width:
                        ratio = width / w
                        new_size = (width, int(h * ratio))
                    else:
                        ratio = height / h
                        new_size = (int(w * ratio), height)
                    frame = cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)

                cv2.imwrite(output_path, frame)
                return True
            return False
        finally:
            cap.release()

    def extract_subtitles(
        self,
        video_path: str,
        output_path: Optional[str] = None,
        track_index: int = 0,
    ) -> Dict[str, Any]:
        """Extract subtitles from a video file.

        Args:
            video_path: Path to the video file.
            output_path: Path to save subtitle file. Optional.
            track_index: Subtitle track index.

        Returns:
            Dict with subtitle info and content.
        """
        self._ensure_active()
        self._increment_call_count()

        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        result = {
            "success": False,
            "track_count": 0,
            "subtitle_text": "",
            "output_file": None,
            "format": "",
            "error": None,
        }

        if self._has_ffmpeg:
            try:
                import subprocess

                # Get subtitle tracks
                probe_cmd = [
                    "ffprobe", "-v", "quiet",
                    "-print_format", "json",
                    "-show_streams",
                    "-select_streams", "s",
                    str(path),
                ]
                probe = subprocess.run(probe_cmd, capture_output=True, text=True)
                import json
                streams_info = json.loads(probe.stdout)
                streams = streams_info.get("streams", [])
                result["track_count"] = len(streams)

                if streams and track_index < len(streams):
                    # Extract subtitle
                    if output_path is None:
                        output_path = str(path.with_suffix(".srt"))

                    extract_cmd = [
                        "ffmpeg", "-y", "-i", str(path),
                        "-map", f"0:s:{track_index}",
                        output_path,
                    ]
                    subprocess.run(extract_cmd, capture_output=True)

                    if Path(output_path).exists():
                        with open(output_path, "r", encoding="utf-8", errors="replace") as f:
                            result["subtitle_text"] = f.read()

                        result["success"] = True
                        result["output_file"] = output_path
                        result["format"] = "srt"
            except Exception as e:
                result["error"] = str(e)

        return result

    def get_supported_formats(self) -> List[str]:
        """Get list of supported video formats."""
        return list(self.SUPPORTED_FORMATS)

    # ==================== Internal Helpers ====================

    def _check_ffmpeg(self) -> bool:
        """Check if ffmpeg is available on the system."""
        try:
            import subprocess
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _get_info_opencv(self, path: Path, ext: str, file_size: int) -> VideoInfo:
        """Get video info using OpenCV."""
        import cv2

        cap = cv2.VideoCapture(str(path))

        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

            duration = frame_count / fps if fps > 0 else 0
            bitrate = int((file_size * 8) / max(duration, 1))

            codec_int = int(cap.get(cv2.CAP_PROP_FOURCC))
            codec = "".join([
                chr((codec_int >> 8 * i) & 0xFF)
                for i in range(4)
            ]).strip("\x00")

            resolution = f"{width}x{height}"

            return VideoInfo(
                file_path=str(path),
                file_size=file_size,
                duration_seconds=round(duration, 3),
                width=width,
                height=height,
                resolution=resolution,
                codec=codec or "unknown",
                fps=round(fps, 2),
                bitrate=bitrate,
                format=ext,
                frame_count=frame_count,
            )
        finally:
            cap.release()

    def _get_info_ffmpeg(self, path: Path, ext: str, file_size: int) -> VideoInfo:
        """Get video info using ffprobe."""
        try:
            import subprocess
            import json

            cmd = [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            info = json.loads(result.stdout)

            video_stream = None
            audio_stream = None

            for stream in info.get("streams", []):
                if stream.get("codec_type") == "video" and video_stream is None:
                    video_stream = stream
                elif stream.get("codec_type") == "audio" and audio_stream is None:
                    audio_stream = stream

            fmt = info.get("format", {})
            duration = float(fmt.get("duration", 0))
            bitrate = int(fmt.get("bit_rate", 0))

            width = 0
            height = 0
            fps = 0.0
            codec = ""
            frame_count = 0

            if video_stream:
                width = int(video_stream.get("width", 0))
                height = int(video_stream.get("height", 0))
                codec = video_stream.get("codec_name", "")

                # Parse FPS from r_frame_rate
                fps_str = video_stream.get("r_frame_rate", "0/1")
                if "/" in fps_str:
                    num, den = fps_str.split("/")
                    if int(den) > 0:
                        fps = float(num) / float(den)

                frame_count = int(video_stream.get("nb_frames", 0))
                if frame_count == 0 and fps > 0:
                    frame_count = int(duration * fps)

            audio_codec = ""
            audio_channels = 0
            audio_sample_rate = 0
            if audio_stream:
                audio_codec = audio_stream.get("codec_name", "")
                audio_channels = int(audio_stream.get("channels", 0))
                audio_sample_rate = int(audio_stream.get("sample_rate", 0))

            return VideoInfo(
                file_path=str(path),
                file_size=file_size,
                duration_seconds=round(duration, 3),
                width=width,
                height=height,
                resolution=f"{width}x{height}",
                codec=codec,
                fps=round(fps, 2),
                bitrate=bitrate,
                audio_codec=audio_codec,
                audio_channels=audio_channels,
                audio_sample_rate=audio_sample_rate,
                format=ext,
                frame_count=frame_count,
            )
        except Exception:
            return self._get_info_basic(path, ext, file_size)

    def _get_info_basic(self, path: Path, ext: str, file_size: int) -> VideoInfo:
        """Get basic video info from file header (limited)."""
        width = 0
        height = 0
        duration = 0.0

        try:
            # Try to parse MP4 header
            if ext == "mp4" or ext == "m4v":
                width, height, duration = self._parse_mp4_header(path)
        except Exception:
            pass

        return VideoInfo(
            file_path=str(path),
            file_size=file_size,
            duration_seconds=round(duration, 3),
            width=width,
            height=height,
            resolution=f"{width}x{height}" if width and height else "unknown",
            codec="unknown",
            fps=0.0,
            bitrate=int((file_size * 8) / max(duration, 1)),
            format=ext,
        )

    def _parse_mp4_header(self, path: Path) -> Tuple[int, int, float]:
        """Parse basic info from MP4 file header."""
        width = 0
        height = 0
        duration = 0.0

        try:
            with open(path, "rb") as f:
                # Read enough of the file to find moov atom
                data = f.read(1024 * 1024)  # Read 1MB

            # Find mvhd atom (movie header) for duration
            mvhd_pos = data.find(b"mvhd")
            if mvhd_pos > 0 and mvhd_pos + 32 < len(data):
                # mvhd atom: version(1) + flags(3) + ctime(4) + mtime(4)
                # + timescale(4) + duration(4)
                timescale_pos = mvhd_pos + 20
                if timescale_pos + 8 < len(data):
                    timescale = struct.unpack(">I", data[timescale_pos:timescale_pos+4])[0]
                    duration_val = struct.unpack(">I", data[timescale_pos+4:timescale_pos+8])[0]
                    if timescale > 0:
                        duration = duration_val / timescale

            # Find tkhd for video dimensions
            tkhd_pos = data.find(b"tkhd")
            if tkhd_pos > 0 and tkhd_pos + 84 < len(data):
                # tkhd has width and height at the end (fixed-point 16.16)
                width_pos = tkhd_pos + 80
                height_pos = tkhd_pos + 84
                if height_pos + 4 < len(data):
                    width_raw = struct.unpack(">I", data[width_pos:width_pos+4])[0]
                    height_raw = struct.unpack(">I", data[height_pos:height_pos+4])[0]
                    width = width_raw >> 16
                    height = height_raw >> 16

        except Exception:
            pass

        return width, height, duration
