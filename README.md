# Latexdiff Popup Review

Git 管理された LaTeX 原稿の二つの状態を比較し、変更箇所に popup コメントを付けたレビュー PDF を作る Codex skill です。原稿そのものは改稿しません。Main が差分を意味単位で評価し、独立した Reviewer とローカル検証を通過したコメントだけを PDF に反映します。popup の表示は Adobe Acrobat / Reader を想定しています。

## 必要なもの

- Python 3.10 以上、Git、`latexdiff-vc`、`latexmk`、`pdflatex`、原稿で使う LaTeX package・font。
- 認証済み Codex CLI と、同じ Python 環境の `openai-codex` SDK（runner 側の要件に従って導入）。モデル利用には料金が発生する場合があります。
- [Codex Job Runner](https://github.com/NakamuraTakumu/Codex-Job-Runner)：公開 `CodexJob` / `ReviewConfig` API と peer-review protocol v3 が必要です。互換性確認済み commit は `b8e71ce`（2026-10-12）。このリポジトリだけでは Job 経路を実行できません。

## インストール

二つの skill を同じ親ディレクトリへ配置します。

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/NakamuraTakumu/Codex-Job-Runner.git ~/.codex/skills/codex-job-runner
git clone https://github.com/NakamuraTakumu/Skill-Latexdiff-Popup-Review.git ~/.codex/skills/latexdiff-popup-review
python3 -c 'import sys; from pathlib import Path; sys.path.insert(0, str(Path.home()/".codex/skills/codex-job-runner")); import codex_job_runner'
codex --version
latexdiff-vc --version
latexmk -v
```

個人の `~/.codex/instructions/writing.md` は不要です。評価原則と文章規則は `references/`、モデル指示は `prompts/` に同梱しています。別の配置では `codex_job_runner` を Python の import path に置いてください。

## 最初の実行

対象 LaTeX project の Git repository root で実行します。`OLD` と `NEW` は実在する commit / tag に置き換え、`OUT` は存在しないか空のディレクトリを指定してください。

```bash
python3 ~/.codex/skills/latexdiff-popup-review/scripts/run_popup_review.py \
  --project-root "$PWD" --master-tex main.tex \
  --from-commit OLD --to-commit NEW \
  --output-dir "$PWD/popup-review-output"
```

現在の staged / tracked working-tree 変更は、`--to-commit NEW` の代わりに `--to-index` / `--to-worktree` で比較できます（baseline 省略時は `HEAD`）。snapshot は branch、HEAD、index、working tree を変更しませんが、Git object database に一時 commit を追加します。未追跡かつ unstaged のファイルは含みません。

任意の `--input-file path/to/context.md`（複数可）と `--prompt "追加のレビュー観点"` で判断材料を渡せます。入力上限は1ファイル64 MiB・合計256 MiB、prompt は256 KiBです。`--codex-bin`、`--model`、`--reviewer-model` などは `--help` を参照してください。Main の model 省略時は `gpt-6.1-sol`（reasoning effort `medium`）、Reviewer の model 省略時は Main と同じ model を使います。認証方法によって利用できる model が異なるため、必要なら両 option を明示してください。実テストでも `POPUP_REVIEW_REAL_MODEL` で指定できます。

成功時は JSON で `comments_path`、`filled_tex_path`、`pdf_path`、`comment_count`、`mode`、`log_path` を返します。通常成果は `OUT/review.pdf`、`OUT/popup_comments.json`、`OUT/filled_review.tex` です。`difference.pdf` は注釈前のレビュー入力です。`--no-build` は最終 PDF だけを省略する diagnostic mode であり、差分 PDF の build は必要です。失敗時の再実行には新しい空の output directory を使います。

## 安全性と制約

Main の永続 write path は `OUT/handoff/` のみ、Reviewer と Main 回答 fork の永続 write path は空です。ファイルを読むため shell は有効、Web 検索は無効です。Main 回答 fork は元の履歴を変更せず finding への回答だけを返します。矛盾した修正要求は `CodexReviewConflictError` で終了し、未承認成果物を配布しません。

Codex には差分、原稿由来の artifact、追加資料が渡ります。秘密情報を含む入力を渡さないでください。ログ・rollout に内容が保存されるため、生成物は公開前に確認してください。host の Git / LaTeX process は Job sandbox の外で動くため、信頼できない原稿には使わないでください。PDF viewer による最終表示の目視確認は利用者が行います。

## 開発・参照

```bash
cd ~/.codex/skills/latexdiff-popup-review
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
# 明示 opt-in のみ実 Codex を起動（最大1800秒、料金発生の可能性）
POPUP_REVIEW_RUN_REAL_CODEX=1 PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_popup_review_job.PopupReviewJobTests.test_real_codex_smoke
```

通常 suite は offline です（LaTeX がある場合はローカル build も検証）。SDK 境界 fake を使う lifecycle test は実 Codex ではありません。

- `SKILL.md`：skill の入口、公開 CLI / Python API。
- `references/popup-review-pipeline.md`：成果物と Comments JSON 契約。
- `references/revision-principles.md` / `references/writing.md`：評価原則とコメント文章規則。
- `document/job-based-latexdiff-popup-review-spec.md`：実装仕様。
- `scripts/run_popup_review.py`：非対話 Job 経路。
- `scripts/popup_review_wizard.py`：手動 handoff の legacy wizard（Job / Reviewer を起動しません）。
