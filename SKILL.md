---
name: latexdiff-popup-review
description: Git 管理された LaTeX project から補助スクリプトで生 `latexdiff` diff を生成し、`paper-review` 基準の所見を Adobe Acrobat / Reader 向け popup 注釈付きレビュー PDF に変換するときに使う。DIF add/delete 対象へのコメント JSON 作成と PDF 注釈生成を扱う。
---

# Latexdiff Popup Review

## 目的

Git 管理された LaTeX project から生 `latexdiff` TeX を生成し、`paper-review` を呼び出して Markdown note を作成し、その所見を popup 注釈付きレビュー PDF に変換する。ワークフローの主導は `scripts/popup_review_wizard.py` に任せ、Codex は `paper-review` の note 所見から script が指示する ID 付きコメント JSON を完成させる。

## 参照

- 手順、責務、データの流れ、具体例は [references/popup-review-pipeline.md](references/popup-review-pipeline.md) を読む。
- 実体的レビューは `paper-review` を呼び出して行う。`paper-review` の Markdown note 出力を抑制しない。
- script の入出力契約を変える前に、対象 script の docstring を読む。

## 中核規則

- **script 主導**: 出力 directory、master TeX、比較元 revision、比較先指定がある場合は、まず `scripts/popup_review_wizard.py` を起動し、その後は script の標準出力の指示に従う。
- **入力面の固定**: Git 管理された LaTeX project から補助スクリプトで生 diff を生成し、既存 diff を入口にしない。
- **作業中差分**: staged または working tree の差分を扱う場合も、wizard の snapshot mode で一時 Git commit を自動生成して既存 pipeline に流す。
- **レビュー粒度の分離**: 実体的レビューは意味単位で行い、popup コメントは diff 対象単位に割り当てる。
- **構造的 diff の保持**: `\item`、`\begin{...}`、`\end{...}`、`\bibitem` などの構造的 control diff もコメント対象にし、popup macro だけを安全な近傍へ移す。
- **paper-review 呼び出し**: 実体的レビューでは `paper-review` を `review-only` mode として使い、`paper-review` の通常成果物として Markdown note を必ず作成または更新する。
- **comments JSON 変換**: `popup_comments.json` は Markdown note の所見から作る。popup ID 対応、comment label、annotation 生成は popup review 側の責務とする。
- **成果物境界の維持**: 生 diff、review TeX、Markdown note、コメント JSON、popup PDF の責務を混ぜない。

## コメント契約

- レビュー対象の diff に空コメントを残さない。ラベル、補完コメント、例外判断は [references/popup-review-pipeline.md](references/popup-review-pipeline.md) の **Comments JSON 契約** に従う。

## 実行契約

- 詳細な手順は [references/popup-review-pipeline.md](references/popup-review-pipeline.md) に従う。
- 出力 directory、master TeX、比較元 revision、比較先 revision が与えられたら、次の形で script を起動する。

```bash
python3 /path/to/latexdiff-popup-review/scripts/popup_review_wizard.py --output-dir OUT --master-tex MAIN.tex --from-commit OLD --to-commit NEW
```

- staged 差分を `HEAD` からレビューする場合は `--to-index` を使う。working tree の tracked 変更まで含める場合は `--to-worktree` を使う。どちらも `--from-commit` 省略時は `HEAD` と比較する。

```bash
python3 /path/to/latexdiff-popup-review/scripts/popup_review_wizard.py --output-dir OUT --master-tex MAIN.tex --to-index
python3 /path/to/latexdiff-popup-review/scripts/popup_review_wizard.py --output-dir OUT --master-tex MAIN.tex --to-worktree
```

- `--to-worktree` は unstaged の tracked 変更を含める。untracked file は、事前に staged されている場合だけ snapshot に含まれる。
- project 固有の text macro が `\DIFdel{\macro{...}}` のように構造扱いされて build error の原因になる場合は、`--latexdiff-option=--append-textcmd=macroA,macroB` を付ける。例: `--latexdiff-option=--append-textcmd=new,delete`。
- script が `popup_comments.json` の completion を待機したら、先に `paper-review` を呼び出して Markdown note を作成または更新し、その所見から `popup_comments.json` を編集して同じ process に戻る。script 生成物のうち直接編集してよいのは `popup_comments.json` だけとする。
- process が終了した場合は、同じ command に `--resume-comments` を付けて再実行し、既存 `popup_comments.json` を検証・保持して続行する。
- baseline revision、target 指定、master TeX、出力先が不明な場合は質問し、推測で確定しない。

## 補助スクリプト

- **通常入口**: `scripts/popup_review_wizard.py`。
- **保守用 helper**: `scripts/prepare_popup_review.py`、`scripts/fill_popup_comments.py`。通常 workflow では直接入口にしない。
- 入出力契約を変える場合は、該当 script の docstring と [references/popup-review-pipeline.md](references/popup-review-pipeline.md) の **処理フロー** を合わせて更新する。

## 出力契約

- **既定の成果物**: Markdown note と popup 注釈付き PDF。
- **Markdown note**: `paper-review` の通常成果物として保存する。mode は `review-only` とし、[../paper-review/references/output-note.md](../paper-review/references/output-note.md) の `## Content` schema に従う。
- **`--no-build` 使用時**: filled review TeX と `comments JSON` を成果物として報告し、PDF path は成果物として扱わない。
- **検証報告**: Markdown note path、build command の結果、生成 PDF path、コメントの充足状況、popup macro を移動した構造的 diff、主要な注意事項を含める。
