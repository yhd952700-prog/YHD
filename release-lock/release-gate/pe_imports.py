#!/usr/bin/env python
"""读取 PE 文件的导入表（DLL 依赖），只依赖标准库。

用途：本机没有 dumpbin / llvm-objdump 时，核对 ``etree.pyd`` 是否把
``libiconv-2.dll`` 作为动态依赖，从而判断 iconv 是静态链接还是动态链接。

用法::

    python dist/release-gate/pe_imports.py <pe-file>
    python dist/release-gate/pe_imports.py <pe-file> --has libiconv-2.dll
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path
from typing import List, Tuple


def _rva_to_offset(sections: List[Tuple[int, int, int, int]], rva: int) -> int | None:
    for vaddr, vsize, rawptr, rawsize in sections:
        if vaddr <= rva < vaddr + max(vsize, rawsize):
            delta = rva - vaddr
            if delta < rawsize:
                return rawptr + delta
            return None
    return None


def read_pe_imports(path: Path) -> List[str]:
    """返回 PE32/PE32+ 的导入 DLL 名称（含 delay-load），保持文件内顺序、去重。"""
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise ValueError("不是 PE 文件（缺少 MZ 头）")
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_off:pe_off + 4] != b"PE\0\0":
        raise ValueError("不是 PE 文件（缺少 PE 签名）")

    coff = pe_off + 4
    num_sections = struct.unpack_from("<H", data, coff + 2)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x10B:       # PE32
        dd_off = opt + 96
    elif magic == 0x20B:     # PE32+
        dd_off = opt + 112
    else:
        raise ValueError("未知的可选头 magic: 0x%X" % magic)

    num_dirs = struct.unpack_from("<I", data, dd_off - 4)[0]
    section_off = opt + opt_size
    sections: List[Tuple[int, int, int, int]] = []
    for index in range(num_sections):
        base = section_off + index * 40
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, base + 8)
        sections.append((vaddr, vsize, rawptr, rawsize))

    def directory(idx: int) -> Tuple[int, int]:
        if idx >= num_dirs:
            return 0, 0
        return struct.unpack_from("<II", data, dd_off + idx * 8)

    def name_at(rva: int) -> str:
        offset = _rva_to_offset(sections, rva)
        if offset is None:
            return ""
        end = data.find(b"\0", offset)
        if end < 0:
            return ""
        return data[offset:end].decode("ascii", "replace")

    found: List[str] = []

    # 常规导入表：20 字节描述符，Name 在 +12
    rva, _ = directory(1)
    if rva:
        offset = _rva_to_offset(sections, rva)
        while offset:
            desc = struct.unpack_from("<IIIII", data, offset)
            if desc == (0, 0, 0, 0, 0):
                break
            name = name_at(desc[3])
            if name:
                found.append(name)
            offset += 20

    # Delay-load 导入表：32 字节描述符，Name 在 +4
    rva, _ = directory(13)
    if rva:
        offset = _rva_to_offset(sections, rva)
        while offset:
            desc = struct.unpack_from("<IIIIIIII", data, offset)
            if not any(desc):
                break
            name = name_at(desc[1])
            if name:
                found.append(name)
            offset += 32

    seen = set()
    unique: List[str] = []
    for name in found:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(name)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(description="读取 PE 导入 DLL 列表")
    parser.add_argument("path", type=Path)
    parser.add_argument("--has", metavar="DLL", help="断言导入表中包含该 DLL（大小写不敏感）")
    args = parser.parse_args()

    imports = read_pe_imports(args.path)
    for name in imports:
        print(name)
    if args.has:
        wanted = args.has.casefold()
        ok = any(name.casefold() == wanted for name in imports)
        print(("[OK] 依赖 %s" if ok else "[FAIL] 未依赖 %s") % args.has)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
