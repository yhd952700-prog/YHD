"""
Audio Processing Plugin for LiuHao AI OS

Provides audio processing including format conversion, trimming,
volume adjustment, info extraction, and speech-to-text interface.

Capabilities:
- get_info: Get audio file information
- convert_format: Convert audio format
- trim: Trim audio clip
- splice: Splice/concatenate audio files
- adjust_volume: Adjust audio volume
- extract_info: Extract detailed audio metadata
- speech_to_text: Speech-to-text interface
"""

import os
import struct
import wave
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
class AudioInfo:
    """Information about an audio file."""
    file_path: str
    file_size: int
    duration_seconds: float
    sample_rate: int
    channels: int
    bit_depth: int
    format: str
    bitrate: int = 0
    codec: str = ""


class AudioPlugin(BasePlugin):
    """Audio processing plugin with manipulation and analysis capabilities.

    Supports format conversion, trimming, volume adjustment,
    info extraction, and speech-to-text interface.

    Example:
        plugin = AudioPlugin()
        plugin.initialize()
        plugin.activate()

        info = plugin.get_info("audio.mp3")
        print(info.duration_seconds)
    """

    SUPPORTED_FORMATS = [
        "wav", "mp3", "ogg", "flac", "aac", "wma", "m4a", "aiff", "opus",
    ]

    def __init__(self) -> None:
        """Initialize the audio plugin."""
        metadata = PluginMetadata(
            name="audio",
            version="1.0.0",
            description="Audio processing and analysis plugin",
            author="LiuHao AI OS Team",
            categories=["media", "audio", "utility"],
            keywords=["audio", "sound", "music", "convert", "trim", "speech"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._has_pydub: bool = False
        self._has_pocketsphinx: bool = False

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the audio plugin."""
        try:
            try:
                from pydub import AudioSegment
                self._has_pydub = True
            except ImportError:
                self._has_pydub = False

            try:
                import speech_recognition
                self._has_pocketsphinx = True
            except ImportError:
                self._has_pocketsphinx = False

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the audio plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the audio plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the audio plugin."""
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
            "convert_format",
            "trim",
            "splice",
            "adjust_volume",
            "extract_info",
            "speech_to_text",
            "batch_convert",
        ]

    # ==================== Audio Processing Methods ====================

    def get_info(self, audio_path: str) -> AudioInfo:
        """Get information about an audio file.

        Args:
            audio_path: Path to the audio file.

        Returns:
            AudioInfo with audio metadata.
        """
        self._ensure_active()
        self._increment_call_count()

        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        file_size = path.stat().st_size
        ext = path.suffix.lower().lstrip(".")

        if self._has_pydub:
            return self._get_info_pydub(path, ext, file_size)
        elif ext == "wav":
            return self._get_info_wav(path, file_size)
        else:
            return AudioInfo(
                file_path=str(path),
                file_size=file_size,
                duration_seconds=0,
                sample_rate=0,
                channels=0,
                bit_depth=0,
                format=ext,
                bitrate=0,
                codec=ext,
            )

    def convert_format(
        self,
        input_path: str,
        output_path: str,
        target_format: Optional[str] = None,
        bitrate: str = "192k",
        sample_rate: Optional[int] = None,
    ) -> bool:
        """Convert audio file format.

        Args:
            input_path: Path to input audio file.
            output_path: Path to save converted audio.
            target_format: Target format (mp3, wav, ogg, etc.).
                Inferred from output extension if None.
            bitrate: Target bitrate (e.g., '192k', '320k').
            sample_rate: Target sample rate in Hz.

        Returns:
            True if conversion succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_pydub:
            raise PluginExecutionError(
                "pydub library is required for audio conversion"
            )

        from pydub import AudioSegment

        # Determine input format
        input_ext = Path(input_path).suffix.lower().lstrip(".")
        audio = AudioSegment.from_file(input_path, format=input_ext)

        # Adjust sample rate if requested
        if sample_rate:
            audio = audio.set_frame_rate(sample_rate)

        # Determine output format
        if target_format:
            out_format = target_format.lower()
        else:
            out_format = Path(output_path).suffix.lower().lstrip(".")

        # Export
        export_kwargs = {"format": out_format}
        if out_format in ("mp3", "aac", "ogg"):
            export_kwargs["bitrate"] = bitrate

        audio.export(output_path, **export_kwargs)
        return True

    def trim(
        self,
        input_path: str,
        output_path: str,
        start_seconds: float,
        end_seconds: float,
    ) -> bool:
        """Trim an audio clip.

        Args:
            input_path: Path to input audio file.
            output_path: Path to save trimmed audio.
            start_seconds: Start time in seconds.
            end_seconds: End time in seconds.

        Returns:
            True if trimming succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_pydub:
            raise PluginExecutionError("pydub library is required for trimming")

        from pydub import AudioSegment

        input_ext = Path(input_path).suffix.lower().lstrip(".")
        audio = AudioSegment.from_file(input_path, format=input_ext)

        start_ms = int(start_seconds * 1000)
        end_ms = int(end_seconds * 1000)

        trimmed = audio[start_ms:end_ms]

        output_ext = Path(output_path).suffix.lower().lstrip(".")
        trimmed.export(output_path, format=output_ext)
        return True

    def splice(
        self,
        input_paths: List[str],
        output_path: str,
        crossfade_ms: int = 0,
    ) -> bool:
        """Splice (concatenate) multiple audio files.

        Args:
            input_paths: List of audio file paths to concatenate.
            output_path: Path to save combined audio.
            crossfade_ms: Crossfade duration in milliseconds.

        Returns:
            True if splicing succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_pydub:
            raise PluginExecutionError("pydub library is required for splicing")

        from pydub import AudioSegment

        if not input_paths:
            raise ValueError("At least one input path is required")

        # Load first audio
        first_ext = Path(input_paths[0]).suffix.lower().lstrip(".")
        combined = AudioSegment.from_file(input_paths[0], format=first_ext)

        # Append remaining audio files
        for path in input_paths[1:]:
            ext = Path(path).suffix.lower().lstrip(".")
            next_audio = AudioSegment.from_file(path, format=ext)
            if crossfade_ms > 0:
                combined = combined.append(next_audio, crossfade=crossfade_ms)
            else:
                combined = combined + next_audio

        output_ext = Path(output_path).suffix.lower().lstrip(".")
        combined.export(output_path, format=output_ext)
        return True

    def adjust_volume(
        self,
        input_path: str,
        output_path: str,
        volume_change_db: float,
    ) -> bool:
        """Adjust audio volume.

        Args:
            input_path: Path to input audio file.
            output_path: Path to save adjusted audio.
            volume_change_db: Volume change in decibels.
                Positive = louder, negative = quieter.

        Returns:
            True if volume adjustment succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_pydub:
            raise PluginExecutionError("pydub library is required")

        from pydub import AudioSegment

        input_ext = Path(input_path).suffix.lower().lstrip(".")
        audio = AudioSegment.from_file(input_path, format=input_ext)

        adjusted = audio + volume_change_db  # pydub uses dB addition

        output_ext = Path(output_path).suffix.lower().lstrip(".")
        adjusted.export(output_path, format=output_ext)
        return True

    def normalize_volume(
        self,
        input_path: str,
        output_path: str,
        target_dbfs: float = -14.0,
    ) -> bool:
        """Normalize audio volume to a target dBFS level.

        Args:
            input_path: Path to input audio file.
            output_path: Path to save normalized audio.
            target_dbfs: Target loudness in dBFS.

        Returns:
            True if normalization succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_pydub:
            raise PluginExecutionError("pydub library is required")

        from pydub import AudioSegment, effects

        input_ext = Path(input_path).suffix.lower().lstrip(".")
        audio = AudioSegment.from_file(input_path, format=input_ext)

        # Normalize
        change_in_db = target_dbfs - audio.dBFS
        normalized = audio.apply_gain(change_in_db)

        output_ext = Path(output_path).suffix.lower().lstrip(".")
        normalized.export(output_path, format=output_ext)
        return True

    def extract_info(self, audio_path: str) -> Dict[str, Any]:
        """Extract detailed audio metadata.

        Args:
            audio_path: Path to the audio file.

        Returns:
            Dictionary with detailed audio information.
        """
        self._ensure_active()
        self._increment_call_count()

        info = self.get_info(audio_path)
        result = {
            "file_path": info.file_path,
            "file_size": info.file_size,
            "file_size_mb": round(info.file_size / (1024 * 1024), 2),
            "duration_seconds": info.duration_seconds,
            "duration_formatted": self._format_duration(info.duration_seconds),
            "sample_rate": info.sample_rate,
            "channels": info.channels,
            "channel_type": "stereo" if info.channels == 2 else "mono" if info.channels == 1 else f"{info.channels}ch",
            "bit_depth": info.bit_depth,
            "format": info.format,
            "bitrate": info.bitrate,
            "codec": info.codec,
        }

        # Add additional info if pydub is available
        if self._has_pydub:
            try:
                from pydub import AudioSegment
                input_ext = Path(audio_path).suffix.lower().lstrip(".")
                audio = AudioSegment.from_file(audio_path, format=input_ext)

                result.update({
                    "rms": audio.rms,
                    "max_dBFS": audio.max_dBFS,
                    "dBFS": audio.dBFS,
                    "max_possible_dBFS": audio.max_possible_dBFS,
                    "samples": len(audio.get_array_of_samples()),
                })
            except Exception:
                pass

        return result

    def speech_to_text(
        self,
        audio_path: str,
        language: str = "en-US",
    ) -> Dict[str, Any]:
        """Convert speech in audio file to text.

        This is an interface method that uses available speech recognition
        backends. Quality depends on the available backend.

        Args:
            audio_path: Path to the audio file.
            language: Language code (e.g., 'en-US', 'zh-CN').

        Returns:
            Dict with 'text', 'confidence', and 'success' keys.
        """
        self._ensure_active()
        self._increment_call_count()

        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        result = {
            "text": "",
            "confidence": 0.0,
            "success": False,
            "error": None,
            "backend": "none",
        }

        # Try with speech_recognition library
        if self._has_pocketsphinx:
            try:
                import speech_recognition as sr

                recognizer = sr.Recognizer()

                # Convert to WAV first if needed
                wav_path = audio_path
                if path.suffix.lower() != ".wav" and self._has_pydub:
                    from pydub import AudioSegment
                    wav_path = str(path.with_suffix(".temp.wav"))
                    audio = AudioSegment.from_file(audio_path)
                    audio.export(wav_path, format="wav")

                with sr.AudioFile(wav_path) as source:
                    audio_data = recognizer.record(source)

                try:
                    # Try CMU Sphinx (offline)
                    text = recognizer.recognize_sphinx(audio_data, language=language)
                    result["text"] = text
                    result["confidence"] = 0.7
                    result["success"] = True
                    result["backend"] = "sphinx"
                except Exception as e:
                    result["error"] = str(e)

                # Clean up temp file
                if wav_path != audio_path and Path(wav_path).exists():
                    os.unlink(wav_path)

            except Exception as e:
                result["error"] = str(e)

        return result

    def batch_convert(
        self,
        input_dir: str,
        output_dir: str,
        target_format: str = "mp3",
        bitrate: str = "192k",
    ) -> int:
        """Batch convert audio files in a directory.

        Args:
            input_dir: Directory containing audio files.
            output_dir: Directory to save converted files.
            target_format: Target format.
            bitrate: Target bitrate.

        Returns:
            Number of files converted.
        """
        self._ensure_active()

        input_path = Path(input_dir)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        count = 0
        for audio_file in input_path.iterdir():
            if audio_file.suffix.lower().lstrip(".") in self.SUPPORTED_FORMATS:
                try:
                    out_file = output_path / audio_file.with_suffix(f".{target_format}").name
                    self.convert_format(
                        str(audio_file), str(out_file),
                        target_format=target_format, bitrate=bitrate,
                    )
                    count += 1
                except Exception:
                    continue

        return count

    # ==================== Internal Helpers ====================

    def _get_info_pydub(self, path: Path, ext: str, file_size: int) -> AudioInfo:
        """Get audio info using pydub."""
        from pydub import AudioSegment

        audio = AudioSegment.from_file(str(path), format=ext)

        return AudioInfo(
            file_path=str(path),
            file_size=file_size,
            duration_seconds=len(audio) / 1000.0,
            sample_rate=audio.frame_rate,
            channels=audio.channels,
            bit_depth=audio.sample_width * 8,
            format=ext,
            bitrate=int((file_size * 8) / max(audio.duration_seconds, 1)),
            codec=ext,
        )

    def _get_info_wav(self, path: Path, file_size: int) -> AudioInfo:
        """Get WAV file info using Python's wave module."""
        try:
            with wave.open(str(path), "rb") as wav:
                frames = wav.getnframes()
                rate = wav.getframerate()
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()

                duration = frames / float(rate) if rate > 0 else 0

                return AudioInfo(
                    file_path=str(path),
                    file_size=file_size,
                    duration_seconds=round(duration, 3),
                    sample_rate=rate,
                    channels=channels,
                    bit_depth=sample_width * 8,
                    format="wav",
                    bitrate=int(rate * channels * sample_width * 8),
                    codec="PCM",
                )
        except Exception:
            return AudioInfo(
                file_path=str(path),
                file_size=file_size,
                duration_seconds=0,
                sample_rate=0,
                channels=0,
                bit_depth=0,
                format="wav",
            )

    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds to HH:MM:SS."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"
