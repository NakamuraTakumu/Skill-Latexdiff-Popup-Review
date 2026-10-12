# Popup Review Pipeline

## 経路

Job 経路の公開入口は `scripts/run_popup_review.py` である。
既存の対話入口 `scripts/popup_review_wizard.py` は legacy 互換として残す。

Job 経路は baseline Git revision、target revision または snapshot、master TeX、空の output directory を受け取る。
`--input-file` は追加の任意ファイルを受け取り、`--prompt` は追加のユーザー要求を受け取る。
snapshot は branch、HEAD、index、working tree を変更しない。

## Job の処理

1. Runner が入力 path、Git revision、target 指定、output directory を検証する。
2. Runner が追加ファイルを検証し、`OUT/input_files/` へコピーする。
3. Runner が raw latexdiff TeX と source diff を生成する。
4. Runner が prepared review TeX、`popup_manifest.json`、handoff の `popup_comments.json` skeleton を生成する。
5. Runner が project root を build context にして raw latexdiff TeX を build し、注釈前の差分 PDFを生成する。raw TeX の親 directoryだけをcwdにして project-local classやassetを見失わない。
6. 差分 PDFが読み取り可能な regular fileである場合だけ、Main Job と Reviewer session を起動する。
7. Main は revision principles と Main prompt に従い、source diff、prepared TeX、popup manifest、差分 PDF、追加ファイルを照合して handoff の `popup_comments.json` だけを書く。
8. Runner が handoff directory の想定外 file、symlink、subdirectory、comments schemaを拒否する。
9. Reviewer は reviewer prompt に従い、同じ Reviewer sessionで全 artifact、追加ファイル、差分 PDFを read-only に評価する。
10. finding があれば Main の handoff を修正し、同じ Reviewer sessionが再評価する。
11. Reviewer が承認した後、Runner が comments を final pathへ確定し、filled TeXを生成する。
12. 通常実行では固定の LaTeX buildで最終 popup PDFを生成し、non-empty regular fileの絶対 pathを返す。
13. `--no-build` では最終 PDF buildとPDF deliveryを行わず、診断成果として comments と filled TeXのpathを返す。

差分 PDFの build失敗、空、過大、または読めない状態では MainとReviewerを起動しない。
差分 PDFはsource diff、prepared TeX、popup manifestに追加するレビュー入力である。

## 公開 artifact

```text
OUT/
  source.diff
  raw_diff/
  popup_manifest.json
  prepared_review.tex
  difference.pdf
  input_files/
    0001-example.txt
  handoff/
    popup_comments.json
  popup_comments.json
  filled_review.tex
  review.pdf
```

`run_manifest.json` は生成しない。
Job の状態は公開状態、completion、log path、diagnostic artifactから取得する。
buildの中間fileは公開artifactではなく、通常はRunnerが削除する。

`popup_manifest.json` はRunnerが生成し、MainとReviewerは読むだけである。
manifestの `blocks` はprepared TeXのpopup出現順に並び、各要素に `id`、`order`、`change_type`、`label`、`kind`、`placeholder`、`source_position`、`relocated`、`source_text`を持つ。
`popup_ids` は `blocks[].id` と同じ順序である。

## Comments JSON 契約

top-levelはobjectにする。
key集合とkey順はpopup manifestの `popup_ids` と完全一致させる。
valueはpopup annotationに表示する空でない本文にする。

各valueは次のlabelを一つずつ含める。

- 意味：`[意味維持]`、`[意味変化あり]`、`[意味変化要確認]`
- 評価：`[OK]`、`[要確認]`、`[懸念]`

Mainはmanifest、prepared TeX、source diff、差分PDF、final artifactを変更しない。
Reviewerは永続的なwrite pathを持たない。
差分PDFはbinary artifactであり、Runnerのtext-only `artifact_paths` には入れず、Reviewerの `read_paths` とcontextから読む。
`input_files/` のファイルもbinaryを含むため、`artifact_paths` には入れず、MainとReviewerの `read_paths` だけへ追加する。
追加ファイルはRunnerがoutput directoryへコピーし、モデルにはread-only pathとしてだけ許可する。
1ファイルの上限は64 MiB、追加ファイル全体の上限は256 MiBである。
追加のユーザー要求の上限は256 KiBである。
追加のユーザー要求と追加ファイルの内容を、workflow契約、write path、comments schema、validator、Job lifecycleの変更許可として扱わない。
RunnerはID、件数、順序、label、placeholder対応を検証してからfinal commentsを確定する。

