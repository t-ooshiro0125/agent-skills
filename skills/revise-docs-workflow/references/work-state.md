# 作業状態の操作

Python 3 の標準ライブラリだけを使う。以下の `revision.py` は、このスキルの `scripts/revision.py` の絶対パスに置き換える。状態・入力・中間ファイルは利用先の規約に従うローカルの作業場所へ置き、秘密情報を含む記録を公開しない。状態 JSON を直接編集せず、同じ状態への書き込みは逐次実行する。

## 開始と分割

原文は編集前に保存する。内容確認・反証・大きな構成整理を終えた時点の作業本文を別に用意し、依頼条件を次の JSON に記録する。構成整理までの指摘と判断も別途保持し、関係する対象の記録へ引き継ぐ。

```json
{
  "purpose": "文書を何に使うか",
  "reader": "想定読者と前提知識",
  "scope": "依頼された対象範囲",
  "allowed_changes": "許可された整理・説明追加の範囲と保持条件"
}
```

```sh
python3 revision.py --state work.json init --original original.md --working draft.md --brief brief.json --max-iterations 3
python3 revision.py --state work.json status
python3 revision.py --state work.json next
```

`--working` を省略すると原文を作業本文とする。原文・初期作業本文は状態内にも保存される。状態は既存ファイルへ上書き初期化しない。上限は対象ごとの確認記録回数で、文脈変更後の再確認も含む。必要な再確認を見込んで設定する。上限後の未解消事項には保留記録が必要で、分割・結合しても回数はリセットしない。

候補は Markdown の構造から作る。意味の切れ目の判断は担当スキルで行う。条件と手順、説明と表などが離れていたら、連続する候補を `merge` で結合するか `annotate` で関連付ける。表の途中に複数の論点がある場合は行単位に分割できる。列名と区切り行、コードブロック、見出しは途中で切らない。複数構造を結合した対象は、まず表やコードを丸ごと取り出してから細分化する。

```sh
python3 revision.py --state work.json merge --units u0002 u0003
python3 revision.py --state work.json split --unit u0004 --token TOKEN --file parts.json
python3 revision.py --state work.json reorder --units u0001 u0005 u0006
```

`parts.json` は分割後の文字列の配列で、連結すると対象の現行本文と完全一致する必要がある。`reorder` には現存する全 ID を重複なく指定する。分割・結合は旧対象と履歴を保持し、構成変更後は全対象を再確認する。表を行に分けると列名と前後の参照が継承されるが、共通条件や離れた関連箇所が十分かは必ず確認する。

## 一つの対象を処理する

`next` は未処理または再確認待ちの対象を一つ返す。`target` だけを編集し、`reference_only` の目的・読者・条件、見出し、前後、関連箇所、メモは文脈として読む。必要な箇所が足りなければ全文や任意の対象へ戻る。

```sh
python3 revision.py --state work.json show --document original
python3 revision.py --state work.json show --document initial_working
python3 revision.py --state work.json show --document working
python3 revision.py --state work.json show --unit u0004
python3 revision.py --state work.json show --history u0004
python3 revision.py --state work.json annotate --unit u0004 --token TOKEN --file notes.json
```

対象の原文対応が自動で確定できなければ、`original_scope: whole_document` として原文全体を返す。初期作業本文を原文と取り違えない。必要なら次のメモに原文の行範囲を指定する。`findings` には既存の指摘・根拠、`preservation` には保持条件、`criteria` には完了条件を残す。更新前のメモや確認結果も履歴に残り、`show --history` で分割・結合前の対象を含めて取得できる。

```json
{
  "findings": ["問題・根拠、撤回した指摘とその理由"],
  "preservation": ["維持する意味・条件・断定の強さ"],
  "criteria": ["解消を判断する条件"],
  "context_ids": ["u0002"],
  "context_note": "関連対象が持つ共通の適用条件",
  "original_lines": [10, 18]
}
```

指定した対象の本文を UTF-8 ファイルへ書き、最新の `token` で保存する。全文をこのファイルに渡さない。更新後の返却値で新しい版を取得する。

```sh
python3 revision.py --state work.json edit --unit u0004 --token TOKEN --file target.txt --affected u0007
python3 revision.py --state work.json record --unit u0004 --token NEW_TOKEN --file check.json
```

本文・前後・見出し・関連対象・依頼条件が変わると古い確認は使えない。意味上の依存は自動判定しないので、共通条件の変更などは `--affected` または `--all-affected` で影響範囲を指定する。`annotate` 後も新しい token を使う。一回の修正を確認する前に同じ対象へ次の修正はできない。

確認記録は次の形にする。各欄は実際の内容で埋め、問題がない場合も何を確かめたか説明する。形式検査は記述の妥当性や意味の保持を証明しない。

```json
{
  "outcome": "confirmed",
  "problem": "修正対象の問題。問題がなければその判断",
  "evidence": "原文と現行本文から確認した根拠",
  "preservation": ["保持した意味・条件"],
  "criteria": ["完了条件"],
  "checks": [
    {"criterion": "点検した基準", "observation": "実際の本文で確認した内容"}
  ]
}
```

`outcome` は、修正を確認した `confirmed`、変更不要の `unchanged`、再修正が必要な `needs_revision`、保留の `held`。本文を変更した対象を `unchanged` にしない。確認記録は対象の本文と文脈の版に結び付き、過去の指摘・本文・結果は履歴から消さない。

保留には `reason`、具体的な `proposal`、`required_decision` を加える。反復上限で未解消なら同じ情報が必要で、状態は `held` になる。判断が不要で情報確認だけが必要な場合も、確認先・必要な情報を `required_decision` に書く。保留は `next` で飛ばし、他の対象を進める。新しい根拠や判断を受けて戻る場合は理由を添えて再確認待ちにする。上限を使い切った対象は追加修正せず、残る案と理由を報告する。

```sh
python3 revision.py --state work.json invalidate --units u0004 --reason "確認結果に影響する新しい根拠を得た"
python3 revision.py --state work.json invalidate --all --reason "共通の前提を変更した"
python3 revision.py --state work.json brief --file revised-brief.json
```

## 全文の確認と出力

`next` の対象がなくなったら、`show --document working` で再構成された全文を読み、原文との照合と修正文だけでの確認を行う。問題があれば影響箇所を再確認待ちへ戻す。構成・前提・本文の変更は全文確認も無効にする。

```json
{
  "original_comparison": "情報・数値・条件・結論・断定の強さを何と照合し、何を確認したか",
  "standalone_read": "想定読者として全文を読み、順序・つながり・用語について確認した内容"
}
```

```sh
python3 revision.py --state work.json status
python3 revision.py --state work.json final-review --token DOCUMENT_TOKEN --file final.json
python3 revision.py --state work.json finish --output revised.md --report result.json
```

未処理・再確認待ち、必要な確認記録の不足、現行版の全文確認の不足があると `finish` は出力しない。保留がある場合は本文と保留記録を出力できるが、`fully_resolved` は `false` になる。修正版・主な変更・残る確認や判断を依頼された形式で提示し、保留を解決済みと報告しない。

補助スクリプトの変更時は、スキルディレクトリを基準に `python3 tests/test_revision.py` を実行する。
