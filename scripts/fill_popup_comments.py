#!/usr/bin/env python3
"""Fill popup comment placeholders in a prepared review TeX.

Usage:
  python3 fill_popup_comments.py REVIEW.tex COMMENTS.json
  python3 fill_popup_comments.py REVIEW.tex COMMENTS.json -o FILLED.tex
  python3 fill_popup_comments.py REVIEW.tex COMMENTS.json --in-place

What this script does:
- reads a prepared popup-review TeX with __COMMENT_*__ placeholders
- reads a JSON object mapping diff IDs to comment strings
- fills the placeholders by plain string replacement
- converts comment text to UTF-16BE hex so PDF popup annotations can carry
  non-ASCII text without mojibake

Inputs:
- REVIEW.tex: prepared review TeX produced by prepare_popup_review.py
- COMMENTS.json: JSON object such as {"R001-D": "...", "R002-A": "..."}

Outputs:
- FILLED.tex or in-place update
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("review_tex", type=Path, help="Prepared review TeX file.")
    p.add_argument("comments_json", type=Path, help="JSON object keyed by diff ID.")
    p.add_argument(
        "-o",
        "--output-tex",
        type=Path,
        help="Filled TeX path. Defaults to REVIEW stem + _filled.tex unless --in-place is used.",
    )
    p.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite REVIEW.tex in place.",
    )
    return p.parse_args()


def default_output_path(review_tex: Path) -> Path:
    return review_tex.with_name(f"{review_tex.stem}_filled.tex")


def placeholder_for(diff_id: str) -> str:
    return f"__COMMENT_{diff_id.replace('-', '_')}__"


def encode_pdf_utf16_hex(text: str) -> str:
    normalized = " ".join(text.split())
    if not normalized:
        return ""
    return ("FEFF" + normalized.encode("utf-16-be").hex()).upper()


def load_comments(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("comments JSON must be an object keyed by diff ID")
    out: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("comments JSON must contain string keys and string values")
        out[key] = encode_pdf_utf16_hex(value)
    return out


def fill_comments(review_tex: str, comments: dict[str, str]) -> tuple[str, list[str], list[str]]:
    missing_placeholders: list[str] = []
    untouched_placeholders: list[str] = []
    filled = review_tex

    for diff_id, comment in comments.items():
        placeholder = placeholder_for(diff_id)
        if placeholder not in filled:
            missing_placeholders.append(diff_id)
            continue
        filled = filled.replace(placeholder, comment)

    for diff_id in comments:
        placeholder = placeholder_for(diff_id)
        if placeholder in filled:
            untouched_placeholders.append(diff_id)

    return filled, missing_placeholders, untouched_placeholders


def main() -> int:
    args = parse_args()
    review_tex = args.review_tex.resolve()
    comments_json = args.comments_json.resolve()

    if args.in_place and args.output_tex is not None:
        raise ValueError("use either --in-place or --output-tex")

    output_tex = review_tex if args.in_place else (args.output_tex or default_output_path(review_tex)).resolve()
    tex = review_tex.read_text(encoding="utf-8")
    comments = load_comments(comments_json)
    filled, missing, untouched = fill_comments(tex, comments)
    output_tex.write_text(filled, encoding="utf-8")

    print(f"filled_tex: {output_tex}")
    print(f"comments: {len(comments)}")
    print(f"missing_placeholders: {len(missing)}")
    if missing:
        print("missing_ids: " + ", ".join(missing[:20]))
    print(f"remaining_placeholders_for_given_ids: {len(untouched)}")
    if untouched:
        print("remaining_ids: " + ", ".join(untouched[:20]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - CLI failure path
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
