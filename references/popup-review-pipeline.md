# Popup Review Pipeline

## Purpose

This reference records the current batch pipeline for popup-comment review copies derived from latexdiff output.

The pipeline starts after a raw latexdiff artifact already exists. Generation of that raw artifact remains a separate workflow so ordinary latexdiff review can still be used without popup comments.

## Current Pipeline

### Stage 1: Prepare review TeX

Input:
- raw latexdiff `.tex`

Output:
- copied review `.tex`

Responsibilities:
- copy the raw diff file
- inject the popup macro block into the preamble
- append an empty popup macro after each diff chunk
- assign stable IDs such as `R014-D` and `R014-A`
- print a summary of inserted IDs and write a JSON skeleton that can be filled later

Illustrative shape:

```tex
\DIFdelbegin \DIFdel{in~\cite{...}}\DIFdelend
\DiffPopup{R014-D}{__COMMENT_R014_D__}
\DIFaddbegin \DIFadd{of \citet{...} the }\DIFaddend
\DiffPopup{R014-A}{__COMMENT_R014_A__}
```

### Stage 2: Generate comments

Input to Codex:
- the prepared review `.tex` as a whole

Output from Codex:
- a separate JSON file keyed by ID

Constraint:
- Codex writes only this comment file.
- Codex does not directly rewrite the raw diff `.tex`.
- Codex does not directly rewrite the prepared review `.tex`.

Illustrative shape:

```json
{
  "R014-D": "旧表現は引用の掛かり先が不自然で、構文上の収まりが悪い。",
  "R014-A": "新表現では著者名を前に出し、文の主部に自然に統合している。"
}
```

Current semantic policy:
- delete comment: why the old wording is not adopted
- add comment: why the new wording is justified
- language: follow the user or project preference

### Stage 3: Fill comments

Input:
- prepared review `.tex`
- comment file

Output:
- filled review `.tex`

Mechanism:
- string replacement keyed by the stable ID placeholder
- the initial implementation uses a JSON object mapping `ID -> comment`
- before replacement, each comment is encoded as UTF-16BE hex for the PDF annotation string
- the write back into the review `.tex` is performed by the fill script, not by Codex directly

## Diff Targets

Treat all of the following as review targets:

- `\DIFdelbegin ... \DIFdelend`
- `\DIFaddbegin ... \DIFaddend`
- `\DIFdelbeginFL ... \DIFdelendFL`
- `\DIFaddbeginFL ... \DIFaddendFL`

Pairing between delete and add may be used for interpretation, but comments are still attached separately.

## Stable Choices

- target viewer: Adobe Acrobat / Acrobat Reader
- popup default: `Open=false`
- primary key: sequential IDs, not line numbers
- popup contents: store comment text as UTF-16BE hex in `/Contents`
- Codex write scope: comment file only

## Open Questions

- How to treat whitespace-only or punctuation-only diffs
- Whether empty comments should be allowed in any non-interactive batch mode
- Whether to keep a deferred interactive mode after the batch mode is stable
