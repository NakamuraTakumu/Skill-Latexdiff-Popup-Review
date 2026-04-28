#!/usr/bin/env python3
"""popup placeholders 付きの latexdiff review copy を準備する。

使用法:
  python3 prepare_popup_review.py INPUT.tex
  python3 prepare_popup_review.py INPUT.tex -o OUTPUT.tex
  python3 prepare_popup_review.py INPUT.tex --comments-json COMMENTS.json

この script の処理:
- raw latexdiff TeX を別の review TeX に copy する
- \\begin{document} の前に popup-annotation macro block を inject する
- 各 safe DIF add/delete block の後に空の popup macro を 1 つ挿入する
- direct popup insertion には unsafe な \\item / \\begin / \\end などの
  structural diff blocks を skip する
- R001-D や R002-A のような stable sequential IDs を assign する
- それらの IDs keyed の JSON skeleton を write する

入力:
- INPUT.tex: raw latexdiff artifact

出力:
- OUTPUT.tex: prepared review copy
- COMMENTS.json: detected IDs を key、空文字列を value に持つ JSON object
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path


MACRO_BLOCK = r"""
% popup-review macros begin
\newcommand{\DiffPopup}[2]{%
  \vbox to 0pt{\hsize=0pt
    \vss
    \noindent
    \raise1.3\baselineskip
    \hbox to 0pt{\vsize=0pt
      \pdfannot width 1em height 1em depth 0pt {
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

PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


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
    p.add_argument("input_tex", type=Path, help="raw latexdiff TeX file。")
    p.add_argument(
        "-o",
        "--output-tex",
        type=Path,
        help="prepared review TeX path。default は INPUT stem + _popup_review.tex。",
    )
    p.add_argument(
        "--comments-json",
        type=Path,
        help="JSON skeleton path。default は OUTPUT stem + _comments.json。",
    )
    p.add_argument(
        "--prefix",
        default="R",
        help="ID prefix。default: R",
    )
    p.add_argument(
        "--overwrite-comments",
        action="store_true",
        help="既存 comments JSON を空 skeleton で置き換える。default は既存 file を拒否する。",
    )
    p.add_argument(
        "--resume-comments",
        action="store_true",
        help="既存 comments JSON の key set と string values を検証して値を保持する。",
    )
    return p.parse_args()


def default_output_path(input_tex: Path) -> Path:
    return input_tex.with_name(f"{input_tex.stem}_popup_review.tex")


def default_comments_path(output_tex: Path) -> Path:
    return output_tex.with_name(f"{output_tex.stem}_comments.json")


def ensure_distinct_paths(*named_paths: tuple[str, Path]) -> None:
    seen: dict[Path, str] = {}
    for name, path in named_paths:
        resolved = path.resolve()
        if resolved in seen:
            raise ValueError(f"{seen[resolved]} と {name} が同じ path です: {resolved}")
        seen[resolved] = name


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


def validate_prefix(prefix: str) -> None:
    if not PREFIX_RE.fullmatch(prefix):
        raise ValueError("prefix must match ^[A-Za-z][A-Za-z0-9_]*$")


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


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_json_atomic(path: Path, payload: Mapping[str, str]) -> None:
    write_text_atomic(path, json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n")


def validate_comments_json(path: Path, blocks: list[DiffBlock]) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("comments JSON must be an object")
    expected = {block.id for block in blocks}
    actual = set(payload)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("missing: " + ", ".join(missing[:20]))
        if extra:
            details.append("extra: " + ", ".join(extra[:20]))
        raise ValueError("comments JSON key set does not match prepared IDs; " + "; ".join(details))
    out: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("comments JSON must contain string keys and string values")
        out[key] = value
    return out


def write_comments_json(path: Path, blocks: list[DiffBlock], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise ValueError(
            f"comments JSON already exists; refusing to overwrite: {path}. "
            "--overwrite-comments を使うか別 path を指定してください。"
        )
    payload = {block.id: "" for block in blocks}
    write_json_atomic(path, payload)


def main() -> int:
    args = parse_args()
    if args.resume_comments and args.overwrite_comments:
        raise ValueError("use either --resume-comments or --overwrite-comments")
    input_tex = args.input_tex.resolve()
    output_tex = (args.output_tex or default_output_path(input_tex)).resolve()
    comments_json = (args.comments_json or default_comments_path(output_tex)).resolve()
    ensure_distinct_paths(
        ("raw diff TeX", input_tex),
        ("prepared review TeX", output_tex),
        ("comments JSON", comments_json),
    )
    validate_prefix(args.prefix)

    tex = input_tex.read_text(encoding="utf-8")
    ensure_injectable(tex)
    raw_head, _raw_body = split_document(tex)
    raw_line_offset = raw_head.count("\n") + 1
    prepared_input = inject_macro_block(tex)
    head, body = split_document(prepared_input)
    prepared_body, blocks, skipped_lines = insert_placeholders(body, args.prefix, raw_line_offset)
    prepared_tex = head + prepared_body
    if comments_json.exists():
        if args.resume_comments:
            validate_comments_json(comments_json, blocks)
        elif not args.overwrite_comments:
            raise ValueError(
                f"comments JSON already exists; refusing to overwrite: {comments_json}. "
                "--overwrite-comments を使うか別 path を指定してください。"
            )
    elif args.resume_comments:
        raise ValueError(
            f"--resume-comments requires an existing comments JSON: {comments_json}"
        )

    write_text_atomic(output_tex, prepared_tex)
    if not args.resume_comments:
        write_comments_json(comments_json, blocks, args.overwrite_comments)

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
