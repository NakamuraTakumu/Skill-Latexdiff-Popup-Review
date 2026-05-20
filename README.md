# Latexdiff Popup Review

Git 管理された LaTeX project の `latexdiff` 結果を、popup 注釈付きレビュー PDF へ変換する Codex skill。

## 内容

- `SKILL.md`: popup review workflow の入口、コメント契約、実行契約。
- `references/popup-review-pipeline.md`: 処理フロー、成果物、comments JSON 契約の正本。
- `scripts/popup_review_wizard.py`: 通常入口になる wizard。
- `scripts/prepare_popup_review.py`: 生 diff と review TeX の準備 helper。
- `scripts/fill_popup_comments.py`: comments JSON の検証と TeX 反映 helper。

## 依存関係

- `../paper-review`: 実体的な論文レビューと Markdown note 作成に使う。
- LaTeX project 側には `git`、`latexdiff`、LaTeX build 環境が必要になる。
- skill 単体で clone した場合は、同じ `skills/` 配下に `paper-review` repo も配置する。

## 運用

- この directory は独立 Git repo として管理する。
- remote は環境ごとにこの repo へ個別に設定する。
- workflow は script 主導とし、通常は `scripts/popup_review_wizard.py` を入口にする。
- script の入出力や comments JSON 契約を変える場合は、`references/popup-review-pipeline.md` と `SKILL.md` を合わせて更新する。
