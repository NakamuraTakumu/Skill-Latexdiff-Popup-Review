# Popup Review Pipeline

## 目的

Git 管理された LaTeX project から `latexdiff` を生成し、`paper-review` 基準の所見を Adobe Acrobat / Reader 向け popup 注釈付き PDF に変換する。

## 責務

- **wizard**: diff 生成、popup ID 付与、`comments JSON` skeleton 作成、コメント埋め込み、PDF build を行う。
- **Codex**: wizard が待機した後、`comments JSON` だけを編集する。
- **禁止**: Codex は raw diff TeX、prepared review TeX、filled review TeX を直接編集しない。
- **実行形態**: wizard は対話実行専用。再開情報は `comments JSON` だけに集約する。
- **復旧**: process が終了した場合は、同じ command に `--resume-comments` を付けて再実行する。

## 入力

- **baseline revision**: 比較元 Git revision。
- **target revision**: 比較先 Git revision。
- **master TeX**: `latexdiff-vc` に渡す main TeX file。
- **output directory**: 生成物の保存先。
- **project root**: Git root。通常は `master TeX` から wizard が検出する。

不明な入力は質問し、推測で確定しない。既存 raw diff TeX は入口にしない。

## 実行

```bash
python3 /path/to/latexdiff-popup-review/scripts/popup_review_wizard.py --output-dir OUT --master-tex MAIN.tex --from-commit OLD --to-commit NEW
```

主な options:

- `--output-dir`: raw diff TeX、source diff、prepared review TeX、`comments JSON`、filled review TeX、PDF の出力先。
- `--master-tex`: main TeX file。`--project-root` なしの相対 path は実行 cwd 基準、`--project-root` ありの相対 path は project root 基準。
- `--from-commit`, `--to-commit`: 比較元・比較先 Git revision。
- `--build-cwd`: PDF build command の working directory。省略時は `master TeX` の directory。
- `--resume-comments`: 既存 `comments JSON` の key set を検証し、値を保持する。
- `--manual-comments`: Codex handoff ではなく、diff ID ごとの対話入力で `comments JSON` を作る。
- `--no-build`: filled TeX 作成後に PDF build しない。
- `--keep-build-files`: PDF build 成功後も LaTeX 一時補助ファイルを残す。

## 処理フロー

1. wizard を起動する。
2. wizard が raw diff TeX、source diff、prepared review TeX、`comments JSON` skeleton を生成して待機する。
3. Codex は stdout に出た path から `source diff` と prepared review TeX を確認し、`comments JSON` だけを完成させる。
4. Codex は待機中の wizard process に Enter を送る。
5. wizard が `comments JSON` を検証し、filled review TeX と review PDF を生成する。
6. PDF build 成功後、`--keep-build-files` がない限り LaTeX 一時補助ファイルを削除する。build failure 時は原因調査のため残す。

## レビュー方針

- 実体的レビューは **meaning unit** 単位で行う。1 hunk から複数所見、または複数 changed lines から 1 所見があり得る。
- 評価基準は `paper-review/references/criteria.md` の relevant な部分を使う。
- popup comment は prepared review TeX にある ID だけへ割り当てる。
- `source diff` だけに現れる重要な問題は、検証報告の注意事項として扱う。
- 評価に必要な source、PDF、画像、基準が不足する場合は、推測で確定せず不足情報を明示する。
- 同じ **meaning unit** から delete と add の両対象が出た場合、1 つの所見が複数の popup IDs に対応してよい。

## Diff 対象

review 対象:

- `\DIFdelbegin ... \DIFdelend`
- `\DIFaddbegin ... \DIFaddend`
- `\DIFdelbeginFL ... \DIFdelendFL`
- `\DIFaddbeginFL ... \DIFaddendFL`

delete と add の対応関係は解釈に使ってよい。comment は ID ごとに付ける。

構造上の例外:

- `\item`、`\begin{...}`、`\end{...}`、`\bibitem` などの構造的 control diff は、popup 注釈の挿入で PDF の `/Annots` arrays を壊す可能性があるため skip してよい。

## Comments JSON 契約

- top-level は object にする。
- key は wizard が生成した diff ID に完全一致させる。
- value は PDF popup に表示する comment body string にする。
- すべての ID に non-empty comment を入れる。
- すべての comment に `[意味維持]`、`[意味変化あり]`、`[意味変化要確認]` のいずれかを含める。
- 必要に応じて `[OK]`、`[要確認]`、`[懸念]` を併記する。
- delete 対象では旧表現の役割と削除の適否を評価する。
- add 対象では新表現の効果と追加の適否を評価する。
- 実体的所見がない diff ID は、懸念がなければ短い `[意味維持][OK]` comment で補完する。
- 空白・句読点だけの差分が意味、scope、citation attachment に影響しない場合は `[意味維持][OK]` にする。
- 空白・句読点だけの差分でも影響し得る場合は `[意味変化要確認]` または `[懸念]` として理由を書く。

例:

```json
{
  "R001-D": "[意味維持][OK] 旧表現の削除により、引用の掛かり先が整理されている。",
  "R002-A": "[意味維持][OK] 新表現では著者名を文の主部に自然に統合している。"
}
```

## PDF 注釈

- 想定 viewer: Adobe Acrobat / Acrobat Reader。
- popup 既定値: `Open=false`。
- popup contents: コメント本文を UTF-16BE hex として `/Contents` に保存する。

## 検証

- `comments JSON` の key set が wizard の diff ID と一致する。
- `comments JSON` に empty value がない。
- filled review TeX に unfilled placeholder がない。
- PDF build 後、期待する PDF path が存在する。

試運転の成功条件は script の入出力契約が通ることを中心に判断する。図、layout、spacing、screenshot、image などの視覚 rendering が対象でない限り、PDF の page image 化や viewer による visual spot check は行わない。