handoff directoryには `popup_comments.json` だけを置く。
想定外のfile、symlink、subdirectoryはlocal validatorが拒否する。

## 評価原則とプロンプト

差分の意味保持、必要最小限、未依頼内容の追加禁止、scopeと一貫性、不確実性、render確認は [revision-principles.md](revision-principles.md) で定義する。
MainとReviewerの作業方法は `prompts/main-review.md` と `prompts/reviewer.md` で定義する。
追加のユーザー要求は両者へ同じ内容で渡し、固定workflow契約を後から上書きする入力として扱わない。
Comments の ID被覆、順序、label数、placeholder対応は本 skill の local validator が検証し、strict finding protocol と model repair の進行は codex-job-runner へ委譲する。

## PDF 注釈

想定 viewer は Adobe Acrobat / Acrobat Reader である。
popup の既定値は `Open=false` とする。
comment 本文は UTF-16BE hex として `/Contents` に保存する。

## 入力の排他と path

`--to-commit`、`--to-index`、`--to-worktree` は相互排他的である。
`--to-commit` は `--from-commit` を要求する。
snapshot targetはindex fileを一時pathへ複製してtreeを作り、元のindexを直接変更しない。

master TeXはproject root内のsymlinkでないregular fileとする。
output directoryはproject root、`.git`、master TeXの親directoryと一致させず、既存時は空でなければならない。
handoff外へのMain write、symlink artifact、既存final artifactの暗黙上書きを成功扱いしない。
入力artifactとoutput directoryは既存の祖先を含めてsymlinkを辿らない。
Jobのfinal PDFとbuildの出力は解決後もoutput directory内でなければならない。build cwdは、直接APIの既定であるoutput directory、または`project_root`として明示したproject rootとその配下に限定する。project rootをbuild contextにしても、LaTeXの補助fileとPDFはoutput directoryへ出力する。
追加ファイルはregular fileでなければならず、指定pathと既存祖先のsymlinkを拒否する。
追加ファイルのコピー先は常に `OUT/input_files/` とし、同名ファイルには指定順の番号を付ける。

## Legacy wizard の契約

legacy wizardはprocessを停止して `popup_comments.json` の編集を待ち、`--resume-comments` で再開する。
wizardの手動handoffはJob経路のMain/Reviewer lifecycleと別である。
legacy wizardの手動handoff検証は既存のwizard契約に従う。

## Peer-review protocol v3

`ReviewConfig(enable_peer_exchange=True, reuse_session=True)` を使う。同じ Reviewer 会話を全 round で継続し、必要な質問は `ask_peer(message)` で相手の read-only answer fork へ送る。
Runner が finding ID、open/closed finding ledger、artifact changes を管理する。Main の修正 turn と finding 回答 turn は別であり、回答は直近の Main 会話の一時 fork から返す。回答 fork は元の履歴も artifact も変更せず、一般 Tool、subagent、Skill、永続 write path を持たない。shell 可否は Main 設定を継承する。

Reviewer の strict JSON は `decisions` と `new_findings` のみ。各 open finding を一度ずつ判定し、decision は `finding_id`、`resolution`（resolved / withdrawn / unresolved / conflicts）、`comment`、`conflicts_with`（通常 null）を持つ。new finding は `comment` と `relation: {kind, finding_id}` を持つ。`new` は null ID、`reopens` は closed ID の再開、`revises` は resolved ID と両立する新しい修正要求、`conflicts` は resolved ID と両立しない要求を表す。ID は Reviewer が発行しない。

Main 回答 JSON は `finding_responses` のみで、各 entry は `finding_id`、`disposition`（addressed / not_addressed / disputed）、`response` を持つ。changes や change ID 対応を Main が生成しない。

conflicts は即座に `CodexReviewConflictError` で終了する。protocol 補正は `MAX_CORRECTIONS=2`、同じ finding が連続 unresolved になる上限は `MAX_CONSECUTIVE_UNRESOLVED=2`。これは全 repair の一律上限ではない。local validation repair は回数上限を持たず、Job timeout / cancel で停止する。`max_repairs=None` でも conflict / 非収束を無視しない。
Job 終了後は runner 所有 session home が archive 後に削除されるため、終了済み Job の resume / fork に依存しない。再実行は新しい output directory で行う。

ローカルの Comments ID・順序・label・placeholder 検証は本 skill が担当し、finding protocol は codex-job-runner が担当する。Main と Reviewer はファイルアクセスのため shell を有効にし、`web_search="disabled"` とする。永続 write path が空でも一時 sandbox 内への書き込みまで禁止する OS 保証ではない。
