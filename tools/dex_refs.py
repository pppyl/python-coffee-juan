"""Find aligned DEX string and method references without executing app code.

This is intentionally a small, dependency-free development helper.  It does
not attempt to decompile control flow; it walks instruction boundaries so a
16-bit operand is not mistaken for an opcode, then reports selected references
with their containing declared method.
"""
from __future__ import annotations

import argparse
import json
import re
import zipfile

try:
    from .dex_inventory import Dex
except ImportError:  # Direct script execution keeps tools/ on sys.path.
    from dex_inventory import Dex


_WIDTHS = [1] * 256
for opcode, width in {
    0x02: 2, 0x03: 3, 0x05: 2, 0x06: 3, 0x08: 2, 0x09: 3,
    0x13: 2, 0x14: 3, 0x15: 2, 0x16: 2, 0x17: 3, 0x18: 5,
    0x19: 2, 0x1A: 2, 0x1B: 3, 0x1C: 2, 0x1F: 2, 0x20: 2,
    0x22: 2, 0x23: 2, 0x24: 3, 0x25: 3, 0x26: 3, 0x29: 2,
    0x2A: 3, 0x2B: 3, 0x2C: 3,
    **{opcode: 2 for opcode in range(0x2D, 0x3E)},
    **{opcode: 2 for opcode in range(0x44, 0x6E)},
    **{opcode: 3 for opcode in range(0x6E, 0x73)},
    **{opcode: 3 for opcode in range(0x74, 0x79)},
    **{opcode: 2 for opcode in range(0x90, 0xB0)},
    **{opcode: 2 for opcode in range(0xD0, 0xE3)},
    0xFA: 4, 0xFB: 4, 0xFC: 3, 0xFD: 3, 0xFE: 2, 0xFF: 2,
}.items():
    _WIDTHS[opcode] = width


def _payload_width(dex: Dex, position: int, size: int) -> int:
    """Return the width of a DEX pseudo-instruction payload in code units."""
    ident = dex.u16(position)
    if ident == 0x0100:  # packed-switch-payload
        return 4 + dex.u16(position + 2) * 2
    if ident == 0x0200:  # sparse-switch-payload
        return 2 + dex.u16(position + 2) * 4
    if ident == 0x0300:  # fill-array-data-payload
        element_width = dex.u16(position + 2)
        element_count = dex.u32(position + 4)
        return 4 + (element_width * element_count + 1) // 2
    return 1


def instructions(dex: Dex, code_offset: int):
    count = dex.u32(code_offset + 12)
    start = code_offset + 16
    index = 0
    while index < count:
        position = start + index * 2
        word = dex.u16(position)
        opcode = word & 0xFF
        width = _payload_width(dex, position, count - index) if opcode == 0 else _WIDTHS[opcode]
        if width <= 0 or index + width > count:
            break
        yield index, position, opcode, width
        index += width


def _method_label(method):
    return {
        "class": method["class"],
        "name": method["name"],
        "args": method["args"],
        "return": method["return"],
        "code_offset": method.get("code_offset", 0),
    }


def scan(apk: str, method_pattern: str, string_pattern: str):
    method_re = re.compile(method_pattern) if method_pattern else None
    string_re = re.compile(string_pattern) if string_pattern else None
    with zipfile.ZipFile(apk) as archive:
        for dex_name in archive.namelist():
            if not re.fullmatch(r"classes\d*\.dex", dex_name):
                continue
            dex = Dex(archive.read(dex_name))
            selected_methods = {
                index
                for index, method in enumerate(dex.methods)
                if method_re and method_re.search(
                    method["class"] + "->" + method["name"] +
                    "(" + "".join(method["args"]) + ")" + method["return"]
                )
            }
            selected_strings = {
                index: value
                for index, value in enumerate(dex.strings)
                if string_re and string_re.search(value)
            }
            for cls in dex.classes:
                for caller in dex.declared(cls)["methods"]:
                    code_offset = caller["code_offset"]
                    if not code_offset:
                        continue
                    hits = []
                    for insn_offset, position, opcode, _ in instructions(dex, code_offset):
                        if opcode in {*range(0x6E, 0x73), *range(0x74, 0x79), 0xFA, 0xFB}:
                            target = dex.u16(position + 2)
                            if target in selected_methods:
                                hits.append({
                                    "kind": "method",
                                    "instruction_offset": insn_offset,
                                    "target": _method_label(dex.methods[target]),
                                })
                        elif opcode == 0x1A:
                            target = dex.u16(position + 2)
                            if target in selected_strings:
                                hits.append({
                                    "kind": "string",
                                    "instruction_offset": insn_offset,
                                    "value": selected_strings[target],
                                })
                        elif opcode == 0x1B:
                            target = dex.u32(position + 2)
                            if target in selected_strings:
                                hits.append({
                                    "kind": "string",
                                    "instruction_offset": insn_offset,
                                    "value": selected_strings[target],
                                })
                    if hits:
                        yield {"dex": dex_name, "caller": _method_label(caller), "hits": hits}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("apk")
    parser.add_argument("--method", default="")
    parser.add_argument("--string", default="")
    args = parser.parse_args()
    for result in scan(args.apk, args.method, args.string):
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
