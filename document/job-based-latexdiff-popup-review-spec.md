---
title: "Job-based Latexdiff Popup Review仕様"
responsibility: "Git管理されたLaTeX差分を評価し、検証済みpopup注釈付きPDFを生成する公開契約を定義する。"
summary: "Git revisionまたはsnapshotからraw diff、注釈前の差分PDF、popup comments、最終PDFを生成し、追加ファイルと追加要求をMainとReviewerへ渡す契約を定める。Mainには文章規則も渡す。"
created: "2026-08-03 11:06 UTC"
updated: "2026-10-12"
workspace: "latexdiff-popup-review"
stale: false
---

# Job-based Latexdiff Popup Review仕様

## Background

既存のJob経路には、差分レビューの対象を越える外部査読依存と補助文書契約が混在していた。
今回の仕様では、差分レビューの評価原則、popup作業方法、機械検証を分離し、実際に利用できるCLIとPython APIへ揃える。
既存のlegacy wizardは利用者互換のために残す。

## At a Glance

```text
Git revision/snapshot
        |
        v
raw diff TeX + source.diff + popup manifest
        |
        v
difference.pdf (raw diff TeXからbuild)
        ^
input_files/ + extra_prompt
        |
        v
Main: popup_comments.json --> Reviewer: read-only finding/approval
        ^                         |
        +------ repair ----------+
        |
        v
validated comments -> filled TeX -> review.pdf
```

