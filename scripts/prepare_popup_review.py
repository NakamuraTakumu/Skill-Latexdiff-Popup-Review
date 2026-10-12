#!/usr/bin/env python3
"""popup placeholders 付きの latexdiff review copy を準備する保守用 helper。

通常の popup review workflow では、この script を直接入口にしない。
Git revisions から `popup_review_wizard.py` を起動し、wizard 経由でこの処理を使う。

使用法:
  python3 prepare_popup_review.py INPUT.tex
  python3 prepare_popup_review.py INPUT.tex -o OUTPUT.tex
  python3 prepare_popup_review.py INPUT.tex --comments-json COMMENTS.json

この script の処理:
- raw latexdiff TeX を別の review TeX に copy する
- \\begin{document} の前に popup-annotation macro block を inject する
- 各 DIF add/delete block に空の popup macro を 1 つ対応させる
- \\item / \\begin / \\end などの structural diff blocks では、その場に
  popup macro を挿入せず、次の安全な位置へ移して挿入する
- R001-D や R002-A のような stable sequential IDs を assign する
- それらの IDs keyed の JSON skeleton を write する

入力:
- INPUT.tex: wizard または保守作業で得た raw latexdiff artifact

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

# TeX arguments to these commands are written to auxiliary files and are
# therefore ``moving arguments``.  The popup macro uses boxes and a PDF
# annotation primitive; leaving it in one of these arguments makes LaTeX fail
# (and, for captions/section headings, can also poison the table of contents).
# Keep this list deliberately conservative: a false positive merely moves an
# annotation to the end of an argument, while a false negative breaks a build.
MOVING_ARGUMENT_COMMANDS = frozenset(
    {
        "caption",
        "captionof",
        "chapter",
        "section",
        "subsection",
        "subsubsection",
        "paragraph",
        "subparagraph",
        "part",
        "title",
        "author",
        "thanks",
    }
)

PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass
class DiffBlock:
    id: str
    kind: str
    placeholder: str
    line: int
    block: str
    relocated: bool = False

    @property
    def change_type(self) -> str:
        """Return the public manifest change type for this diff block."""
        return "delete" if self.kind == "del" else "add"

    def to_manifest_entry(self, order: int) -> dict[str, object]:
        """Serialize deterministic source-to-popup correspondence metadata."""
        return {
            "id": self.id,
            "order": order,
            "change_type": self.change_type,
            "label": self.change_type,
            "kind": self.kind,
            "placeholder": self.placeholder,
            "source_position": {"line": self.line, "column": 1},
            "relocated": self.relocated,
            "source_text": self.block,
        }


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
        "--popup-manifest",
        type=Path,
        help="popup IDとsource位置のmanifest path。指定時だけ生成する。",
    )
    p.add_argument(
        "--prefix",
        default="R",
        help="ID prefix。default: R",
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


def popup_macro(diff_id: str, placeholder: str) -> str:
    return f"\n\\DiffPopup{{{diff_id}}}{{{placeholder}}}\n"


def split_document(tex: str) -> tuple[str, str]:
    marker = r"\begin{document}"
    head, sep, tail = tex.partition(marker)
    if not sep:
        raise ValueError(r"could not find \begin{document}")
    return head + sep, tail


def split_document_end(body: str) -> tuple[str, str]:
    marker = r"\end{document}"
    head, sep, tail = body.rpartition(marker)
    if not sep:
        return body, ""
    return head, sep + tail


def _skip_tex_space(tex: str, position: int) -> int:
    """Skip whitespace and comments while parsing a command argument."""
    length = len(tex)
    while position < length:
        if tex[position].isspace():
            position += 1
            continue
        if tex[position] == "%":
            newline = tex.find("\n", position)
            position = length if newline < 0 else newline + 1
            continue
        break
    return position


def _balanced_delimiter_end(tex: str, opening: int, left: str, right: str) -> int | None:
    """Return the exclusive end of a balanced TeX delimiter, or ``None``."""
    if opening >= len(tex) or tex[opening] != left:
        return None
    depth = 1
    nested_braces = 0
    position = opening + 1
    while position < len(tex):
        char = tex[position]
        if char == "%":
            newline = tex.find("\n", position)
            position = len(tex) if newline < 0 else newline + 1
            continue
        if char == "\\":
            # Escaped braces/brackets are text, not delimiters.  A control
            # symbol consumes the following character; a control word is
            # consumed as one token as well.
            position += 1
            if position < len(tex) and tex[position].isalpha():
                while position < len(tex) and tex[position].isalpha():
                    position += 1
            elif position < len(tex):
                position += 1
            continue
        if left == "[":
            # Optional moving arguments may contain nested groups with a
            # literal closing bracket.  Treat those brackets as text.
            if char == "{":
                nested_braces += 1
            elif char == "}" and nested_braces:
                nested_braces -= 1
            elif not nested_braces and char == left:
                depth += 1
            elif not nested_braces and char == right:
                depth -= 1
                if depth == 0:
                    return position + 1
        elif char == left:
            depth += 1
        elif char == right:
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    return None


def moving_argument_regions(tex: str) -> tuple[tuple[int, int], ...]:
    """Find moving-argument spans as ``(content_start, end)``.

    The scanner handles starred commands, optional short arguments, nested
    braces, escaped delimiters, and comments.  Unknown/malformed commands are
    ignored so preparation can still provide diagnostics for the original
    latexdiff artifact.
    """
    regions: list[tuple[int, int]] = []
    position = 0
    length = len(tex)
    while position < length:
        if tex[position] == "%":
            newline = tex.find("\n", position)
            position = length if newline < 0 else newline + 1
            continue
        if tex[position] != "\\":
            position += 1
            continue
        command_start = position
        position += 1
        if position >= length or not (tex[position].isalpha() or tex[position] == "@"):
            position += 1
            continue
        name_start = position
        while position < length and (tex[position].isalpha() or tex[position] == "@"):
            position += 1
        command = tex[name_start:position]
        if command not in MOVING_ARGUMENT_COMMANDS:
            continue
        if position < length and tex[position] == "*":
            position += 1
        position = _skip_tex_space(tex, position)
        optional_start: int | None = None
        if position < length and tex[position] == "[":
            optional_start = position
            optional_end = _balanced_delimiter_end(tex, position, "[", "]")
            if optional_end is None:
                continue
            position = _skip_tex_space(tex, optional_end)
        if position >= length or tex[position] != "{":
            if optional_start is not None:
                # Keep a malformed command's optional argument protected even
                # when no mandatory argument follows it.
                regions.append((optional_start + 1, position))
            continue
        argument_end = _balanced_delimiter_end(tex, position, "{", "}")
        if argument_end is None:
            continue
        if optional_start is not None:
            # A popup in the short/optional argument must be emitted after the
            # mandatory argument too; placing it between ``[...]`` and ``{}``
            # would make the command consume the popup macro as its argument.
            regions.append((optional_start + 1, argument_end))
        regions.append((position + 1, argument_end))
        # Continue scanning after the command name.  Nested moving commands
        # inside this argument are discovered by the normal scan and are
        # handled conservatively by choosing the outermost enclosing region at
        # injection time.
        position = command_start + 1
    return tuple(regions)


def moving_argument_end(position: int, regions: tuple[tuple[int, int], ...]) -> int | None:
    """Return the end of the outermost moving argument containing position."""
    containing = [end for start, end in regions if start <= position < end]
    return max(containing) if containing else None


def flush_pending(out: list[str], pending_macros: list[str]) -> None:
    if not pending_macros:
        return
    out.extend(pending_macros)
    pending_macros.clear()


def insert_placeholders(body: str, prefix: str, line_offset: int) -> tuple[str, list[DiffBlock], list[int]]:
    blocks: list[DiffBlock] = []
    relocated_lines: list[int] = []
    body_without_end, document_end = split_document_end(body)
    regions = moving_argument_regions(body_without_end)
    matches = tuple(BLOCK_RE.finditer(body_without_end))

    # Build insertion events against offsets in the original body.  Keeping
    # the source offsets separate from the rewritten text lets us place a
    # popup after a complete ``\\caption{...}``/``\\section{...}`` argument,
    # including when the diff block itself contains structural tokens.
    events: dict[int, list[tuple[int, str]]] = {}
    for index, match in enumerate(matches, 1):
        block = match.group("block")
        kind = match.group("kind_fl") or match.group("kind_std")
        line = line_offset + body_without_end.count("\n", 0, match.start())
        diff_id = build_id(prefix, index, kind)
        placeholder = placeholder_for(diff_id)
        moving_end = moving_argument_end(match.start(), regions)
        relocated = moving_end is not None or not is_safe_popup_target(block)
        macro = popup_macro(diff_id, placeholder)

        if moving_end is not None:
            # Structural relocation is preferred to dropping an ID.  The
            # event is after the enclosing argument's closing brace, so the
            # popup primitive never appears in a moving argument.
            insertion = moving_end
        elif not is_safe_popup_target(block):
            # Preserve the legacy "pending until a meaningful safe boundary"
            # behavior for structural blocks outside moving arguments.
            next_match = matches[index] if index < len(matches) else None
            next_start = next_match.start() if next_match is not None else len(body_without_end)
            gap = body_without_end[match.end() : next_start]
            # With whitespace-only separation the old pending queue flushed
            # after the next block and before that block's own safe popup.  Use
            # the same anchor so manifest/source order remains popup order.
            insertion = (
                next_start
                if gap.strip() or next_match is None
                else next_match.end()
            )
            # A pending structural macro must not accidentally be flushed
            # inside a later moving argument.
            enclosing_end = moving_argument_end(insertion, regions)
            if enclosing_end is not None:
                insertion = enclosing_end
        else:
            insertion = match.end()

        events.setdefault(insertion, []).append((index, macro))
        if relocated:
            relocated_lines.append(line)
        blocks.append(
            DiffBlock(
                id=diff_id,
                kind=kind,
                placeholder=placeholder,
                line=line,
                block=block,
                relocated=relocated,
            )
        )

    out: list[str] = []
    cursor = 0
    for event_position in sorted(events):
        position = event_position
        event_list = events[event_position]
        if position < cursor:
            # This can only arise from malformed overlapping regions.  Keep
            # the source text intact and emit the event at the current cursor,
            # which remains outside any already-consumed argument.
            position = cursor
        out.append(body_without_end[cursor:position])
        out.extend(macro for _index, macro in sorted(event_list, key=lambda item: item[0]))
        cursor = position
    out.append(body_without_end[cursor:])
    out.append(document_end)
    return "".join(out), blocks, relocated_lines


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


def write_comments_json(path: Path, blocks: list[DiffBlock]) -> None:
    if path.exists():
        raise ValueError(
            f"comments JSON already exists; refusing to overwrite: {path}. "
            "--resume-comments を使うか別 path を指定してください。"
        )
    payload = {block.id: "" for block in blocks}
    write_json_atomic(path, payload)


def popup_manifest_payload(blocks: list[DiffBlock]) -> dict[str, object]:
    """Build the deterministic popup manifest consumed by the Job workflow."""
    entries = [block.to_manifest_entry(index) for index, block in enumerate(blocks, 1)]
    return {
        "schema_version": 1,
        "popup_ids": [entry["id"] for entry in entries],
        "blocks": entries,
    }


def write_popup_manifest(path: Path, blocks: list[DiffBlock]) -> None:
    """Write a popup manifest without allowing an existing artifact to be replaced."""
    if path.exists():
        raise ValueError(f"popup manifest already exists; refusing overwrite: {path}")
    write_text_atomic(
        path,
        json.dumps(popup_manifest_payload(blocks), ensure_ascii=False, indent=2) + "\n",
    )


def main() -> int:
    args = parse_args()
    input_tex = args.input_tex.resolve()
    output_tex = (args.output_tex or default_output_path(input_tex)).resolve()
    comments_json = (args.comments_json or default_comments_path(output_tex)).resolve()
    popup_manifest = args.popup_manifest.resolve() if args.popup_manifest else None
    named_paths = [
        ("raw diff TeX", input_tex),
        ("prepared review TeX", output_tex),
        ("comments JSON", comments_json),
    ]
    if popup_manifest is not None:
        named_paths.append(("popup manifest", popup_manifest))
    ensure_distinct_paths(*named_paths)
    validate_prefix(args.prefix)

    tex = input_tex.read_text(encoding="utf-8")
    ensure_injectable(tex)
    raw_head, _raw_body = split_document(tex)
    raw_line_offset = raw_head.count("\n") + 1
    prepared_input = inject_macro_block(tex)
    head, body = split_document(prepared_input)
    prepared_body, blocks, relocated_lines = insert_placeholders(body, args.prefix, raw_line_offset)
    prepared_tex = head + prepared_body
    if comments_json.exists():
        if args.resume_comments:
            validate_comments_json(comments_json, blocks)
        else:
            raise ValueError(
                f"comments JSON already exists; refusing to overwrite: {comments_json}. "
                "--resume-comments を使うか別 path を指定してください。"
            )
    elif args.resume_comments:
        raise ValueError(
            f"--resume-comments requires an existing comments JSON: {comments_json}"
        )

    write_text_atomic(output_tex, prepared_tex)
    if not args.resume_comments:
        write_comments_json(comments_json, blocks)
    if popup_manifest is not None:
        write_popup_manifest(popup_manifest, blocks)

    print(f"prepared_tex: {output_tex}")
    print(f"comments_json: {comments_json}")
    if popup_manifest is not None:
        print(f"popup_manifest: {popup_manifest}")
    print(f"diff_blocks: {len(blocks)}")
    print(f"relocated_structural_blocks: {len(relocated_lines)}")
    if relocated_lines:
        preview = ", ".join(str(line) for line in relocated_lines[:20])
        print(f"relocated_lines: {preview}")
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
