from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from prepare_popup_review import insert_placeholders, popup_manifest_payload, split_document
import run_popup_review as workflow
from codex_job_runner import (
    CodexExecutionError, CodexResponse, CodexReviewConflictError,
    CodexValidationError, SessionConfig, WorkspaceConfig,
)
from codex_job_runner import job as job_module
from codex_job_runner._models import _CodexAttempt, _ReviewResult


def _fixture(root: Path) -> tuple[workflow.PopupManifest, Path, Path, Path, Path]:
    raw = (
        "\\documentclass{article}\n"
        "\\newcommand{\\DIFdelbegin}{}\n"
        "\\newcommand{\\DIFdelend}{}\n"
        "\\newcommand{\\DIFaddbegin}{}\n"
        "\\newcommand{\\DIFaddend}{}\n"
        "\\newcommand{\\DiffPopup}[2]{}\n"
        "\\begin{document}\n"
        "\\DIFdelbegin old\\DIFdelend\n"
        "\\DIFaddbegin new\\DIFaddend\n"
        "\\end{document}\n"
    )
    head, body = split_document(raw)
    prepared_body, blocks, _ = insert_placeholders(body, "R", head.count("\n") + 1)
    source = root / "source.diff"
    prepared = root / "prepared_review.tex"
    manifest_path = root / "popup_manifest.json"
    diff_pdf = root / "difference.pdf"
    handoff = root / "handoff"
    source.write_text("diff\n", encoding="utf-8")
    prepared.write_text(head + prepared_body, encoding="utf-8")
    manifest_path.write_text(json.dumps(popup_manifest_payload(blocks)), encoding="utf-8")
    diff_pdf.write_bytes(b"%PDF-1.4\n")
    handoff.mkdir()
    comments = {block.id: "[意味維持][OK] 理由" for block in blocks}
    (handoff / "popup_comments.json").write_text(json.dumps(comments), encoding="utf-8")
    return workflow.PopupManifest.from_path(manifest_path), source, prepared, handoff / "popup_comments.json", diff_pdf


