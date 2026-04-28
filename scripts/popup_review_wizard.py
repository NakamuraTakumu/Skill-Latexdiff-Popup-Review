#!/usr/bin/env python3
"""Git revisions から popup-comment latexdiff review PDF までを作る。

使用法:
  python3 popup_review_wizard.py --output-dir out --master-tex main.tex --from-commit OLD --to-commit NEW
  python3 popup_review_wizard.py BASELINE_HASH --master-tex main.tex
  python3 popup_review_wizard.py BASELINE_HASH
  python3 popup_review_wizard.py BASELINE_HASH --master-tex main.tex --no-build
  python3 popup_review_wizard.py BASELINE_HASH --master-tex main.tex --generate-only

この script の処理:
- commit hash と master LaTeX file から latexdiff-vc を実行する
- raw latexdiff TeX に popup placeholders を追加する
- Codex に comments JSON 作成指示を出し、完了後に検証する
- comments を filled review TeX に挿入する
- filled review TeX を build command で PDF にする
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from fill_popup_comments import default_output_path as default_filled_path
from fill_popup_comments import fill_comments, load_comments
from prepare_popup_review import DiffBlock
from prepare_popup_review import default_comments_path, default_output_path
from prepare_popup_review import ensure_injectable, inject_macro_block, insert_placeholders
from prepare_popup_review import split_document
from prepare_popup_review import validate_prefix


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("baseline_hash", nargs="?", help="legacy mode: latexdiff-vc の -r に渡す Git hash。例: HEAD、abc1234")
    p.add_argument("--from-commit", help="比較元 Git hash。--to-commit と併用すると two-revision mode になる。")
    p.add_argument("--to-commit", help="比較先 Git hash。--from-commit と併用する。")
    p.add_argument("--output-dir", type=Path, help="workflow artifact output directory。")
    p.add_argument(
        "--master-tex",
        type=Path,
        help=(
            "master LaTeX file。省略時は対話で入力する。"
            "--project-root 省略時の相対 path は実行 cwd 基準、"
            "--project-root 指定時の相対 path は project root 基準。"
        ),
    )
    p.add_argument(
        "--project-root",
        type=Path,
        help="Git-backed LaTeX project root。省略時は master TeX から git root を検出する。",
    )
    p.add_argument(
        "-d",
        "--diff-dir",
        type=Path,
        help="latexdiff-vc output directory。省略時は --output-dir、legacy mode では diff。",
    )
    p.add_argument("--review-tex", type=Path, help="prepared review TeX path。")
    p.add_argument("--comments-json", type=Path, help="comments JSON path。")
    p.add_argument("--filled-tex", type=Path, help="filled review TeX path。")
    p.add_argument(
        "--prefix",
        default="R",
        help="popup ID prefix。default: R",
    )
    p.add_argument(
        "--latexdiff-command",
        default="latexdiff-vc",
        help="latexdiff-vc command path。default: latexdiff-vc",
    )
    p.add_argument(
        "--build-command",
        default="latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir={pdf_dir} {tex}",
        help="PDF build command。{tex} は build cwd からの相対 TeX path、{pdf_output}/{pdf_dir} は expected PDF path に置換される。",
    )
    p.add_argument(
        "--build-cwd",
        type=Path,
        help="build command の working directory。default: master TeX file の directory",
    )
    p.add_argument("--pdf-output", type=Path, help="expected generated PDF path。省略時は filled TeX の拡張子を .pdf にする。")
    p.add_argument("--generate-only", action="store_true", help="latexdiff-vc で raw diff TeX を生成して終了する。")
    p.add_argument("--no-build", action="store_true", help="filled TeX 作成後に PDF build を行わない。")
    p.add_argument(
        "--keep-build-files",
        action="store_true",
        help="PDF build 成功後も LaTeX の一時補助ファイルを残す。default は削除する。",
    )
    p.add_argument(
        "--overwrite-comments",
        action="store_true",
        help="既存 comments JSON を空 skeleton で置き換える。default は既存 file を拒否する。",
    )
    p.add_argument(
        "--resume-comments",
        action="store_true",
        help="既存 comments JSON の key set を検証して値を保持する。",
    )
    p.add_argument(
        "--create-build-cwd",
        action="store_true",
        help="--build-cwd が存在しない場合に作成する。default は既存 directory を要求する。",
    )
    p.add_argument(
        "--allow-empty-comments",
        action="store_true",
        help="空 comment を許可する。通常は未入力を許可しない。",
    )
    p.add_argument(
        "--manual-comments",
        action="store_true",
        help="Codex handoff ではなく diff ID ごとの対話入力で comments JSON を作る。",
    )
    return p.parse_args()


def prompt_path(label: str) -> Path:
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return Path(value)
        print("path を入力してください。")


def resolve_under_root(path: Path, root: Path) -> Path:
    return path if path.is_absolute() else root / path


def resolve_optional_under_root(path: Path | None, root: Path) -> Path | None:
    if path is None:
        return None
    return resolve_under_root(path, root).resolve()


def git_root_for(path: Path) -> Path:
    start = path if path.is_dir() else path.parent
    out = subprocess.check_output(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        text=True,
    )
    return Path(out.strip()).resolve()


def relative_to_root(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(root.resolve()))
    except ValueError:
        return str(resolved)


def tex_files(path: Path) -> set[Path]:
    if not path.exists():
        return set()
    return {p.resolve() for p in path.rglob("*.tex") if p.is_file()}


def newest_tex(path: Path) -> Path:
    candidates = [p for p in path.rglob("*.tex") if p.is_file()]
    if not candidates:
        raise ValueError(f"latexdiff output TeX が見つかりません: {path}")
    return max(candidates, key=lambda p: p.stat().st_mtime).resolve()


def ensure_distinct_paths(*named_paths: tuple[str, Path]) -> None:
    seen: dict[Path, str] = {}
    for name, path in named_paths:
        resolved = path.resolve()
        if resolved in seen:
            raise ValueError(f"{seen[resolved]} と {name} が同じ path です: {resolved}")
        seen[resolved] = name


def write_json_atomic(path: Path, payload: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_comments_skeleton(path: Path, blocks: list[DiffBlock], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise ValueError(
            f"comments JSON already exists; refusing unsafe reuse or overwrite: {path}. "
            "別 path を指定するか --resume-comments または --overwrite-comments を使ってください。"
        )
    payload: dict[str, str] = {block.id: "" for block in blocks}
    write_json_atomic(path, payload)


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


def run_latexdiff(
    baseline_hash: str,
    target_hash: str | None,
    master_tex: Path,
    project_root: Path,
    diff_dir: Path,
    command: str,
) -> Path:
    diff_dir_abs = resolve_under_root(diff_dir, project_root).resolve()
    diff_dir_arg = relative_to_root(diff_dir_abs, project_root)
    master_arg = relative_to_root(master_tex, project_root)
    before = tex_files(diff_dir_abs)
    cmd = [
        command,
        "--git",
        "--flatten",
        "--force",
        "-d",
        diff_dir_arg,
        "-r",
        baseline_hash,
    ]
    if target_hash is not None:
        cmd.extend(["-r", target_hash])
    cmd.append(master_arg)
    print("Running: " + " ".join(shlex.quote(part) for part in cmd))
    subprocess.run(cmd, cwd=project_root, check=True)
    after = tex_files(diff_dir_abs)
    created = sorted(after - before)
    if created:
        return max(created, key=lambda p: p.stat().st_mtime)
    return newest_tex(diff_dir_abs)


def write_source_diff(project_root: Path, from_commit: str, to_commit: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        subprocess.run(
            ["git", "diff", "--no-ext-diff", from_commit, to_commit, "--", "."],
            cwd=project_root,
            check=True,
            stdout=handle,
            text=True,
        )


def prepare_review_tex(
    raw_tex: Path,
    review_tex: Path | None,
    comments_json: Path | None,
    prefix: str,
    overwrite_comments: bool,
    resume_comments: bool,
) -> tuple[Path, Path, list[DiffBlock], list[int]]:
    if overwrite_comments and resume_comments:
        raise ValueError("use either --resume-comments or --overwrite-comments")
    output_tex = (review_tex or default_output_path(raw_tex)).resolve()
    comments_path = (comments_json or default_comments_path(output_tex)).resolve()
    ensure_distinct_paths(
        ("raw diff TeX", raw_tex),
        ("prepared review TeX", output_tex),
        ("comments JSON", comments_path),
    )

    tex = raw_tex.read_text(encoding="utf-8")
    ensure_injectable(tex)
    raw_head, _ = split_document(tex)
    raw_line_offset = raw_head.count("\n") + 1
    tex = inject_macro_block(tex)
    head, body = split_document(tex)
    prepared_body, blocks, skipped_lines = insert_placeholders(body, prefix, raw_line_offset)
    if comments_path.exists():
        if resume_comments:
            validate_comments_json(comments_path, blocks)
        elif not overwrite_comments:
            raise ValueError(
                f"comments JSON already exists; refusing unsafe reuse or overwrite: {comments_path}. "
                "別 path を指定するか --resume-comments または --overwrite-comments を使ってください。"
            )
    elif resume_comments:
        raise ValueError(f"--resume-comments requires an existing comments JSON: {comments_path}")
    output_tex.parent.mkdir(parents=True, exist_ok=True)
    output_tex.write_text(head + prepared_body, encoding="utf-8")
    if not resume_comments:
        write_comments_skeleton(comments_path, blocks, overwrite_comments)
    return output_tex, comments_path, blocks, skipped_lines


def compact_snippet(text: str, limit: int = 500) -> str:
    snippet = " ".join(text.split())
    if len(snippet) <= limit:
        return snippet
    return snippet[: limit - 3] + "..."


def prompt_comment(block: DiffBlock, previous: str | None, allow_empty: bool) -> str:
    print()
    print(f"{block.id} ({block.kind}, line {block.line})")
    print(compact_snippet(block.block))
    if previous:
        print("Enter: 前回の comment を再利用 / :same: 前回の comment を再利用")
    print("comment を入力してください。")
    while True:
        value = input("> ").strip()
        if value == ":same" and previous:
            return previous
        if value == "" and previous:
            return previous
        if value or allow_empty:
            return value
        print("空 comment は許可されていません。入力してください。")


def collect_comments(comments_json: Path, blocks: list[DiffBlock], allow_empty: bool) -> None:
    payload = json.loads(comments_json.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("comments JSON must be an object")

    previous: str | None = None
    for block in blocks:
        existing = payload.get(block.id, "")
        if isinstance(existing, str) and existing.strip():
            print(f"{block.id}: existing comment を保持します。")
            previous = existing
            continue
        comment = prompt_comment(block, previous, allow_empty)
        payload[block.id] = comment
        write_json_atomic(comments_json, payload)
        if comment:
            previous = comment


def wait_for_codex_comments(comments_json: Path) -> None:
    print()
    print("ACTION REQUIRED: Codex comments JSON handoff")
    print(f"comments_json: {comments_json}")
    print("popup-review-pipeline.md のレビュー方針と Comments JSON 契約に従って comments JSON を完成させてください。")
    print("完了後、この process に Enter を送ると script が検証と PDF build を続行します。")
    print("process が終了した場合は、同じ command に --resume-comments を付けて再実行してください。")
    try:
        input("Press Enter after comments JSON is complete...")
    except EOFError:
        raise RuntimeError("interactive stdin is required; rerun in a terminal, or rerun later with --resume-comments") from None


def ensure_complete_comments(comments_json: Path, allow_empty: bool) -> None:
    payload = json.loads(comments_json.read_text(encoding="utf-8"))
    missing = [key for key, value in payload.items() if not isinstance(value, str) or not value.strip()]
    if missing and not allow_empty:
        preview = ", ".join(missing[:20])
        raise ValueError(f"comment が空の ID があります: {preview}")


def fill_review_tex(review_tex: Path, comments_json: Path, filled_tex: Path | None, allow_empty: bool) -> Path:
    output_tex = (filled_tex or default_filled_path(review_tex)).resolve()
    ensure_distinct_paths(("prepared review TeX", review_tex), ("filled review TeX", output_tex))
    tex = review_tex.read_text(encoding="utf-8")
    comments = load_comments(comments_json, allow_empty)
    filled, missing, untouched, unfilled = fill_comments(tex, comments)
    if missing:
        raise ValueError("placeholder が見つからない ID があります: " + ", ".join(missing[:20]))
    if untouched:
        raise ValueError("置換されずに残った ID があります: " + ", ".join(untouched[:20]))
    if unfilled:
        raise ValueError("未置換 placeholder が残っています: " + ", ".join(unfilled[:20]))
    output_tex.parent.mkdir(parents=True, exist_ok=True)
    output_tex.write_text(filled, encoding="utf-8")
    return output_tex


LATEX_BUILD_EXTENSIONS = (
    ".aux",
    ".bbl",
    ".bcf",
    ".blg",
    ".fdb_latexmk",
    ".fls",
    ".lof",
    ".log",
    ".lot",
    ".out",
    ".run.xml",
    ".synctex.gz",
    ".toc",
)


def cleanup_latex_build_files(filled_tex: Path, expected_pdf: Path) -> list[Path]:
    build_dir = expected_pdf.parent
    removed: list[Path] = []
    for ext in LATEX_BUILD_EXTENSIONS:
        path = build_dir / f"{filled_tex.stem}{ext}"
        if not path.exists() or path.resolve() in {filled_tex.resolve(), expected_pdf.resolve()}:
            continue
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed


def build_pdf(
    filled_tex: Path,
    build_command: str,
    build_cwd: Path,
    pdf_output: Path | None,
    create_build_cwd: bool,
    keep_build_files: bool,
) -> Path:
    if create_build_cwd:
        build_cwd.mkdir(parents=True, exist_ok=True)
    if not build_cwd.is_dir():
        raise ValueError(f"build cwd does not exist or is not a directory: {build_cwd}")
    tex_arg = relative_to_root(filled_tex, build_cwd)
    expected_pdf = (pdf_output or filled_tex.with_suffix(".pdf")).resolve()
    pdf_arg = relative_to_root(expected_pdf, build_cwd)
    cmd_text = build_command.format(
        tex=tex_arg,
        tex_name=filled_tex.name,
        tex_path=str(filled_tex),
        pdf_output=pdf_arg,
        pdf_path=str(expected_pdf),
        pdf_dir=relative_to_root(expected_pdf.parent, build_cwd),
    )
    cmd = shlex.split(cmd_text)
    print("Running: " + " ".join(shlex.quote(part) for part in cmd))
    subprocess.run(cmd, cwd=build_cwd, check=True)
    if not expected_pdf.exists():
        raise ValueError(f"PDF が見つかりません: {expected_pdf}")
    if not keep_build_files:
        removed = cleanup_latex_build_files(filled_tex, expected_pdf)
        if removed:
            print("cleaned_build_files: " + ", ".join(relative_to_root(path, build_cwd) for path in removed))
    return expected_pdf


def main() -> int:
    args = parse_args()

    if args.from_commit or args.to_commit:
        if not args.from_commit or not args.to_commit:
            raise ValueError("--from-commit and --to-commit must be used together")
        if args.baseline_hash:
            raise ValueError("do not combine positional BASELINE_HASH with --from-commit/--to-commit")
        baseline_hash = args.from_commit
        target_hash = args.to_commit
    else:
        if not args.baseline_hash:
            raise ValueError("provide BASELINE_HASH or --from-commit/--to-commit")
        baseline_hash = args.baseline_hash
        target_hash = None

    if args.master_tex is None:
        master_input = prompt_path("master TeX file")
    else:
        master_input = args.master_tex

    if args.project_root is None:
        master_tex = master_input.resolve()
        project_root = git_root_for(master_tex)
    else:
        project_root = args.project_root.resolve()
    if not project_root.is_dir():
        raise ValueError(f"project root does not exist or is not a directory: {project_root}")
    validate_prefix(args.prefix)
    if args.project_root is not None:
        master_tex = resolve_under_root(master_input, project_root).resolve()
    if not master_tex.exists():
        raise ValueError(
            f"master TeX file が見つかりません: {master_tex}. "
            "--project-root を指定していない場合、相対 --master-tex は実行 cwd 基準で解決されます。"
        )
    output_dir = resolve_under_root(args.output_dir, project_root).resolve() if args.output_dir else None
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
    diff_dir = args.diff_dir or output_dir or Path("diff")
    review_tex = resolve_optional_under_root(args.review_tex, project_root)
    comments_json = resolve_optional_under_root(args.comments_json, project_root)
    filled_tex_arg = resolve_optional_under_root(args.filled_tex, project_root)
    build_cwd = resolve_under_root(args.build_cwd, project_root).resolve() if args.build_cwd else master_tex.parent
    pdf_output = resolve_optional_under_root(args.pdf_output, project_root)
    if output_dir is not None:
        review_tex = review_tex or (output_dir / "popup_review.tex")
        comments_json = comments_json or (output_dir / "popup_comments.json")
        filled_tex_arg = filled_tex_arg or (output_dir / "popup_review_filled.tex")
        pdf_output = pdf_output or (output_dir / "popup_review_filled.pdf")

    raw_tex = run_latexdiff(
        baseline_hash=baseline_hash,
        target_hash=target_hash,
        master_tex=master_tex,
        project_root=project_root,
        diff_dir=diff_dir,
        command=args.latexdiff_command,
    )
    print(f"raw_diff_tex: {raw_tex}")
    if args.generate_only:
        return 0

    source_diff = None
    if output_dir is not None and target_hash is not None:
        source_diff = output_dir / "source.diff"
        write_source_diff(project_root, baseline_hash, target_hash, source_diff)
        print(f"source_diff: {source_diff}")

    review_tex, comments_json, blocks, skipped_lines = prepare_review_tex(
        raw_tex=raw_tex,
        review_tex=review_tex,
        comments_json=comments_json,
        prefix=args.prefix,
        overwrite_comments=args.overwrite_comments,
        resume_comments=args.resume_comments,
    )
    print(f"prepared_review_tex: {review_tex}")
    print(f"comments_json: {comments_json}")
    print(f"diff_blocks: {len(blocks)}")
    print(f"skipped_structural_blocks: {len(skipped_lines)}")

    if args.manual_comments or source_diff is None:
        collect_comments(comments_json, blocks, args.allow_empty_comments)
    else:
        wait_for_codex_comments(comments_json)
    ensure_complete_comments(comments_json, args.allow_empty_comments)

    filled_tex = fill_review_tex(review_tex, comments_json, filled_tex_arg, args.allow_empty_comments)
    print(f"filled_tex: {filled_tex}")

    if args.no_build:
        print("build: skipped")
        return 0

    pdf = build_pdf(
        filled_tex,
        args.build_command,
        build_cwd,
        pdf_output,
        args.create_build_cwd,
        args.keep_build_files,
    )
    print(f"pdf: {pdf}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:  # pragma: no cover - CLI failure path
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
