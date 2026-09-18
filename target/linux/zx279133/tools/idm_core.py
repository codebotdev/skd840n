#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline CPU-view IDM inspector. Never opens devices or writes hardware.

The checked-in C header is the format/geometry source of truth. This deliberately
small parser accepts only numeric constants, arithmetic and literal table rows;
it neither preprocesses nor executes C. A new grammar requires an explicit edit.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "files-6.12/drivers/net/ethernet/zte/skd840n-idm-core.h"
RX_NAMES = ("address", "packet_length", "address_type", "omci_flag", "source_port",
            "checksum_correct", "output", "reorder_flag", "gemport_llid_id")
TX_NAMES = ("address", "packet_length", "address_type", "offset", "checksum_enable",
            "inport", "ipv4_ipv6_flag", "tcp_udp_flag", "omci_flag", "l3_offset",
            "l4_offset", "output_port_valid", "output_port", "gemport_llid_id", "queue_id")
REGION_NAMES = ("cpu_rx_desc", "cpu_tx_desc", "rx_normal_bp", "rx_jumbo_bp", "tx_bp",
                "tx_free", "rx_normal_payload", "rx_jumbo_payload", "tx_payload")
VIEW = "stock dump_desc CPU numeric words; NOT a proven raw DMA byte layout"


def expression(text: str, constants: dict[str, int]) -> int:
    text = re.sub(r"\b(0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]+\b", r"\1", text.strip())
    def visit(node: ast.AST) -> int:
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Name) and node.id in constants:
            return constants[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            a, b = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            return a * b
        raise ValueError(f"unsupported constant expression: {text!r}")
    return visit(ast.parse(text, mode="eval").body)


def load_spec(header: Path = HEADER) -> dict:
    source = header.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    constants = {}
    for name, value in re.findall(r"^#define\s+(SKD840N_\w+)\s+([^\n]+)$", source, re.M):
        if name in constants:
            raise ValueError(f"duplicate constant {name}")
        constants[name] = expression(value, constants)
    fields = {}
    for kind, names in (("rx", RX_NAMES), ("tx", TX_NAMES)):
        match = re.search(r"skd840n_idm_" + kind + r"_fields\[\]\s*=\s*\{(.*?)\};", source, re.S)
        if not match:
            raise ValueError(f"missing {kind} field table")
        rows = re.findall(r"\{([^{}]+)\}\s*,?", match[1])
        residue = re.sub(r"\{[^{}]+\}\s*,?", "", match[1]).strip()
        if residue or len(rows) != len(names):
            raise ValueError(f"unexpected {kind} table grammar or field count")
        masks = [0] * 8
        fields[kind] = []
        for name, row in zip(names, rows):
            values = row.split(",")
            if len(values) != 3:
                raise ValueError("a field requires word, mask, shift")
            word, mask, shift = (expression(x, constants) for x in values)
            if not (0 <= word < 8 and 0 <= shift < 32 and 0 < mask <= 0xffffffff):
                raise ValueError(f"invalid {kind} field {name}")
            width_mask = mask >> shift
            if mask & ((1 << shift) - 1) or width_mask & (width_mask + 1):
                raise ValueError(f"non-contiguous {kind} field {name}")
            if masks[word] & mask:
                raise ValueError(f"overlapping {kind} field {name}")
            masks[word] |= mask
            fields[kind].append(dict(name=name, word=word, mask=mask, shift=shift))
    match = re.search(r"skd840n_idm_region_bytes\[SKD840N_IDM_REGIONS\]\s*=\s*\{(.*?)\};", source, re.S)
    if not match:
        raise ValueError("missing region table")
    sizes = [expression(x, constants) for x in match[1].split(",") if x.strip()]
    if len(sizes) != len(REGION_NAMES) or any(x <= 0 or x % 4096 for x in sizes):
        raise ValueError("invalid region sizes")
    return dict(constants=constants, fields=fields, sizes=sizes)


def decode(kind: str, words: list[int], spec: dict) -> dict:
    if kind not in ("rx", "tx") or len(words) != 8:
        raise ValueError("exactly eight CPU-view u32 words are required")
    if any(type(x) is not int or not 0 <= x <= 0xffffffff for x in words):
        raise ValueError("a word must be an unsigned 32-bit integer")
    masks = [0] * 8
    result = {}
    for field in spec["fields"][kind]:
        word, mask, shift = field["word"], field["mask"], field["shift"]
        result[field["name"]] = (words[word] & mask) >> shift
        masks[word] |= mask
    return dict(view=VIEW, direction=kind, fields=result,
                words=[f"{x:08x}" for x in words],
                unclassified_bits=[f"{x & ~m & 0xffffffff:08x}" for x, m in zip(words, masks)],
                warnings=["No ownership/valid, RX reason or hardware queue is inferred.",
                          "Address interpretation and DMA byte order require separate verification.",
                          "Decoded checksum flags do not authorize CHECKSUM_UNNECESSARY."])


def layout(dma: int, allocated: int, spec: dict) -> dict:
    total = sum(spec["sizes"])
    if dma < 0 or dma % 4096 or allocated < total:
        raise ValueError("DMA base must be 4 KiB aligned and allocation must cover every region")
    if dma + total > 1 << 32:
        raise ValueError("layout exceeds the conservative DMA32 address boundary")
    rows, offset = [], 0
    for name, size in zip(REGION_NAMES, spec["sizes"]):
        rows.append(dict(name=name, offset=f"0x{offset:x}", bytes=size, dma=f"0x{dma + offset:x}"))
        offset += size
    return dict(offline_only=True, bytes_used=total, regions=rows,
                hardware_recv_desc="cpu_tx_desc", hardware_sent_desc="cpu_rx_desc",
                warning="A layout is NOT an allocation, a reservation or permission to start DMA.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for kind in ("rx", "tx"):
        p = sub.add_parser(kind, help="decode exactly eight numeric CPU-view words")
        p.add_argument("words", nargs=8, help="hexadecimal words from the stock CPU-side dump")
    p = sub.add_parser("layout", help="calculate addresses without accessing memory")
    p.add_argument("--dma-base", required=True, type=lambda x: int(x, 0))
    p.add_argument("--allocated-bytes", required=True, type=lambda x: int(x, 0))
    try:
        args = parser.parse_args(argv)
        spec = load_spec()
        if args.command == "layout":
            output = layout(args.dma_base, args.allocated_bytes, spec)
        else:
            if any(not re.fullmatch(r"(?:0[xX])?[0-9a-fA-F]{1,8}", x) for x in args.words):
                raise ValueError("words must contain at most eight hexadecimal digits")
            output = decode(args.command, [int(x, 16) for x in args.words], spec)
        print(json.dumps(output, indent=2))
        return 0
    except (OSError, ValueError, SyntaxError) as exc:
        print(f"idm_core: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
