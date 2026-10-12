"""
Compression Tools Plugin for LiuHao AI OS

Provides string compression/decompression (RLE, Huffman interface),
compression ratio calculation, multiple algorithms, and statistics.

Capabilities:
- compress_rle: Compress with Run-Length Encoding
- decompress_rle: Decompress RLE data
- compress_dict: Dictionary-based compression (LZW-like)
- compression_ratio: Calculate compression ratio
- get_statistics: Compression statistics
- huffman_interface: Huffman coding parameters
"""

from typing import Dict, Any, Optional, List, Tuple
from collections import Counter
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class CompressionResult:
    """Result of a compression operation."""
    input: str
    output: str
    algorithm: str
    original_size: int
    compressed_size: int
    ratio: float  # compressed / original
    savings: float  # 1 - ratio
    encoding_table: Optional[Dict[str, str]] = None


class CompressionPlugin(BasePlugin):
    """String compression and decompression plugin.

    Supports Run-Length Encoding (RLE), dictionary-based
    compression (LZW-style), compression ratio calculation,
    and provides Huffman coding interface parameters.

    Example:
        plugin = CompressionPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.compress_rle("AAAABBBCCDAA")
        print(result.output)  # "4A3B2C1D2A"
        print(result.ratio)
    """

    def __init__(self) -> None:
        """Initialize the compression plugin."""
        metadata = PluginMetadata(
            name="compression",
            version="1.0.0",
            description="String compression with RLE, dictionary coding, and statistics",
            author="LiuHao AI OS Team",
            categories=["compression", "encoding", "utility"],
            keywords=["compression", "decompression", "rle", "huffman", "lzw", "encoding"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the compression plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the compression plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the compression plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the compression plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "compress_rle",
            "decompress_rle",
            "compress_dict",
            "decompress_dict",
            "compression_ratio",
            "frequency_analysis",
            "huffman_codes",
            "get_statistics",
        ]

    # ==================== Run-Length Encoding ====================

    def compress_rle(self, text: str) -> CompressionResult:
        """Compress text using Run-Length Encoding.

        Encodes consecutive repeated characters as count+char.
        E.g., "AAABBB" -> "3A3B"

        Args:
            text: Text to compress.

        Returns:
            CompressionResult with RLE-encoded text.
        """
        self._ensure_active()
        self._increment_call_count()

        if not text:
            return CompressionResult(
                input=text,
                output="",
                algorithm="rle",
                original_size=0,
                compressed_size=0,
                ratio=0.0,
                savings=0.0,
            )

        result: List[str] = []
        count = 1
        current = text[0]

        for ch in text[1:]:
            if ch == current:
                count += 1
            else:
                result.append(f"{count}{current}")
                current = ch
                count = 1

        result.append(f"{count}{current}")
        output = ''.join(result)

        original_size = len(text.encode('utf-8'))
        compressed_size = len(output.encode('utf-8'))
        ratio = compressed_size / original_size if original_size > 0 else 0.0

        return CompressionResult(
            input=text,
            output=output,
            algorithm="rle",
            original_size=original_size,
            compressed_size=compressed_size,
            ratio=round(ratio, 4),
            savings=round(1 - ratio, 4),
        )

    def decompress_rle(self, encoded: str) -> CompressionResult:
        """Decompress RLE-encoded text.

        Args:
            encoded: RLE-encoded string (count+char pairs).

        Returns:
            CompressionResult with decompressed text.

        Raises:
            ValueError: If encoded string is invalid.
        """
        self._ensure_active()
        self._increment_call_count()

        if not encoded:
            return CompressionResult(
                input=encoded,
                output="",
                algorithm="rle_decompress",
                original_size=0,
                compressed_size=0,
                ratio=0.0,
                savings=0.0,
            )

        result: List[str] = []
        i = 0
        n = len(encoded)

        while i < n:
            # Parse count
            count_str = ''
            while i < n and encoded[i].isdigit():
                count_str += encoded[i]
                i += 1

            if not count_str:
                raise ValueError(
                    f"Invalid RLE at position {i}: expected count"
                )

            count = int(count_str)

            if i >= n:
                raise ValueError(
                    f"Invalid RLE at position {i}: expected character"
                )

            char = encoded[i]
            i += 1

            result.append(char * count)

        output = ''.join(result)

        original_size = len(encoded.encode('utf-8'))
        compressed_size = len(output.encode('utf-8'))

        return CompressionResult(
            input=encoded,
            output=output,
            algorithm="rle_decompress",
            original_size=original_size,
            compressed_size=compressed_size,
            ratio=round(original_size / compressed_size, 4)
            if compressed_size > 0 else 0.0,
            savings=round(1 - original_size / compressed_size, 4)
            if compressed_size > 0 else 0.0,
        )

    # ==================== Dictionary (LZW-style) Compression ====================

    def compress_dict(self, text: str) -> CompressionResult:
        """Compress text using dictionary-based (LZW-style) encoding.

        Builds a dictionary of sequences and outputs codes.

        Args:
            text: Text to compress.

        Returns:
            CompressionResult with comma-separated codes and table.
        """
        self._ensure_active()
        self._increment_call_count()

        if not text:
            return CompressionResult(
                input=text,
                output="",
                algorithm="lzw",
                original_size=0,
                compressed_size=0,
                ratio=0.0,
                savings=0.0,
                encoding_table={},
            )

        # Initialize dictionary with single characters
        dict_size = 256
        dictionary: Dict[str, int] = {}
        for i in range(dict_size):
            dictionary[chr(i)] = i

        result_codes: List[int] = []
        current = ''

        for ch in text:
            combined = current + ch
            if combined in dictionary:
                current = combined
            else:
                result_codes.append(dictionary[current])
                dictionary[combined] = dict_size
                dict_size += 1
                current = ch

        if current:
            result_codes.append(dictionary[current])

        output = ','.join(str(c) for c in result_codes)

        # Calculate sizes
        original_size = len(text.encode('utf-8'))
        # Approximate compressed size: codes in binary
        bits_per_code = max(9, dict_size.bit_length())
        compressed_bits = len(result_codes) * bits_per_code
        compressed_size = (compressed_bits + 7) // 8

        ratio = compressed_size / original_size if original_size > 0 else 0.0

        # Build encoding table for display
        encoding_table = {
            k: str(v) for k, v in dictionary.items()
            if len(k) > 1  # Only show multi-char entries
        }

        return CompressionResult(
            input=text,
            output=output,
            algorithm="lzw",
            original_size=original_size,
            compressed_size=compressed_size,
            ratio=round(ratio, 4),
            savings=round(1 - ratio, 4),
            encoding_table=encoding_table,
        )

    def decompress_dict(self, codes_str: str) -> CompressionResult:
        """Decompress LZW-encoded data.

        Args:
            codes_str: Comma-separated integer codes.

        Returns:
            CompressionResult with decompressed text.
        """
        self._ensure_active()
        self._increment_call_count()

        if not codes_str:
            return CompressionResult(
                input=codes_str,
                output="",
                algorithm="lzw_decompress",
                original_size=0,
                compressed_size=0,
                ratio=0.0,
                savings=0.0,
            )

        codes = [int(c.strip()) for c in codes_str.split(',') if c.strip()]

        # Initialize dictionary
        dict_size = 256
        dictionary: Dict[int, str] = {}
        for i in range(dict_size):
            dictionary[i] = chr(i)

        result: List[str] = []
        current = dictionary[codes[0]]
        result.append(current)

        for code in codes[1:]:
            if code in dictionary:
                entry = dictionary[code]
            elif code == dict_size:
                entry = current + current[0]
            else:
                raise ValueError(f"Invalid LZW code: {code}")

            result.append(entry)
            dictionary[dict_size] = current + entry[0]
            dict_size += 1
            current = entry

        output = ''.join(result)

        original_size = len(codes_str.encode('utf-8'))
        compressed_size = len(output.encode('utf-8'))

        return CompressionResult(
            input=codes_str,
            output=output,
            algorithm="lzw_decompress",
            original_size=original_size,
            compressed_size=compressed_size,
            ratio=round(original_size / compressed_size, 4)
            if compressed_size > 0 else 0.0,
            savings=round(1 - original_size / compressed_size, 4)
            if compressed_size > 0 else 0.0,
        )

    # ==================== Compression Ratio ====================

    def compression_ratio(
        self,
        original: str,
        compressed: str,
    ) -> Dict[str, Any]:
        """Calculate compression ratio and statistics.

        Args:
            original: Original data.
            compressed: Compressed data.

        Returns:
            Dict with compression statistics.
        """
        self._ensure_active()
        self._increment_call_count()

        orig_bytes = len(original.encode('utf-8'))
        comp_bytes = len(compressed.encode('utf-8'))

        ratio = comp_bytes / orig_bytes if orig_bytes > 0 else 0.0
        savings = 1 - ratio

        return {
            "original_size_bytes": orig_bytes,
            "compressed_size_bytes": comp_bytes,
            "size_difference": orig_bytes - comp_bytes,
            "ratio": round(ratio, 6),
            "savings_percent": round(savings * 100, 2),
            "compression_factor": round(orig_bytes / comp_bytes, 2)
            if comp_bytes > 0 else 0.0,
            "bits_per_char_original": round(orig_bytes * 8 / len(original), 2)
            if original else 0.0,
            "bits_per_char_compressed": round(comp_bytes * 8 / len(compressed), 2)
            if compressed else 0.0,
        }

    # ==================== Frequency Analysis ====================

    def frequency_analysis(self, text: str) -> Dict[str, Any]:
        """Perform frequency analysis on text.

        Args:
            text: Text to analyze.

        Returns:
            Dict with frequency data and entropy.
        """
        self._ensure_active()
        self._increment_call_count()

        if not text:
            return {
                "total_chars": 0,
                "unique_chars": 0,
                "frequencies": {},
                "entropy": 0.0,
                "min_bits_per_char": 0.0,
            }

        counter = Counter(text)
        total = len(text)
        unique = len(counter)

        # Calculate Shannon entropy
        import math
        entropy = 0.0
        frequencies: Dict[str, Dict[str, Any]] = {}

        for char, count in counter.most_common():
            freq = count / total
            entropy -= freq * math.log2(freq)
            frequencies[char] = {
                "count": count,
                "frequency": round(freq, 6),
                "percentage": round(freq * 100, 2),
            }

        return {
            "total_chars": total,
            "unique_chars": unique,
            "frequencies": frequencies,
            "entropy": round(entropy, 4),
            "min_bits_per_char": round(entropy, 4),
            "theoretical_min_size_bytes": round(total * entropy / 8, 2),
        }

    # ==================== Huffman Coding ====================

    def huffman_codes(self, text: str) -> Dict[str, Any]:
        """Generate Huffman codes for text.

        Builds a Huffman tree and generates optimal prefix codes
        based on character frequencies.

        Args:
            text: Text to encode.

        Returns:
            Dict with codes, tree info, and statistics.
        """
        self._ensure_active()
        self._increment_call_count()

        if not text:
            return {
                "codes": {},
                "total_bits": 0,
                "avg_code_length": 0.0,
                "entropy": 0.0,
                "efficiency": 0.0,
            }

        # Count frequencies
        counter = Counter(text)
        total = len(text)

        # Build Huffman tree using heap
        import heapq

        # Each node: (weight, id, char, left, right)
        # Use unique id to avoid comparison issues
        nodes = []
        uid = 0
        for char, freq in counter.items():
            heapq.heappush(nodes, (freq, uid, char, None, None))
            uid += 1

        # Build tree
        while len(nodes) > 1:
            freq1, id1, char1, left1, right1 = heapq.heappop(nodes)
            freq2, id2, char2, left2, right2 = heapq.heappop(nodes)
            combined_freq = freq1 + freq2
            heapq.heappush(
                nodes, (combined_freq, uid, None, (freq1, id1, char1, left1, right1),
                        (freq2, id2, char2, left2, right2))
            )
            uid += 1

        # Generate codes by traversing tree
        codes: Dict[str, str] = {}

        def traverse(node, code: str) -> None:
            freq, uid, char, left, right = node
            if char is not None:
                codes[char] = code
            else:
                if left:
                    traverse(left, code + '0')
                if right:
                    traverse(right, code + '1')

        if nodes:
            traverse(nodes[0], '')

        # Calculate statistics
        import math
        total_bits = sum(len(codes[c]) * counter[c] for c in counter)
        avg_code_length = total_bits / total

        # Entropy
        entropy = 0.0
        for char, count in counter.items():
            freq = count / total
            entropy -= freq * math.log2(freq)

        efficiency = entropy / avg_code_length if avg_code_length > 0 else 0.0

        # Sort codes by character
        sorted_codes = dict(sorted(codes.items()))

        return {
            "codes": sorted_codes,
            "code_count": len(codes),
            "total_bits": total_bits,
            "total_bytes": (total_bits + 7) // 8,
            "avg_code_length": round(avg_code_length, 4),
            "entropy": round(entropy, 4),
            "efficiency": round(efficiency * 100, 2),
            "original_size_bytes": total,
            "compressed_size_bytes": (total_bits + 7) // 8,
            "compression_ratio": round(
                ((total_bits + 7) // 8) / total, 4
            ) if total > 0 else 0.0,
        }

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "algorithms": ["RLE", "LZW", "Huffman"],
            "supports_compression": True,
            "supports_decompression": True,
            "supports_frequency_analysis": True,
            "rle_best_case": "Highly repetitive data",
            "lzw_best_case": "Text with repeated patterns",
            "huffman_best_case": "Skewed character frequencies",
        }