class PopupReviewJobTests(unittest.TestCase):
    def test_manifest_and_comments_are_ordered_and_labeled(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, _source, prepared, comments, _pdf = _fixture(Path(tmp))
            result = workflow.validate_popup_comments(comments, manifest, prepared_tex=prepared)
            self.assertEqual(tuple(result), manifest.ids)

    def test_comments_reject_out_of_order_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, _source, _prepared, comments, _pdf = _fixture(Path(tmp))
            payload = json.loads(comments.read_text(encoding="utf-8"))
            comments.write_text(json.dumps(dict(reversed(list(payload.items())))), encoding="utf-8")
            with self.assertRaises(ValueError):
                workflow.validate_popup_comments(comments, manifest)

    def test_comments_reject_repeated_label_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, _source, _prepared, comments, _pdf = _fixture(root)
            payload = json.loads(comments.read_text(encoding="utf-8"))
            first_id = manifest.ids[0]
            payload[first_id] = "[意味維持][意味維持][OK] 理由"
            comments.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                workflow.validate_popup_comments(comments, manifest)
            payload[first_id] = "[意味維持][OK][OK] 理由"
            comments.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                workflow.validate_popup_comments(comments, manifest)

    def test_job_is_pending_and_reviewer_is_read_only_with_pdf_read_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            job = workflow.PopupReviewJob(
                config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                manifest=manifest,
                source_diff=source,
                prepared_tex=prepared,
                comments_skeleton=comments,
                diff_pdf=diff_pdf,
                output_dir=root,
                no_build=True,
            )
            self.assertEqual(job.status, "pending")
            subcwd_paths = (
                "${SUBCWD}/source.diff",
                "${SUBCWD}/prepared_review.tex",
                "${SUBCWD}/popup_manifest.json",
                "${SUBCWD}/difference.pdf",
            )
            self.assertEqual(
                job.config.workspace.read_paths,
                subcwd_paths + (comments.resolve(),),
            )
            self.assertEqual(
                job.review_config.session.workspace.read_paths,
                subcwd_paths + (comments.resolve(),),
            )
            self.assertIsNone(job.config.workspace.subcwd_root)
            self.assertEqual(
                tuple(job.config.workspace.subcwd_files),
                tuple(path.removeprefix("${SUBCWD}/") for path in subcwd_paths),
            )
            self.assertEqual(
                job.config.workspace.subcwd_files["difference.pdf"]["bytes"],
                diff_pdf.read_bytes(),
            )
            self.assertEqual(
                job.review_config.artifact_paths,
                subcwd_paths[:3] + (comments.resolve(),),
            )
            self.assertEqual(job.config.workspace.write_paths, (root / "handoff",))
            self.assertEqual(job.review_config.session.workspace.write_paths, ())
            self.assertTrue(job.config.ignore_global_context)
            self.assertEqual(job.config.allowed_skills, ())
            self.assertTrue(job.review_config.session.ignore_global_context)
            self.assertEqual(job.review_config.session.allowed_skills, ())
            reviewer_context = "\n".join(job.review_config.context)
            for host_path in (source, prepared, manifest.path, diff_pdf):
                self.assertNotIn(str(host_path), job.input)
                self.assertNotIn(str(host_path), reviewer_context)
            self.assertIn("意味保持", job.input)
            self.assertIn("意味保持", reviewer_context)

    def test_stage_input_files_copies_multiple_files_without_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "out"
            output.mkdir()
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            first = first_dir / "notes.txt"
            second = second_dir / "notes.txt"
            first.write_text("first\n", encoding="utf-8")
            second.write_text("second\n", encoding="utf-8")

            staged = workflow.stage_input_files([first, second], output)

            self.assertEqual(tuple(path.name for path in staged), ("0001-notes.txt", "0002-notes.txt"))
            self.assertEqual(staged[0].read_text(encoding="utf-8"), "first\n")
            self.assertEqual(staged[1].read_text(encoding="utf-8"), "second\n")
            self.assertFalse(staged[0].is_symlink())
            self.assertFalse(staged[1].is_symlink())
            self.assertEqual(staged[0].stat().st_mode & 0o222, 0)

    def test_stage_input_files_rejects_missing_nonregular_symlink_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "out"
            output.mkdir()
            regular = root / "input.txt"
            regular.write_text("input\n", encoding="utf-8")
            directory = root / "directory"
            directory.mkdir()
            link = root / "link.txt"
            link.symlink_to(regular)
            ancestor_target = root / "ancestor-target"
            ancestor_target.mkdir()
            ancestor = root / "ancestor"
            ancestor.symlink_to(ancestor_target, target_is_directory=True)

            cases = [
                (root / "missing.txt", "regular file"),
                (directory, "regular file"),
                (link, "symlink"),
                (ancestor / "input.txt", "symlink"),
            ]
            for path, _label in cases:
                with self.subTest(path=path):
                    with self.assertRaises(ValueError):
                        workflow.stage_input_files([path], output)
                    staged_dir = output / workflow.INPUT_FILES_DIR_NAME
                    if staged_dir.exists():
                        for entry in staged_dir.iterdir():
                            entry.unlink()

            with self.assertRaises(ValueError):
                workflow.stage_input_files([regular], output, max_file_bytes=1)
            with self.assertRaises(ValueError):
                workflow.stage_input_files([regular, regular], output, max_total_bytes=1)

    def test_staged_inputs_and_extra_prompt_are_visible_to_main_and_reviewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            external = root / "external.txt"
            external.write_text("attached input\n", encoding="utf-8")
            staged = workflow.stage_input_files([external], root)
            before = tuple(path.read_bytes() for path in staged)
            job = workflow.PopupReviewJob(
                config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                manifest=manifest,
                source_diff=source,
                prepared_tex=prepared,
                comments_skeleton=comments,
                diff_pdf=diff_pdf,
                output_dir=root,
                input_files=staged,
                extra_prompt="Focus on the attached input.",
                no_build=True,
            )

            self.assertEqual(job.status, "pending")
            self.assertEqual(before, tuple(path.read_bytes() for path in staged))
            staged_subcwd_path = f"${{SUBCWD}}/input_files/{staged[0].name}"
            self.assertIn(staged_subcwd_path, job.config.workspace.read_paths)
            self.assertIn(staged_subcwd_path, job.review_config.session.workspace.read_paths)
            self.assertNotIn(staged_subcwd_path, job.review_config.artifact_paths)
            self.assertEqual(
                job.config.workspace.subcwd_files[f"input_files/{staged[0].name}"]["bytes"],
                before[0],
            )
            self.assertIn("Focus on the attached input.", job.input)
            self.assertIn(staged_subcwd_path, job.input)
            reviewer_context = "\n".join(job.review_config.context)
            self.assertIn("Focus on the attached input.", reviewer_context)
            self.assertIn(staged_subcwd_path, reviewer_context)
            self.assertLess(job.input.index("Focus on the attached input."), job.input.index("Main review prompt"))

    def test_job_requires_staged_input_paths_and_does_not_copy_in_constructor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            external = root / "external.txt"
            external.write_text("attached input\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                workflow.PopupReviewJob(
                    config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                    manifest=manifest,
                    source_diff=source,
                    prepared_tex=prepared,
                    comments_skeleton=comments,
                    diff_pdf=diff_pdf,
                    output_dir=root,
                    input_files=[external],
                    no_build=True,
                )
            self.assertFalse((root / workflow.INPUT_FILES_DIR_NAME).exists())

    def test_extra_prompt_size_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            with self.assertRaises(ValueError):
                workflow.PopupReviewJob(
                    config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                    manifest=manifest,
                    source_diff=source,
                    prepared_tex=prepared,
                    comments_skeleton=comments,
                    diff_pdf=diff_pdf,
                    output_dir=root,
                    extra_prompt="x" * (workflow.MAX_EXTRA_PROMPT_BYTES + 1),
                    no_build=True,
                )

    def test_relative_manifest_path_is_resolved_for_model_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, _source, _prepared, _comments, _pdf = _fixture(root)
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                relative = workflow.PopupManifest.from_path(Path("popup_manifest.json"))
            finally:
                os.chdir(old_cwd)
            self.assertTrue(relative.path.is_absolute())
            self.assertEqual(relative.path, (root / "popup_manifest.json").resolve())
            self.assertEqual(manifest.path, (root / "popup_manifest.json").resolve())

    def test_reviewer_receives_codex_bin_from_public_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            config = SessionConfig(
                codex_bin="/opt/codex-custom",
                workspace=WorkspaceConfig(cwd=root),
                allow_shell_commands=False,
            )
            job = workflow.PopupReviewJob(
                config=config,
                manifest=manifest,
                source_diff=source,
                prepared_tex=prepared,
                comments_skeleton=comments,
                diff_pdf=diff_pdf,
                output_dir=root,
                no_build=True,
            )
            self.assertEqual(job.review_config.session.codex_bin, "/opt/codex-custom")

    def test_public_difference_pdf_must_be_nonempty_and_bounded(self):
        for content in (b"", b"x" * (workflow.MAX_PDF_BYTES + 1)):
            with self.subTest(size=len(content)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                manifest, source, prepared, comments, diff_pdf = _fixture(root)
                diff_pdf.write_bytes(content)
                with self.assertRaises(ValueError):
                    workflow.PopupReviewJob(
                        config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                        manifest=manifest,
                        source_diff=source,
                        prepared_tex=prepared,
                        comments_skeleton=comments,
                        diff_pdf=diff_pdf,
                        output_dir=root,
                        no_build=True,
                    )

    def test_unexpected_handoff_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            (root / "handoff" / "unexpected.txt").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unexpected"):
                workflow.PopupReviewJob(
                    config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                    manifest=manifest,
                    source_diff=source,
                    prepared_tex=prepared,
                    comments_skeleton=comments,
                    diff_pdf=diff_pdf,
                    output_dir=root,
                    no_build=True,
                )

    def test_bundled_prompt_resources_are_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            job = workflow.PopupReviewJob(
                config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                manifest=manifest,
                source_diff=source,
                prepared_tex=prepared,
                comments_skeleton=comments,
                diff_pdf=diff_pdf,
                output_dir=root,
                no_build=True,
            )
            self.assertIn("${SUBCWD}/source.diff", job.input)
            reviewer_context = "\n".join(job.review_config.context)
            self.assertIn("${SUBCWD}/difference.pdf", reviewer_context)
            for relation in ("new", "reopens", "revises", "conflicts"):
                self.assertIn(relation, reviewer_context)
            self.assertIn("read-only fork", job.input)
            self.assertIn("finding_responses", job.input)
            self.assertEqual(job.review_config.session.web_search, "disabled")
            self.assertEqual(job.config.web_search, "disabled")
            self.assertTrue(job.config.allow_shell_commands)
            self.assertEqual(job.review_config.session.model, job.config.model)

    def test_main_prompt_has_ordered_constraints_and_rendered_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            job = workflow.PopupReviewJob(
                config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                manifest=manifest,
                source_diff=source,
                prepared_tex=prepared,
                comments_skeleton=comments,
                diff_pdf=diff_pdf,
                output_dir=root,
                extra_prompt="Check the supplied context.",
                no_build=True,
            )

            principles = workflow._read_resource(workflow._REVISION_PRINCIPLES_PATH)
            writing_instructions = workflow._read_resource(workflow._WRITING_INSTRUCTIONS_PATH)
            self.assertIn(principles, job.input)
            self.assertIn(writing_instructions, job.input)
            self.assertLess(job.input.index("Check the supplied context."), job.input.index(principles))
            self.assertLess(job.input.index(principles), job.input.index(writing_instructions))
            self.assertLess(job.input.index(writing_instructions), job.input.index("# Main review prompt"))
            for path in (
                "${SUBCWD}/source.diff",
                "${SUBCWD}/prepared_review.tex",
                "${SUBCWD}/popup_manifest.json",
                "${SUBCWD}/difference.pdf",
                str(comments),
            ):
                self.assertIn(path, job.input)
            self.assertNotIn("{{", job.input)
            self.assertNotIn("}}", job.input)

    def test_main_prompt_fails_clearly_when_writing_instructions_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            with patch.object(workflow, "_WRITING_INSTRUCTIONS_PATH", root / "missing-writing.md"):
                with self.assertRaisesRegex(ValueError, "prompt resource.*missing-writing"):
                    workflow.PopupReviewJob(
                        config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                        manifest=manifest,
                        source_diff=source,
                        prepared_tex=prepared,
                        comments_skeleton=comments,
                        diff_pdf=diff_pdf,
                        output_dir=root,
                        no_build=True,
                    )

    def test_no_build_finalizes_without_pdf_and_does_not_create_run_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            job = workflow.PopupReviewJob(
                config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                manifest=manifest,
                source_diff=source,
                prepared_tex=prepared,
                comments_skeleton=comments,
                diff_pdf=diff_pdf,
                output_dir=root,
                no_build=True,
            )
            completion = job._finalize(SimpleNamespace(log_path="job.log"))
            self.assertEqual(completion.mode, "diagnostic")
            self.assertIsNone(completion.pdf_path)
            self.assertFalse((root / "review.pdf").exists())
            self.assertFalse((root / "run_manifest.json").exists())
            self.assertNotIn("__COMMENT_", (root / "filled_review.tex").read_text(encoding="utf-8"))

    def test_difference_pdf_failure_is_reported_before_model_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "out"
            output.mkdir()
            raw = root / "raw.tex"
            raw.write_text("x", encoding="utf-8")
            with patch.object(workflow, "_fixed_build_pdf", side_effect=RuntimeError("latex failed")):
                with self.assertRaises(RuntimeError):
                    workflow._build_difference_pdf(raw, output / "difference.pdf", root)
            self.assertFalse((output / "difference.pdf").exists())

    def test_difference_pdf_build_propagates_keep_build_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw.tex"
            raw.write_text("x", encoding="utf-8")
            output = root / "difference.pdf"
            captured = {}

            def fake_build(*args, **kwargs):
                captured["keep_build_files"] = kwargs["keep_build_files"]
                return output

            with patch.object(workflow, "_fixed_build_pdf", side_effect=fake_build):
                workflow._build_difference_pdf(raw, output, root, keep_build_files=True)
            self.assertTrue(captured["keep_build_files"])

    def test_manifest_requires_complete_source_correspondence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, _source, _prepared, _comments, _pdf = _fixture(root)
            payload = {"schema_version": 1, "popup_ids": list(manifest.ids), "blocks": list(manifest.blocks)}
            del payload["blocks"][0]["source_position"]["column"]
            with self.assertRaises(ValueError):
                workflow.PopupManifest.from_payload(payload)

    def test_moving_caption_relocates_all_popup_macros_without_dropping_diff_markup(self):
        body = (
            "\\begin{document}\n"
            "\\caption[Short {nested ] brace}]{"
            "before \\DIFdelbeginFL \\begin{itemize}old\\DIFdelendFL "
            "and \\DIFaddbeginFL \\item new\\DIFaddendFL "
            "plus \\DIFaddbeginFL visible caption text\\DIFaddendFL"
            " and {nested braces}}\n"
            "after\n\\end{document}\n"
        )
        _head, body_only = split_document(body)
        prepared, blocks, _ = insert_placeholders(body_only, "R", 1)

        self.assertEqual([block.id for block in blocks], ["R001-D", "R002-A", "R003-A"])
        self.assertTrue(all(block.relocated for block in blocks))
        self.assertIn(r"\DIFaddbeginFL visible caption text\DIFaddendFL", prepared)
        caption_end = prepared.index("}\n\\DiffPopup", prepared.index(r"\caption"))
        popup_positions = [prepared.index(f"\\DiffPopup{{{block.id}}}") for block in blocks]
        self.assertTrue(all(position > caption_end for position in popup_positions))
        self.assertEqual(popup_positions, sorted(popup_positions))
        for block in blocks:
            self.assertEqual(prepared.count(block.placeholder), 1)

    def test_moving_optional_caption_argument_relocates_popup_after_full_command(self):
        body = (
            "\\begin{document}\n"
            "\\caption[short \\DIFaddbeginFL changed\\DIFaddendFL]{long title}\n"
            "\\end{document}\n"
        )
        _head, body_only = split_document(body)
        prepared, blocks, _ = insert_placeholders(body_only, "R", 1)
        self.assertEqual([block.id for block in blocks], ["R001-A"])
        self.assertTrue(blocks[0].relocated)
        popup_position = prepared.index(r"\DiffPopup{R001-A}")
        command_end = prepared.index("]{long title}") + len("]{long title}")
        self.assertGreater(popup_position, command_end)
        self.assertIn(r"\DIFaddbeginFL changed\DIFaddendFL", prepared)

    def test_structural_pending_popup_keeps_source_order_before_next_safe_block(self):
        body = (
            "\\begin{document}\n"
            "\\DIFdelbeginFL \\begin{itemize}old\\DIFdelendFL\n"
            "\\DIFaddbeginFL new\\DIFaddendFL\n"
            "\\end{document}\n"
        )
        _head, body_only = split_document(body)
        prepared, blocks, _ = insert_placeholders(body_only, "R", 1)
        positions = [prepared.index(f"\\DiffPopup{{{block.id}}}") for block in blocks]
        self.assertEqual(positions, sorted(positions))

    def test_job_accepts_declared_project_root_as_build_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            project = parent / "project"
            output = project / "out"
            project.mkdir()
            output.mkdir()
            manifest, source, prepared, comments, diff_pdf = _fixture(output)
            job = workflow.PopupReviewJob(
                config=SessionConfig(workspace=WorkspaceConfig(cwd=output), allow_shell_commands=False),
                manifest=manifest,
                source_diff=source,
                prepared_tex=prepared,
                comments_skeleton=comments,
                diff_pdf=diff_pdf,
                output_dir=output,
                project_root=project,
                build_cwd=project,
                no_build=True,
            )
            self.assertEqual(job.build_cwd, project.resolve())

    def test_difference_pdf_build_uses_project_local_class_not_raw_diff_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            raw_dir = project / "out" / "raw_diff"
            output = project / "out" / "difference.pdf"
            raw_dir.mkdir(parents=True)
            (project / "localclass.cls").write_text(
                "\\NeedsTeXFormat{LaTeX2e}\n"
                "\\ProvidesClass{localclass}\n"
                "\\LoadClass{article}\n",
                encoding="utf-8",
            )
            raw = raw_dir / "raw.tex"
            raw.write_text(
                "\\documentclass{localclass}\n"
                "\\begin{document}\nvisible latexdiff markup\n\\end{document}\n",
                encoding="utf-8",
            )
            result = workflow._build_difference_pdf(raw, output, project)
            self.assertEqual(result, output.resolve())
            self.assertTrue(output.is_file())

    def test_fixed_build_uses_requested_pdf_jobname_and_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tex = root / "filled_review.tex"
            tex.write_text("x", encoding="utf-8")
            output = root / "requested.pdf"
            captured = {}

            def fake_run(command, *, cwd, check):
                captured["command"] = command
                captured["cwd"] = cwd
                self.assertTrue(check)
                jobname = next(value.split("=", 1)[1] for value in command if value.startswith("-jobname="))
                outdir = next(value.split("=", 1)[1] for value in command if value.startswith("-outdir="))
                outdir_path = Path(cwd) / outdir
                outdir_path.mkdir(parents=True, exist_ok=True)
                (outdir_path / f"{jobname}.pdf").write_bytes(b"%PDF-1.4\n")

            with patch.object(workflow.subprocess, "run", side_effect=fake_run):
                result = workflow._fixed_build_pdf(tex, output, root)
            self.assertEqual(result, output.resolve())
            self.assertIn("-jobname=requested", captured["command"])
            self.assertIn("-outdir=.", captured["command"])

    def test_fixed_build_rejects_stale_sidecar_without_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tex = root / "source.tex"
            tex.write_text("x", encoding="utf-8")
            stale = root / "requested.log"
            stale.write_text("stale", encoding="utf-8")
            with self.assertRaises(ValueError):
                workflow._fixed_build_pdf(tex, root / "requested.pdf", root)
            self.assertTrue(stale.exists())

    def test_fixed_build_rejects_preexisting_destination_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tex = root / "source.tex"
            tex.write_text("x", encoding="utf-8")
            destination = root / "requested.pdf"
            destination.write_bytes(b"%PDF-stale\n")
            with self.assertRaises(ValueError):
                workflow._fixed_build_pdf(tex, destination, root)
            self.assertEqual(destination.read_bytes(), b"%PDF-stale\n")

    def test_fixed_build_does_not_fallback_to_tex_stem_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tex = root / "source.tex"
            tex.write_text("x", encoding="utf-8")

            def fake_run(command, *, cwd, check):
                del command, check
                (Path(cwd) / "source.pdf").write_bytes(b"%PDF-1.4\n")

            with patch.object(workflow.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(ValueError):
                    workflow._fixed_build_pdf(tex, root / "requested.pdf", root)
            self.assertFalse((root / "requested.pdf").exists())
            self.assertTrue((root / "source.pdf").exists())

    def test_real_latexmk_cleanup_removes_jobname_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tex = root / "filled_review.tex"
            tex.write_text(
                "\\documentclass{article}\n\\begin{document}\nSmoke.\n\\end{document}\n",
                encoding="utf-8",
            )
            output = root / "requested.pdf"
            result = workflow._fixed_build_pdf(tex, output, root)
            self.assertEqual(result, output.resolve())
            self.assertTrue(output.is_file())
            for extension in workflow.LATEX_BUILD_EXTENSIONS:
                self.assertFalse((root / f"requested{extension}").exists(), extension)

    def test_public_package_imports_from_repository_root(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        completed = subprocess.run(
            [sys.executable, "-c", "from scripts.run_popup_review import PopupReviewJob; print(PopupReviewJob.__name__)"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("PopupReviewJob", completed.stdout)

    def test_cli_parser_exposes_fixed_workflow_options(self):
        with patch.object(
            sys,
            "argv",
            [
                "run_popup_review.py",
                "--output-dir",
                "out",
                "--master-tex",
                "main.tex",
                "--to-worktree",
                "--no-build",
                "--input-file",
                "attached.txt",
                "--prompt",
                "focus",
            ],
        ):
            args = workflow.parse_args()
        self.assertTrue(args.to_worktree)
        self.assertTrue(args.no_build)
        self.assertEqual(args.input_file, [Path("attached.txt")])
        self.assertEqual(args.extra_prompt, "focus")

    def test_offline_runner_lifecycle_reviewer_repair_and_normal_pdf(self):
        self._exercise_offline_lifecycle()

    def test_offline_build_failure_fails_job_and_removes_final_artifacts(self):
        self._exercise_offline_lifecycle(build_failure=True)

    def test_offline_conflict_prevents_delivery(self):
        self._exercise_offline_lifecycle(conflict=True)

    def test_offline_consecutive_unresolved_prevents_delivery(self):
        self._exercise_offline_lifecycle(unresolved=True)

    def _exercise_offline_lifecycle(self, *, build_failure=False, conflict=False, unresolved=False):
        # This SDK-boundary adapter alone uses runner internals; no real Codex
        # process is launched. Exercise the real lifecycle/protocol machinery.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            state = {"attempts": 0, "reviews": 0}

            class FakeProcess:
                returncode = 0

                def __init__(self, output_path: Path, repair_index: int, content: str):
                    self.output_path = output_path
                    self.repair_index = repair_index
                    self.content = content

                def communicate(self, prompt):
                    del prompt
                    self.output_path.write_text(
                        self.content,
                        encoding="utf-8",
                    )
                    return "", ""

                def emitted_stdout_lines(self):
                    return False

                def poll(self):
                    return self.returncode

                def terminate(self):
                    return None

            class FakeAnswerFork:
                _turn_session = SimpleNamespace()

                def __enter__(self):
                    state["answer_forks"] = state.get("answer_forks", 0) + 1
                    return self

                def __exit__(self, *args):
                    state["answer_forks_closed"] = state.get("answer_forks_closed", 0) + 1

                def _send_turn(self, prompt, **kwargs):
                    self_test.assertIn("files cannot be changed", prompt)
                    self_test.assertIsNotNone(kwargs.get("output_schema_path"))
                    return CodexResponse(
                        content=json.dumps({"finding_responses": [{
                            "finding_id": fid, "disposition": "addressed",
                            "response": "fixed",
                        } for fid in (("F001", "F002") if conflict else ("F001",))]}),
                        returncode=0, stdout="", stderr="", argv=("fake-codex",),
                    )

            self_test = self

            class FakeRunner:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs

                def _attempt(self, owner, *, prompt, attempt_index, repair_index, content=None):
                    state["attempts"] += 1
                    output = root / f"main-response-{repair_index}.txt"
                    return _CodexAttempt(
                        argv=("fake-codex",),
                        prompt=prompt,
                        process=FakeProcess(
                            output,
                            repair_index,
                            content or f"fake main response {repair_index}",
                        ),
                        output_path=output,
                        attempt_index=attempt_index,
                        repair_index=repair_index,
                    )

                def start(self, **kwargs):
                    owner = kwargs["_job_owner"]
                    subcwd_path = root / "fake-subcwd"
                    subcwd_path.mkdir()
                    for relative_path, content in dict(kwargs.get("subcwd_files", {})).items():
                        destination = subcwd_path / relative_path
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(content["bytes"])

                    def resolve_subcwd_path(path):
                        text = str(path)
                        if text.startswith("${SUBCWD}/"):
                            return subcwd_path / text.removeprefix("${SUBCWD}/")
                        return path

                    review = kwargs.get("review")
                    if review is not None:
                        review = replace(
                            review,
                            artifact_paths=tuple(
                                resolve_subcwd_path(path) for path in review.artifact_paths
                            ),
                        )
                    initial = self._attempt(
                        owner,
                        prompt="fake initial prompt",
                        attempt_index=0,
                        repair_index=0,
                    )

                    def spawn_attempt(*, prompt, attempt_index, repair_index, **spawn_kwargs):
                        self_test.assertNotIn("turn_output_schema_path", spawn_kwargs)
                        return self._attempt(
                            owner, prompt=prompt, attempt_index=attempt_index,
                            repair_index=repair_index,
                        )

                    def review_runner(**review_kwargs):
                        state["reviews"] += 1
                        passed = state["reviews"] >= 2
                        response = CodexResponse(
                            content=json.dumps({"passed": passed}),
                            returncode=0,
                            stdout="",
                            stderr="",
                            argv=("fake-reviewer",),
                        )
                        decision = (
                            {
                                "decisions": [
                                    {
                                        "finding_id": "F001",
                                        "resolution": "resolved",
                                        "comment": "ok",
                                        "conflicts_with": None,
                                    }
                                ],
                                "new_findings": [],
                            }
                            if passed
                            else {
                                "decisions": [],
                                "new_findings": [{"comment": "repair required",
                                                  "relation": {"kind": "new", "finding_id": None}}],
                            }
                        )
                        if conflict:
                            if state["reviews"] == 1:
                                decision["new_findings"].append({
                                    "comment": "incompatible repair",
                                    "relation": {"kind": "new", "finding_id": None},
                                })
                            else:
                                decision["decisions"].append({
                                    "finding_id": "F002", "resolution": "conflicts",
                                    "comment": "mutually exclusive requirements",
                                    "conflicts_with": "F001",
                                })
                            passed = False
                        if unresolved and state["reviews"] > 1:
                            decision["decisions"][0]["resolution"] = "unresolved"
                            passed = False
                        return _ReviewResult(
                            passed=passed,
                            findings=() if passed else ("repair required",),
                            summary="accepted" if passed else "needs repair",
                            response=response,
                            stdout="",
                            stderr="",
                            argv=["fake-reviewer"],
                            stream_emitted=True,
                            decision=decision,
                        )

                    owner._initialize_runtime(
                        attempt=initial,
                        spawn_attempt=spawn_attempt,
                        original_input=kwargs["input"],
                        context_paths=kwargs.get("read_paths", ()),
                        context_sections=(),
                        output_schema_section=None,
                        output_model_source_section=None,
                        read_paths=kwargs.get("read_paths", ()),
                        write_paths=kwargs.get("write_paths", ()),
                        subcwd_files=kwargs.get("subcwd_files", ()),
                        default_timeout=kwargs.get("timeout"),
                        cwd=root,
                        subcwd_path=subcwd_path,
                        ignore_global_context=kwargs.get("ignore_global_context", False),
                        review_config=review,
                        review_runner=review_runner,
                        open_answer_fork=lambda prompt: FakeAnswerFork(),
                        output_validators=kwargs.get("_output_validators", ()),
                        repair_validation_failures=kwargs.get("repair_validation_failures", False),
                        max_review_repairs=kwargs.get("max_review_repairs"),
                        log_dir=root,
                        log_path=root / "fake-job.log",
                        hook_owner=kwargs.get("_hook_owner"),
                        hooks=kwargs.get("_hooks"),
                        hook_no_args=True,
                        job_id=kwargs.get("_job_id"),
                    )
                    return owner

                def close(self):
                    return None

            def fake_configure(_owner, **kwargs):
                return FakeRunner(**kwargs)

            config = SessionConfig(
                workspace=WorkspaceConfig(cwd=root),
                allow_shell_commands=False,
                timeout=30,
            )
            with patch.object(job_module, "_configure_runtime", fake_configure):
                job = workflow.PopupReviewJob(
                    config=config,
                    manifest=manifest,
                    source_diff=source,
                    prepared_tex=prepared,
                    comments_skeleton=comments,
                    diff_pdf=diff_pdf,
                    output_dir=root,
                    build_cwd=root,
                    pdf_output=root / "nested" / "final.pdf",
                    no_build=False,
                )
                build_context = (
                    patch.object(workflow, "_fixed_build_pdf", side_effect=ValueError("synthetic build failure"))
                    if build_failure else nullcontext()
                )
                with build_context:
                    if conflict or unresolved or build_failure:
                        error_type = (CodexReviewConflictError if conflict else
                                      CodexValidationError if unresolved else CodexExecutionError)
                        with self.assertRaises(error_type):
                            job.run()
                        self.assertEqual(job.status, "failed")
                        self.assertFalse(job.final_comments.exists())
                        self.assertFalse(job.filled_tex.exists())
                        self.assertFalse(job.pdf_output.exists())
                        return
                    completion = job.run()

            self.assertEqual(job.status, "completed")
            self.assertEqual(state["attempts"], 2)
            self.assertEqual(state["answer_forks"], 1)
            self.assertEqual(state["answer_forks_closed"], 1)
            package = json.loads((root / job.id / "review/round-001/review-package.json").read_text())
            self.assertEqual(package["schema_version"], 3)
            self.assertEqual(package["finding_responses"][0]["finding_id"], "F001")
            self.assertEqual(state["reviews"], 2)
            self.assertEqual(completion.mode, "normal")
            self.assertTrue(completion.pdf_path)
            self.assertTrue(Path(completion.pdf_path).is_file())
            self.assertEqual(Path(completion.pdf_path), (root / "nested" / "final.pdf").resolve())
            self.assertEqual(completion.comment_count, len(manifest.ids))

    @unittest.skipUnless(os.environ.get("POPUP_REVIEW_RUN_REAL_CODEX") == "1",
                         "real Codex smoke test is opt-in")
    def test_real_codex_smoke(self):
        """One paid SDK run over synthetic data, including reviewer repair."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            source.write_text("-old\n+new\n", encoding="utf-8")
            diff_pdf.unlink()
            workflow._fixed_build_pdf(prepared, diff_pdf, root)

            class SmokeJob(workflow.PopupReviewJob):
                attempts = 0

                def after_main_attempt(self):
                    self.attempts += 1
                    if self.attempts == 1:
                        # Valid local schema but violates the explicit review
                        # requirement: force one independent Reviewer rejection.
                        comments.write_text(json.dumps({
                            fid: "[意味維持][OK] synthetic comment missing required marker"
                            for fid in manifest.ids
                        }), encoding="utf-8")

            job = SmokeJob(
                config=SessionConfig(
                    model=os.environ.get("POPUP_REVIEW_REAL_MODEL", workflow.DEFAULT_REVIEWER_MODEL),
                    timeout=1800, workspace=WorkspaceConfig(cwd=root),
                ),
                manifest=manifest, source_diff=source, prepared_tex=prepared,
                comments_skeleton=comments, diff_pdf=diff_pdf, output_dir=root,
                extra_prompt=(
                    "This is a synthetic smoke test. Every popup comment must include "
                    "the exact literal smoke-check. Reviewer must reject any comment "
                    "missing smoke-check, even when labels are otherwise valid. "
                    "Do not create any additional files."
                ),
                reviewer_model=os.environ.get("POPUP_REVIEW_REAL_MODEL", workflow.DEFAULT_REVIEWER_MODEL),
                reviewer_timeout=300,
            )
            try:
                completion = job.run()
            except Exception as exc:
                print(f"real smoke error: {exc}; log_path={getattr(exc, 'log_path', None)}")
                raise
            self.assertEqual(job.status, "completed")
            self.assertTrue(job.review_passed)
            self.assertGreaterEqual(job.attempts, 2)
            self.assertEqual(completion.mode, "normal")
            self.assertTrue(Path(completion.pdf_path).is_file())
            self.assertTrue(completion.log_path)
            for comment in json.loads(Path(completion.comments_path).read_text()).values():
                self.assertIn("smoke-check", comment)
            print(f"real smoke succeeded; attempts={job.attempts}; log_path={completion.log_path}")

    def test_symlink_pdf_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "real.pdf"
            target.write_bytes(b"%PDF")
            link = root / "link.pdf"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                workflow._ensure_pdf(link)

    def test_job_rejects_input_and_output_symlinks_before_resolving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            source_link = root / "source-link.diff"
            source_link.symlink_to(source)
            with self.assertRaises(ValueError):
                workflow.PopupReviewJob(
                    config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                    manifest=manifest,
                    source_diff=source_link,
                    prepared_tex=prepared,
                    comments_skeleton=comments,
                    diff_pdf=diff_pdf,
                    output_dir=root,
                    no_build=True,
                )

    def test_job_rejects_symlink_ancestors_and_external_build_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, source, prepared, comments, diff_pdf = _fixture(root)
            target = root / "target"
            target.mkdir()
            ancestor = root / "ancestor"
            ancestor.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                workflow._reject_symlink(ancestor / "nested" / "output", "output")
            with self.assertRaises(ValueError):
                workflow._reject_symlink(ancestor / ".." / "safe", "output")
            with self.assertRaises(ValueError):
                workflow.PopupReviewJob(
                    config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                    manifest=manifest,
                    source_diff=source,
                    prepared_tex=prepared,
                    comments_skeleton=comments,
                    diff_pdf=diff_pdf,
                    output_dir=root,
                    build_cwd=root.parent,
                    no_build=True,
                )
            with self.assertRaises(ValueError):
                workflow.PopupReviewJob(
                    config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                    manifest=manifest,
                    source_diff=source,
                    prepared_tex=prepared,
                    comments_skeleton=comments,
                    diff_pdf=diff_pdf,
                    output_dir=root,
                    pdf_output=root.parent / "external-review.pdf",
                    no_build=True,
                )
            output_target = root / "output-target"
            output_target.mkdir()
            output_link = root / "output-link"
            output_link.symlink_to(output_target, target_is_directory=True)
            with self.assertRaises(ValueError):
                workflow.PopupReviewJob(
                    config=SessionConfig(workspace=WorkspaceConfig(cwd=root), allow_shell_commands=False),
                    manifest=manifest,
                    source_diff=source,
                    prepared_tex=prepared,
                    comments_skeleton=comments,
                    diff_pdf=diff_pdf,
                    output_dir=output_link,
                    no_build=True,
                )


if __name__ == "__main__":
    unittest.main()
