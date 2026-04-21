---
name: latexdiff-popup-review
description: Use when the user wants to turn a latexdiff-generated LaTeX diff into a popup-comment review copy, with separate comment targets for DIF delete/add blocks, placeholder injection, comment-file filling, and Acrobat-oriented popup annotations.
---

# Latexdiff Popup Review

## Overview

This skill handles the post-`latexdiff` review stage. It assumes a raw latexdiff `.tex` already exists and produces a separate review `.tex` / `.pdf` with popup comments attached to each diff chunk.

Keep `latexdiff` generation itself separate. Use `latexdiff-review` first when the user still needs the raw diff artifact.

The current design is batch-oriented:

1. A preparation script copies the raw diff `.tex`, injects popup macros, and inserts empty comment placeholders after each diff chunk.
2. Codex reads the prepared review `.tex` as a whole and writes a separate comment file keyed by stable IDs.
3. A fill script writes the comments back into the placeholder macros by string replacement.

Do not edit the raw latexdiff artifact in place.
Codex must not directly rewrite the prepared review `.tex` either. In this workflow, the only file Codex may write directly is the separate comment file.

## When To Use

- The user already has a `latexdiff` `.tex` and wants popup comments added.
- The review should target Adobe Acrobat / Reader rather than visible margin comments.
- Each `delete` and `add` should receive separate comments.
- The workflow should preserve a raw diff artifact and create a separate derived review copy.

Do not use this skill when the user only wants a plain latexdiff PDF with no popup review layer.

## Core Rules

- Treat `\DIFdel...` and `\DIFadd...` as separate comment targets.
- Treat `FL` variants (`\DIFdelbeginFL...\DIFdelendFL`, `\DIFaddbeginFL...\DIFaddendFL`) as comment targets too.
- Use stable sequential IDs rather than line numbers as the primary key.
- Delete comments should explain why the old wording is not adopted.
- Add comments should explain why the new wording is justified.
- Comment language should follow the user or project preference.
- Codex should read the prepared review `.tex` as a whole.
- Codex must write only the separate comment file. It must not directly modify the raw diff `.tex` or the prepared review `.tex`.
- The fill stage should use plain string replacement keyed by ID.
- The fill stage should encode popup text as UTF-16BE hex before embedding it into the PDF annotation `/Contents` field.

## Workflow

1. Confirm the raw diff `.tex` path and choose a separate output path for the review copy.
2. Run `scripts/prepare_popup_review.py` to copy the raw diff, inject the popup macro block, and insert placeholder popup macros after every diff chunk.
3. Read the prepared review `.tex` as a whole when generating comments.
4. Write comments only to a separate JSON file keyed by diff ID.
5. Run `scripts/fill_popup_comments.py` to fill the placeholders by string replacement.
6. Compile the derived review `.tex` only after the fill step.

## References

- For the current pipeline and expected data flow, read [references/popup-review-pipeline.md](references/popup-review-pipeline.md).
- Read the script docstrings before changing their I/O contract.
