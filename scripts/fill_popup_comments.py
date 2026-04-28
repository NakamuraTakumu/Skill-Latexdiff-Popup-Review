#!/usr/bin/env python3
"""prepared review TeX 内の popup comment placeholders を fill する。

使用法:
  python3 fill_popup_comments.py REVIEW.tex COMMENTS.json
  python3 fill_popup_comments.py REVIEW.tex COMMENTS.json -o FILLED.tex
  python3 fill_popup_comments.py REVIEW.tex COMMENTS.json --in-place

この script の処理:
- __COMMENT_*__ placeholders を含む prepared popup-review TeX を読む
- diff IDs から comment strings への mapping を持つ JSON object を読む
- plain string replacement で placeholders を fill する
- PDF popup annotations が non-ASCII text を mojibake なしで持てるよう、
  comment text を UTF-16BE hex に変換する

入力:
- REVIEW.tex: prepared review TeX produced by prepare_popup_review.py
- COMMENTS.json: {"R001-D": "...", "R002-A": "..."} のような JSON object

出力:
- FILLED.tex または in-place update
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from prepare_popup_review import write_text_atomic


PLACEHOLDER_RE = re.compile(r"__COMMENT_[A-Za-z0-9_]+__")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("review_tex", type=Path, help="prepared review TeX file。")
    p.add_argument("comments_json", type=Path, help="diff ID keyed の JSON object。")
    p.add_argument(
        "-o",
        "--output-tex",
        type=Path,
        help="filled TeX path。--in-place を使わない場合、default は REVIEW stem + _filled.tex。",
    )
    p.add_argument(
        "--in-place",
        action="store_true",
        help="REVIEW.tex を in place で overwrite する。",
    )
    p.add_argument(
        "--allow-unfilled-placeholders",
        action="store_true",
        help="TeX に残った __COMMENT_*__ placeholders を error にしない。",
    )
    p.add_argument(
        "--allow-placeholder-mismatches",
        action="store_true",
        help="comments JSON の ID に対応する placeholder がない場合や置換後も残る場合を error にしない。",
    )
    p.add_argument(
        "--allow-empty-comments",
        action="store_true",
        help="空または whitespace-only の comment values を許可する。通常は reviewed target の空 comment を拒否する。",
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


def load_comments(path: Path, allow_empty_comments: bool = False) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("comments JSON must be an object keyed by diff ID")
    out: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("comments JSON must contain string keys and string values")
        if not allow_empty_comments and not value.strip():
            raise ValueError(f"empty comment is not allowed for {key}; use --allow-empty-comments to override")
        out[key] = encode_pdf_utf16_hex(value)
    return out


def find_unfilled_placeholders(tex: str) -> list[str]:
    return sorted(set(PLACEHOLDER_RE.findall(tex)))


def fill_comments(review_tex: str, comments: dict[str, str]) -> tuple[str, list[str], list[str], list[str]]:
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

    return filled, missing_placeholders, untouched_placeholders, find_unfilled_placeholders(filled)


def main() -> int:
    args = parse_args()
    review_tex = args.review_tex.resolve()
    comments_json = args.comments_json.resolve()

    if args.in_place and args.output_tex is not None:
        raise ValueError("use either --in-place or --output-tex")

    output_tex = review_tex if args.in_place else (args.output_tex or default_output_path(review_tex)).resolve()
    tex = review_tex.read_text(encoding="utf-8")
    comments = load_comments(comments_json, args.allow_empty_comments)
    filled, missing, untouched, unfilled = fill_comments(tex, comments)
    if (missing or untouched) and not args.allow_placeholder_mismatches:
        details: list[str] = []
        if missing:
            details.append("missing IDs: " + ", ".join(missing[:20]))
        if untouched:
            details.append("remaining IDs: " + ", ".join(untouched[:20]))
        raise ValueError("placeholder mismatch; " + "; ".join(details))
    if unfilled and not args.allow_unfilled_placeholders:
        raise ValueError("unfilled placeholders remain: " + ", ".join(unfilled[:20]))
    write_text_atomic(output_tex, filled)

    print(f"filled_tex: {output_tex}")
    print(f"comments: {len(comments)}")
    print(f"missing_placeholders: {len(missing)}")
    if missing:
        print("missing_ids: " + ", ".join(missing[:20]))
    print(f"remaining_placeholders_for_given_ids: {len(untouched)}")
    if untouched:
        print("remaining_ids: " + ", ".join(untouched[:20]))
    print(f"unfilled_placeholders: {len(unfilled)}")
    if unfilled:
        print("unfilled: " + ", ".join(unfilled[:20]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - CLI failure path
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
