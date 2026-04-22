#!/usr/bin/env python3
"""Prepare a latexdiff review copy with popup placeholders.

Usage:
  python3 prepare_popup_review.py INPUT.tex
  python3 prepare_popup_review.py INPUT.tex -o OUTPUT.tex
  python3 prepare_popup_review.py INPUT.tex --comments-json COMMENTS.json

What this script does:
- copies the raw latexdiff TeX into a separate review TeX
- injects a popup-annotation macro block before \\begin{document}
- inserts one empty popup macro after each safe DIF add/delete block
- skips structural diff blocks such as \\item / \\begin / \\end that are unsafe
  for direct popup insertion
- assigns stable sequential IDs such as R001-D and R002-A
- writes a JSON skeleton keyed by those IDs

Inputs:
- INPUT.tex: raw latexdiff artifact

Outputs:
- OUTPUT.tex: prepared review copy
- COMMENTS.json: empty JSON object with the detected IDs
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


MACRO_BLOCK = r"""
% popup-review macros begin
\newcommand{\DiffPopup}[2]{%
  \vbox to 0pt{\hsize=0pt
    \vss
    \noindent
    \raise1.3\baselineskip
    \hbox to 0pt{\vsize=0pt
      \pdfannot{
        /Subtype /Text
        /Open false
        /Name /Comment
        /T (#1)
        /C [0 0 1]
        /CA 0.75
        /Subj (#1)
        /Contents <#2>
        /F 4
      }%
      \hss
    }%
  }%
}
% popup-review macros end
""".strip()

BLOCK_RE = re.compile(
    r"""
    (?P<block>
      \\DIF(?P<kind_fl>del|add)beginFL(?P<body_fl>.*?)\\DIF(?P=kind_fl)endFL
      |
      \\DIF(?P<kind_std>del|add)begin(?P<body_std>.*?)\\DIF(?P=kind_std)end
    )
    """,
    re.DOTALL | re.VERBOSE,
)

UNSAFE_TOKENS = (
    r"\begin",
    r"\end",
    r"\item",
    r"\bibitem",
)


@dataclass
class DiffBlock:
    id: str
    kind: str
    placeholder: str
    line: int
    block: str


def is_safe_popup_target(block: str) -> bool:
    return not any(token in block for token in UNSAFE_TOKENS)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input_tex", type=Path, help="Raw latexdiff TeX file.")
    p.add_argument(
        "-o",
        "--output-tex",
        type=Path,
        help="Prepared review TeX path. Defaults to INPUT stem + _popup_review.tex.",
    )
    p.add_argument(
        "--comments-json",
        type=Path,
        help="JSON skeleton path. Defaults to OUTPUT stem + _comments.json.",
    )
    p.add_argument(
        "--prefix",
        default="R",
        help="ID prefix. Default: R",
    )
    return p.parse_args()


def default_output_path(input_tex: Path) -> Path:
    return input_tex.with_name(f"{input_tex.stem}_popup_review.tex")


def default_comments_path(output_tex: Path) -> Path:
    return output_tex.with_name(f"{output_tex.stem}_comments.json")


def ensure_injectable(tex: str) -> None:
    if "popup-review macros begin" in tex or "__COMMENT_" in tex:
        raise ValueError("input already appears to be a prepared popup-review TeX")
    if r"\begin{document}" not in tex:
        raise ValueError(r"could not find \begin{document}")


def inject_macro_block(tex: str) -> str:
    return tex.replace(r"\begin{document}", f"{MACRO_BLOCK}\n\n\\begin{{document}}", 1)


def build_id(prefix: str, index: int, kind: str) -> str:
    letter = "D" if kind == "del" else "A"
    return f"{prefix}{index:03d}-{letter}"


def placeholder_for(diff_id: str) -> str:
    return f"__COMMENT_{diff_id.replace('-', '_')}__"


def split_document(tex: str) -> tuple[str, str]:
    marker = r"\begin{document}"
    head, sep, tail = tex.partition(marker)
    if not sep:
        raise ValueError(r"could not find \begin{document}")
    return head + sep, tail


def insert_placeholders(body: str, prefix: str, line_offset: int) -> tuple[str, list[DiffBlock], list[int]]:
    out: list[str] = []
    blocks: list[DiffBlock] = []
    skipped_lines: list[int] = []
    cursor = 0
    index = 0

    for match in BLOCK_RE.finditer(body):
        block = match.group("block")
        kind = match.group("kind_fl") or match.group("kind_std")
        line = line_offset + body.count("\n", 0, match.start())
        out.append(body[cursor : match.end()])
        cursor = match.end()

        if not is_safe_popup_target(block):
            skipped_lines.append(line)
            continue

        index += 1
        diff_id = build_id(prefix, index, kind)
        placeholder = placeholder_for(diff_id)
        macro = f"\n\\DiffPopup{{{diff_id}}}{{{placeholder}}}"
        out.append(macro)
        blocks.append(
            DiffBlock(
                id=diff_id,
                kind=kind,
                placeholder=placeholder,
                line=line,
                block=block,
            )
        )

    out.append(body[cursor:])
    return "".join(out), blocks, skipped_lines


def write_comments_json(path: Path, blocks: list[DiffBlock]) -> None:
    payload = {block.id: "" for block in blocks}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    input_tex = args.input_tex.resolve()
    output_tex = (args.output_tex or default_output_path(input_tex)).resolve()
    comments_json = (args.comments_json or default_comments_path(output_tex)).resolve()

    tex = input_tex.read_text(encoding="utf-8")
    ensure_injectable(tex)
    tex = inject_macro_block(tex)
    head, body = split_document(tex)
    line_offset = head.count("\n") + 1
    prepared_body, blocks, skipped_lines = insert_placeholders(body, args.prefix, line_offset)
    prepared_tex = head + prepared_body

    output_tex.write_text(prepared_tex, encoding="utf-8")
    write_comments_json(comments_json, blocks)

    print(f"prepared_tex: {output_tex}")
    print(f"comments_json: {comments_json}")
    print(f"diff_blocks: {len(blocks)}")
    print(f"skipped_structural_blocks: {len(skipped_lines)}")
    if skipped_lines:
        preview = ", ".join(str(line) for line in skipped_lines[:20])
        print(f"skipped_lines: {preview}")
    for block in blocks[:20]:
        print(f"{block.id}\t{block.kind}\tline={block.line}")
    if len(blocks) > 20:
        print(f"... {len(blocks) - 20} more")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - CLI failure path
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