通常実行のdeliveryはReviewer承認後に確定し、`--no-build` はPDFをdeliveryしないdiagnostic modeである。
公開APIとartifact構成は[公開Python API](#公開python-api)と[Artifact](#artifact)で確認する。

## Body

- [目的](#目的)
- [適用範囲](#適用範囲)
- [用語](#用語)
- [入力](#入力)
- [CLI](#cli)
- [公開Python API](#公開python-api)
- [評価原則](#評価原則)
- [レビュー要求](#レビュー要求)
- [Comments JSON](#comments-json)
- [Artifact](#artifact)
- [権限](#権限)
- [失敗](#失敗)
- [完了条件](#完了条件)
- [Legacy境界](#legacy境界)
- [非対象](#非対象)

### 目的

Git管理されたLaTeX projectの二つの状態を比較し、変更箇所を確認してpopup注釈付きPDFを生成する。
Job経路は差分レビューだけを扱い、論文全体の査読や原稿の自動改稿を行わない。

### 適用範囲

対象は `scripts/run_popup_review.py` の公開CLIと `PopupReviewJob` APIである。
既存の対話workflowはlegacy境界で扱い、Jobの契約とは分離する。

### 用語

- **Runner**：Git差分、raw latexdiff TeX、prepared TeX、popup manifest、差分PDF、最終artifactを準備し、ローカル検証とbuildを実行する処理。
- **Main**：差分artifactを読み、popup IDへ対応するcommentsをhandoffへ書くモデル。
- **Reviewer**：Mainのcommentsを全artifactと照合し、findingを返すread-onlyモデル。
- **意味単位**：差分が一つの意味を担う最小のまとまりであり、文、式、図、参照、構造的なLaTeX commandを含む。
- **popup manifest**：popup ID、変更種別、順序、source位置、placeholder対応を記録するRunner生成artifact。
- **handoff directory**：Mainだけに永続writeを許可するrun固有directory。
- **差分PDF**：raw latexdiff TeXから、popup注釈を埋める前にbuildするレビュー入力。
- **最終artifact**：検証を通過して利用者へ渡せるcomments、filled TeX、popup PDF。

### 入力

入力はbaseline revision、target revisionまたはindex・working tree snapshot、master TeX、空のoutput directoryである。
`--input-file` は任意の追加ファイルを受け取り、`--prompt` は追加のユーザー要求を受け取る。
`--to-commit`、`--to-index`、`--to-worktree` は相互排他的である。
`--to-commit` は `--from-commit` を要求する。
snapshotはbranch、HEAD、index、working treeを変更しない。

project rootはGit repository rootでなければならない。
master TeXはproject root内のsymlinkでないregular fileでなければならない。
output directoryはproject root、`.git`、master TeXの親directoryと一致せず、既存なら空でなければならない。
入力artifact、output directory、handoff directoryの既存祖先にsymlinkを置かない。
追加ファイルの指定pathと既存祖先のsymlinkを拒否し、同名ファイルには指定順の番号を付ける。

### CLI

```bash
python3 scripts/run_popup_review.py \
  --output-dir OUT \
  --master-tex main.tex \
  --from-commit OLD \
  --to-commit NEW
```

公開optionは `--project-root`、`--latexdiff-option`、`--timeout`、`--no-build`、`--keep-build-files`、`--model`、`--reasoning-effort`、`--reviewer-model`、`--reviewer-reasoning-effort`、`--reviewer-timeout`、`--codex-bin`、`--input-file`、`--prompt` である。
`--input-file` は複数指定できる。
`--prompt` は追加のユーザー要求を指定する。
`--codex-bin` は使用するCodex executableを指定する。
自由記述のbuild commandは受け付けず、buildは固定argvで実行する。

### 公開Python API

公開classは `PopupManifest`、`PopupReviewJob`、`PopupReviewCompletion` である。
`PopupReviewJob` は `config`、manifest、source diff、prepared TeX、handoff comments skeleton、差分PDFを受け取る。
`output_dir`、`no_build`、`build_cwd`、`keep_build_files`、`pdf_output`、Reviewer設定は任意で指定できる。

```python
from pathlib import Path

# Load the skill first so its sibling-runner import fallback is available.
from scripts.run_popup_review import PopupManifest, PopupReviewJob, stage_input_files
from codex_job_runner import SessionConfig, WorkspaceConfig

out = Path("OUT")
staged = stage_input_files([Path("path/to/context.md")], out)
job = PopupReviewJob(
    config=SessionConfig(timeout=1800, workspace=WorkspaceConfig(cwd=out)),
    manifest=PopupManifest.from_path(out / "popup_manifest.json"),
    source_diff=out / "source.diff",
    prepared_tex=out / "prepared_review.tex",
    comments_skeleton=out / "handoff/popup_comments.json",
    diff_pdf=out / "difference.pdf",
    input_files=staged,
    extra_prompt="追加資料を差分の判断材料にする",
)
completion = job.run()
```

生成時のJobは `pending` である。
`start()` は同じJob instanceを返し、`run()` は起動して完了を待つ。
`result()` は未起動Jobを自動起動せず、`cancel()` はpendingまたはrunning Jobを停止する。
`PopupReviewCompletion` は `comments_path`、`filled_tex_path`、`pdf_path`、`comment_count`、`mode`、`log_path` を持つ。
`--no-build` または `no_build=True` では `pdf_path` は `None` であり、modeはdiagnosticになる。

### 評価原則

MainとReviewerは [revision-principles.md](../references/revision-principles.md) を評価原則として使う。
原則は意味保持、必要最小限、未依頼内容の追加禁止、範囲と一貫性、文字数制限、既存TODO保持、不確実性、render確認である。
Mainだけには、追加要求、revision principles の後、Main prompt の前に 同梱の `references/writing.md` 全文を渡す。
文章規則はpopup commentの作成にだけ適用し、原稿を編集する権限、TODOを追加する権限、またはpopup workflowの固定契約を変更する権限を与えない。
論文全体の評価面、引用探索、Web検索、根拠台帳をJobの契約へ追加しない。

### レビュー要求

Mainはsource diff、prepared TeX、popup manifest、差分PDF、追加ファイルを照合し、意味単位をpopup IDへ割り当てる。
deleteとaddを別々に評価し、構造的なLaTeX差分も対象にする。
Mainのwrite pathはhandoff directoryだけであり、`popup_comments.json` 以外を書かない。

Reviewerはsource diff、prepared TeX、popup manifest、handoff comments、差分PDF、追加ファイルをread-onlyで照合する。
追加のユーザー要求はMainとReviewerへ同じ内容で渡す。
`input_files`のstage済みファイルもMainとReviewerへread-onlyで渡す。
`PopupReviewJob`のconstructorは外部ファイルをコピーしないため、外部pathを使う場合は生成前に`stage_input_files()`を呼ぶ。
Reviewerは具体的なpopup ID、位置、不整合、必要な修正をfindingとして返す。
findingが残る場合はMainへ修正を戻し、同じReviewer sessionで再査読する。
Comments の ID完全被覆、順序、label数、placeholder対応は本 skill の local validator が検証し、strict finding protocol と model repair の進行は codex-job-runner が管理する。

### Comments JSON

top-levelはobjectであり、key集合と順序はpopup manifestの `popup_ids` と完全一致する。
valueは空でない本文であり、意味labelを一つ、評価labelを一つ、具体的な理由とともに含める。
意味labelは `[意味維持]`、`[意味変化あり]`、`[意味変化要確認]` のいずれかである。
評価labelは `[OK]`、`[要確認]`、`[懸念]` のいずれかである。

### Artifact

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

`popup_manifest.json` はRunnerが生成し、MainとReviewerは変更しない。
差分PDFはraw latexdiff TeXから生成し、prepared TeXから生成するとは扱わない。
追加ファイルは `OUT/input_files/` に保持し、binaryを含むためReviewerのtext-only `artifact_paths` へは入れない。
`run_manifest.json` は生成しない。
build中間fileは公開artifactではなく、通常はRunnerが削除する。

### 権限

Mainの永続write pathはhandoff directoryだけである。
Reviewerの永続write pathは空である。
追加ファイルはRunnerがregular fileとして検証し、1ファイル64 MiB、合計256 MiBを上限として`OUT/input_files/`へコピーする。
追加のユーザー要求は256 KiBまでとする。
MainとReviewerにはstage済みpathをread-onlyで許可する。
追加のユーザー要求と追加ファイルの内容を、workflow契約、write path、comments schema、validator、Job lifecycleの変更許可として扱わない。
source diff、prepared TeX、popup manifest、差分PDF、既存final artifactをモデルが変更してはならない。
shell実行を許可しても、write pathを拡張しない。
handoff directoryに想定外のfile、symlink、subdirectoryがあればlocal validatorが拒否する。

### 失敗

入力検証、差分準備、差分PDF build、Main、Reviewer、comments検証、filled TeX、最終PDF buildのいずれかが失敗すれば後続段階を開始しない。
差分PDFが空、過大、読めない場合はMainとReviewerを起動しない。
未検証artifactを成功成果物として報告しない。
timeoutまたはcancelでは同じrunの後続処理を開始しない。
途中状態からの段階単位resumeは提供せず、再実行は空の新しいoutput directoryで行う。

### 完了条件

通常実行は、Jobがcompletedで、Reviewerが未解決findingなしで承認し、commentsのID集合、順序、label、placeholderが検証され、filled TeX、差分PDF、最終PDFがnon-empty regular fileであり、completionのpathが確定artifactと一致した場合だけdeliveryできる。
`--no-build` はcommentsとfilled TeXの検証成功時だけdiagnostic modeで完了し、PDF deliveryとは扱わない。

### Legacy境界

`scripts/popup_review_wizard.py` はprocessを停止して利用者が `popup_comments.json` を手でhandoffする既存workflowである。
legacy wizardの手動handoff検証はwizard自身の契約に従い、JobのMain/Reviewer lifecycleを起動しない。

### 非対象

- 複数論文または複数Jobの並列レビュー
- 原稿の自動revision
- Web検索による引用またはBibTeX照合
- Job経路の途中状態resume
- 任意build commandの実行
- popup manifestのモデル編集
- host processのOS sandbox保証
- PDF viewer相当の既定visual inspection

## Peer-review protocol v3

`ReviewConfig(enable_peer_exchange=True, reuse_session=True)` を使う。同じ Reviewer 会話を全 round で継続し、必要な質問は `ask_peer(message)` で相手の read-only answer fork へ送る。
Runner が finding ID、open/closed finding ledger、artifact changes を管理する。Main の修正 turn と finding 回答 turn は別であり、回答は直近の Main 会話の一時 fork から返す。回答 fork は元の履歴も artifact も変更せず、一般 Tool、subagent、Skill、永続 write path を持たない。shell 可否は Main 設定を継承する。

Reviewer の strict JSON は `decisions` と `new_findings` のみ。各 open finding を一度ずつ判定し、decision は `finding_id`、`resolution`（resolved / withdrawn / unresolved / conflicts）、`comment`、`conflicts_with`（通常 null）を持つ。new finding は `comment` と `relation: {kind, finding_id}` を持つ。`new` は null ID、`reopens` は closed ID の再開、`revises` は resolved ID と両立する新しい修正要求、`conflicts` は resolved ID と両立しない要求を表す。ID は Reviewer が発行しない。

Main 回答 JSON は `finding_responses` のみで、各 entry は `finding_id`、`disposition`（addressed / not_addressed / disputed）、`response` を持つ。changes や change ID 対応を Main が生成しない。

conflicts は即座に `CodexReviewConflictError` で終了する。protocol 補正は `MAX_CORRECTIONS=2`、同じ finding が連続 unresolved になる上限は `MAX_CONSECUTIVE_UNRESOLVED=2`。これは全 repair の一律上限ではない。local validation repair は回数上限を持たず、Job timeout / cancel で停止する。`max_repairs=None` でも conflict / 非収束を無視しない。
Job 終了後は runner 所有 session home が archive 後に削除されるため、終了済み Job の resume / fork に依存しない。再実行は新しい output directory で行う。

ローカルの Comments ID・順序・label・placeholder 検証は本 skill が担当し、finding protocol は codex-job-runner が担当する。Main と Reviewer はファイルアクセスのため shell を有効にし、`web_search="disabled"` とする。永続 write path が空でも一時 sandbox 内への書き込みまで禁止する OS 保証ではない。
