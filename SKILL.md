---
name: latexdiff-popup-review
description: Use when the user wants to turn a latexdiff-generated LaTeX diff into a popup-comment review copy, with a substantive review pass derived from `paper-review` style findings and separate popup comments for DIF delete/add blocks, placeholder injection, comment-file filling, and Acrobat-oriented annotations.
---

# Latexdiff Popup Review

## Overview

This skill handles the post-`latexdiff` review stage. It assumes a raw latexdiff `.tex` already exists and produces a separate review `.tex` / `.pdf` with popup comments attached to each diff chunk.

Keep `latexdiff` generation itself separate. Always use `latexdiff` first to generate the raw diff artifact before using this skill.

The current design is batch-oriented:

1. A preparation script copies the raw diff `.tex`, injects popup macros, and inserts empty comment placeholders after each diff chunk.
2. Codex performs a substantive review pass in the style of `paper-review` `review-only`, using a source diff such as `git diff` and splitting findings by semantic change rather than by popup target.
3. Codex reads the prepared review `.tex` as a whole and renders those findings into a separate comment file keyed by stable IDs.
4. A fill script writes the comments back into the placeholder macros by string replacement.

Do not edit the raw latexdiff artifact in place.
Codex must not directly rewrite the prepared review `.tex` either. In this workflow, the only file Codex may write directly is the separate comment file.

## When To Use

- The user already has a `latexdiff` `.tex` and wants popup comments added.
- The review should target Adobe Acrobat / Reader rather than visible margin comments.
- Each `delete` and `add` should receive separate comments.
- The workflow should preserve a raw diff artifact and create a separate derived review copy.
- The review stage should work the same way whether the original edit was made by Codex or by someone else.

Do not use this skill when the user only wants a plain latexdiff PDF with no popup review layer.
Do not start from a hand-picked or ad hoc diff source when the standard `latexdiff` skill should be used first to generate the raw diff artifact.

## Core Rules

- Treat `\DIFdel...` and `\DIFadd...` as separate comment targets.
- Treat `FL` variants (`\DIFdelbeginFL...\DIFdelendFL`, `\DIFaddbeginFL...\DIFaddendFL`) as comment targets too.
- Use stable sequential IDs rather than line numbers as the primary key.
- Delete comments should describe the role of the old wording and evaluate whether deleting it is appropriate.
- Add comments should describe the effect of the new wording and evaluate whether adding it is appropriate.
- Each comment should include a meaning-change label near the start so readers can quickly see whether the diff preserves meaning. Use labels such as `[意味維持]`, `[意味変化あり]`, and `[意味変化要確認]`.
- Prefer explicit review labels such as `[OK]`, `[要確認]`, and `[懸念]` at the start of each comment when that helps separate endorsement from mere description.
- Do not treat every add/delete pair as automatically justified. Review comments must remain willing to question, defer, or criticize a change instead of rationalizing it.
- Use the same substantive review standards as `paper-review`.
- Perform the substantive review pass in the style of `paper-review` `review-only` before rendering popup comments.
- Use a source diff such as `git diff` as the default review surface for that substantive pass; use the latexdiff artifact for popup target mapping and placement.
- Keep review findings at the level of semantic change units rather than popup targets. Do not make `paper-review` reason in terms of add/delete blocks.
- One semantic finding may map to multiple popup IDs, and one diff hunk may yield multiple findings when it contains separable changes.
- When the substantive review pass produces no finding for a reviewed change, generate the popup comment locally from the diff target and its nearby context. In that fallback path, default to a concise `[OK]` explanation unless the popup-stage reading itself reveals a concern.
- Load only the relevant parts of `../paper-review/references/criteria.md` when judging the diff. `latexdiff-popup-review` changes the review surface and output format, not the underlying review criteria.
- Comment language should follow the user or project preference.
- Always obtain the raw diff artifact through the `latexdiff` skill first.
- Codex should read the prepared review `.tex` as a whole.
- Codex must write only the separate comment file. It must not directly modify the raw diff `.tex` or the prepared review `.tex`.
- Treat this workflow as a fresh review pass even when the original edit was also produced by Codex.
- Do not leave a reviewed diff target without a comment. Even when a diff looks inappropriate, put some comment text in the comment file for that target.
- Treat the popup-reviewed PDF itself as the default review record. Do not create an extra Markdown note by default.
- The intermediate `paper-review` style findings may remain transient. Do not create a separate review Markdown note unless the user explicitly asks for one or a separate summary is otherwise necessary.
- Only create a separate Markdown or other follow-up document when the user explicitly asks for it, or when inappropriate diffs need a separate summary after the per-target comments are complete.
- The fill stage should use plain string replacement keyed by ID.
- The fill stage should encode popup text as UTF-16BE hex before embedding it into the PDF annotation `/Contents` field.

## Workflow

1. Run `latexdiff` first and confirm the raw diff `.tex` path that it produced.
2. Choose a separate output path for the review copy.
3. Run `scripts/prepare_popup_review.py` to copy the raw diff, inject the popup macro block, and insert placeholder popup macros after every diff chunk.
4. Choose the review criteria for this diff by loading only the relevant parts of `../paper-review/references/criteria.md`.
5. Obtain the corresponding source diff for the same change set, using `git diff` by default unless another review surface is clearly better for the task.
6. Run a substantive review pass in the style of `paper-review` `review-only`, splitting the diff into semantic findings rather than popup targets.
7. Read the prepared review `.tex` as a whole and map those findings onto the relevant popup IDs. If a reviewed change has no substantive finding, synthesize a short fallback popup comment from the local old/new wording and nearby context.
8. Write comments only to a separate JSON file keyed by diff ID, ensure every reviewed target receives some comment text, and include a meaning-change label for each diff.
9. Treat the filled popup-review PDF as the default deliverable. Do not add a separate Markdown note unless the user explicitly asks for one or some inappropriate diffs need a separate summary.
10. Run `scripts/fill_popup_comments.py` to fill the placeholders by string replacement.
11. Compile the derived review `.tex` only after the fill step.

## References

- For the current pipeline and expected data flow, read [references/popup-review-pipeline.md](references/popup-review-pipeline.md).
- For substantive review criteria, load only the relevant parts of [../paper-review/references/criteria.md](../paper-review/references/criteria.md).
- Read the script docstrings before changing their I/O contract.
