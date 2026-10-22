# 作業状態の操作

Python 3.10 以上と [markdown-it-py](https://pypi.org/project/markdown-it-py/) を使う。利用先の規約に従う Python 環境へ、先に依存パッケージを導入する。新しい環境が必要なら作業用の仮想環境を用意し、その Python を以降の `python3` として使う。

```sh
python3 -m pip install -r /path/to/revise-docs-workflow/requirements.txt
```

依存バージョンは [requirements.txt](../requirements.txt) に固定している。CommonMark とパイプ形式の表を解析して候補と切断可能位置を判断し、本文は元の文字列から切り出して再構成する。以下の `revision.py` は `scripts/revision.py` の絶対パスに置き換える。状態・入力・中間ファイルは利用先の規約に従うローカルの作業場所へ置く。状態 JSON を直接編集せず、同じ状態への書き込みは逐次実行する。

## 依頼条件と観点を登録する

原文を編集前に保存し、内容確認・反証を経て構成案を検討する。書き換える前の本文を作業本文とし、説明が必要な表現を登録してから構成・表現を修正する。依頼者の困りごと・改善後に理解したいことと、SKILL.md で選んだ観点を `brief.json` に記録する。指定がない困りごとはその旨を記し、職種・役割から専門用語を既知としたり、読者を初心者に固定したりしない。

原文・依頼文・会話の指定を読んでから、[読者の指定](../SKILL.md#読者の指定)を確認する。役割や説明量だけが既知ならそれらを引き継ぎ、未指定の知識条件だけを一度確認する。選択・自由入力・指定済みの条件を `reader` に、具体的な指摘と選んだ読者に必要な説明を `reader_difficulties` に、理解・判断できるようにする内容を `desired_understanding` に記す。知識の条件を文体の変更指示へ置き換えない。文体の指定は `allowed_changes` 等の既存の保持条件へ引き継ぎ、別指定がなければ原文を保つ。

```json
{
  "purpose": "文書を何に使うか",
  "reader": "想定読者と確認できた前提知識",
  "reader_difficulties": "依頼で示された、意味や判断を読み取りにくい点",
  "desired_understanding": "改善後に何を理解・判断できればよいか",
  "scope": "依頼された対象範囲",
  "allowed_changes": "許可された整理・説明追加の範囲と保持条件",
  "criteria": [
    {"id": "meaning", "stage": "unit", "description": "意味・条件・断定の強さを保つ"},
    {"id": "terms", "stage": "unit", "description": "読者に必要な用語の意味・役割が分かる"},
    {"id": "referents", "stage": "unit", "description": "何について何を説明・確認・判断するか分かる"},
    {"id": "comparison", "stage": "document", "description": "原文の情報と保持条件を全文で照合する"},
    {"id": "coherence", "stage": "document", "description": "原文なしでも説明順序と文のつながりを追える"}
  ]
}
```

これは読みやすさを改善する依頼の登録例。依頼に応じて少数の観点を選び、基本観点で扱えない固有の改善目標も必要なものだけ登録する。`unit` は各対象、`document` は全文確認に使う。両段階の観点が必要で、ID は英小文字で始め、英小文字・数字・`_`・`-` を使って重複しないように付ける。見つかった個別の問題だけから観点を作らない。

```sh
python3 revision.py --state work.json init --original original.md --working draft.md --brief brief.json --max-iterations 3
python3 revision.py --state work.json next
```

`--working` を省略すると原文を作業本文とする。原文・初期作業本文は状態内にも保存し、既存の状態へ上書き初期化しない。上限は対象ごとの確認記録回数で、文脈変更後の再確認も含む。必要な再確認を見込んで設定する。上限を合格とは扱わず、分割・結合でも回数はリセットしない。

## 分割候補を調整する

候補は意味の切れ目を担当スキルで判断して調整する。条件と手順、説明と表などが離れていたら、連続する候補を結合するか関連付ける。表は行単位に分割できるが、列名と区切り行、コードブロック、見出し、区切り線は途中で切らない。インラインコード・リンクなどの構文やリンクの参照定義も途中で切らず、通常の文章や構文の前後で分ける。リストは継続行・入れ子を含む上位項目単位で分ける。複数の構造を結合した対象は、まず表やコードを丸ごと取り出してから細分化する。

保留中、または直前の編集の確認待ちの対象が含まれると、`split`・`merge` は状態を変えずに拒否する。保留は新しい根拠や判断を得て理由付きの `invalidate` で再確認待ちへ戻す。直前の編集は `record` で確認結果を保存してから再編成する。編集の確認待ちは `invalidate` だけでは解除されない。

```sh
python3 revision.py --state work.json merge --units u0002 u0003
python3 revision.py --state work.json split --unit u0004 --token TOKEN --file parts.json
python3 revision.py --state work.json reorder --units u0001 u0005 u0006
```

`parts.json` は分割後の文字列の配列で、連結すると現行本文に完全一致する必要がある。`reorder` には現存する全 ID を重複なく指定する。分割した表の列名と行、リストの項目は、順序・連続を保ってまとめて移動する。部分ごとの変更が必要なら先に構造全体へ結合する。まとまり全体を移す場合も、通常段落がリストの続きになるなど構造の境界が変わる配置は拒否する。引用内でも構造を保ち、切断位置は引用行の先頭にする。表の行には列名を含む先頭部分と前後の参照を継承するが、共通条件や離れた関連箇所が十分かは担当者が確かめる。

分割・結合は旧対象と履歴を残し、確認済みを新しい対象へ引き継がない。計画の条件も継承・統合し、現在の対象に合わなければ計画を更新する。構成変更後は全対象を再確認する。登録した表現の ID・引用と位置・結果履歴も残し、置換後の全対象へ対応付け直す。詳細は[表現ごとの確認](expression-checks.md)を参照する。

## 対象を読み、計画を登録する

`next` は未処理または再確認待ちを一つ返す。`hold_active` のある対象は、文脈変更で状態が `recheck` になった場合も候補から外し、他の対象を進める。`target` の本文・計画・`required_criteria`、`required_expressions` の個別の表現と、`reference_only` の依頼条件・見出し・前後・関連箇所・メモを読む。`previous_review` は過去の結果で、現行版に使えるとは限らない。必要な文脈が不足すれば原文・全文・任意の対象や履歴へ戻る。

```sh
python3 revision.py --state work.json show --document original
python3 revision.py --state work.json show --document initial_working
python3 revision.py --state work.json show --document working
python3 revision.py --state work.json show --unit u0004
python3 revision.py --state work.json show --history u0004
```

原文対応を自動で確定できない場合は `original_scope: whole_document` として原文全体を返す。初期作業本文を原文と取り違えない。既存の指摘・根拠や参照を残す場合は `annotate` を使う。入力できるのは `findings`、`context_note`、`context_ids`、原文の包含行範囲 `original_lines`。これらは確認結果にはならない。

計画は、問題・根拠・修正案・保持条件と、`unit` 段階に登録した全基準 ID に対応する具体的な完了条件に限定する。`criteria` の値は、この対象で何が理解できればよいかを示す文字列にする。

```json
{
  "problem": "依頼の観点に照らして点検する問題・不足",
  "evidence": "原文と依頼条件から確認できる根拠",
  "proposal": "意味を保ってどう直すか。変更が必要か未確定なら点検後に判断する",
  "preservation": ["維持する意味・条件・断定の強さ"],
  "criteria": {
    "meaning": "この対象の条件・数値・留保を変えていない",
    "terms": "この対象に必要な用語が何を指し、何のために使うかを読み取れる",
    "referents": "何を対象に、何を確認・判断するか本文から分かる"
  }
}
```

```sh
python3 revision.py --state work.json plan --unit u0004 --token TOKEN --file plan.json
```

結果・合否・`checks`・`outcome` を混ぜた計画は拒否する。計画登録だけでは確認済みにならない。登録した観点の ID は計画や結果から省略できず、観点自体を変更する場合は `brief` で更新する。

## 修正後に別入力で確認結果を保存する

修正対象の本文だけを UTF-8 ファイルへ書き、更新された token で保存する。意味上の依存は自動判定しないため、共通条件などの変更には `--affected` または `--all-affected` で影響範囲を指定する。

```sh
python3 revision.py --state work.json edit --unit u0004 --token TOKEN --file target.txt --affected u0007
python3 revision.py --state work.json show --unit u0004
```

現行本文と参照文脈を実際に点検し、先に[表現ごとの確認結果](expression-checks.md)を保存する。表現結果の保存でもまとまりの token は変わるため、全表現の保存後に `show --unit` でまとまりを取得し直し、表現結果も含めて点検してから別の `result.json` を作る。結果入力は `outcome` と `checks` だけで、基準の定義・省略・差し替えはできない。点検時に取得した `token` と、同じ対象の `required_criteria` に示された基準の版を、それぞれ CLI の `--token` と各 `criterion_version` に使う。保存直前に最新の版を取り直して古い結果へ自動で付け直さない。版が変わったら対象と文脈を取得し直して点検する。本文・文脈・計画・表現結果の版は `token` に、基準の定義・完了条件の版は `criterion_version` に結び付く。

```json
{
  "outcome": "confirmed",
  "checks": [
    {"criterion_id": "meaning", "criterion_version": "提示された版", "status": "passed", "observation": "現行本文で条件・数値・留保を原文と照合した内容"},
    {"criterion_id": "terms", "criterion_version": "提示された版", "status": "passed", "observation": "現行本文のどの用語をどの説明で理解できるか確認した内容"},
    {"criterion_id": "referents", "criterion_version": "提示された版", "status": "passed", "observation": "現行本文から対象と確認・判断内容を読み取った内容"}
  ]
}
```

```sh
python3 revision.py --state work.json record --unit u0004 --token CURRENT_TOKEN --file result.json
```

全 ID に現行版の結果が必要で、重複・未知の ID・古い版・単なる「OK」は拒否する。`status` は `passed`、`needs_revision`、`held`、`not_applicable`。適用しない基準には `reason` を添える。説明が必要なのに根拠や判断がない場合は適用外にせず、`held` に `reason`・`proposal`・`required_decision` を添える。

`outcome` は `confirmed`、`unchanged`、`needs_revision`、`held`。全基準を満たすか理由付き適用外にした場合にだけ、確認済み・変更不要にできる。本文を変更した対象は `unchanged` にできない。未解消の基準がある場合は再修正または保留とし、保留として出す対象の未解消基準すべてに具体案・理由・必要な判断を残す。

補助コードは定型的な保存・呼び出しを補助する範囲で使う。本文の変更有無で `confirmed`／`unchanged` を自動選択したり、修正前に用意した `checks` を自動登録したりしない。変更不要の対象も、現行本文を読んでから結果を作る。同じ結果の文言での再確認は許容し、文言の変更を再読の証拠にしない。形式・ID・版の検査も、実際の再読や内容の妥当性を証明しない。

## 変更・反復・保留

本文・前後・見出し・関連対象・依頼条件・計画が変わると、古い確認を完了に使えない。本文を以前と同じ内容へ戻した場合も、編集前の token・content_token は再利用できず、復元後の本文を点検する。基準や完了条件を変えた場合も、その版の結果が必要になる。観点を無言で削除・別段階へ移動する更新は拒否する。適用しない項目は理由付きの結果として残す。

```sh
python3 revision.py --state work.json invalidate --units u0004 --reason "確認結果に影響する新しい根拠を得た"
python3 revision.py --state work.json invalidate --all --reason "共通の前提を変更した"
python3 revision.py --state work.json brief --file revised-brief.json
```

一回の修正を確認する前に同じ対象へ次の修正はできない。保留は `next` で飛ばして他を進める。保留結果は直接上書きできず、新しい根拠や判断を得た対象を理由付きの `invalidate` で再確認待ちへ戻してから記録する。個別の基準に `held` が一つでもあれば、他の基準が `needs_revision` で全体が `recheck` の場合にも `hold_active` を保持する。旧状態のフラグが偽でも、保存済みの保留チェックを優先する。解除は直近の確認記録の版と対応付けて保存し、後の保留へ流用しない。文脈変更などで状態が `recheck` になっても、`hold_active` が残る対象は、明示的な `invalidate` まで結果の再保存・編集・分割・結合を拒否する。`target` と `status` の対象一覧でこの阻止状態も確認できる。

反復上限でまだ `needs_revision` の基準がある場合、具体案・理由・必要な判断が揃うまで記録を受け付けず、揃ったら `held` として保存する。上限到達を合格にせず、上限のままでは追加修正せずに残る案を報告する。

必要な情報・判断が加わり、現行の形式2状態で編集を再開する場合は、理由付きで上限を明示的に増やせる。上限だけを増やしても保留・本文・既存の確認結果は変えず、履歴と反復回数を保つ。対象は新しい根拠とともに再確認待ちへ戻し、修正・確認する。全文確認もやり直す。

```sh
python3 revision.py --state work.json extend-limit --max-iterations 6 --reason "不足していた判断が確定し、残る修正と再確認を行う"
python3 revision.py --state work.json invalidate --units u0004 --reason "追加された判断の根拠と内容"
```

現在と同じ上限や引き下げは拒否する。上限に達しただけで自動的に増やしたり、状態 JSON を直接編集して回数を消したりしない。

## 全文の確認と出力

`next` の対象がなくなったら `status` で全文段階の ID を確認する。`plan --document` に同じ計画形式で、`document` 段階に登録した全基準 ID の具体的な完了条件を登録する。

```sh
python3 revision.py --state work.json status
python3 revision.py --state work.json plan --document --token DOCUMENT_TOKEN --file document-plan.json
python3 revision.py --state work.json show --document working
```

原文との照合と修正文だけでの全文確認を行い、結果を別入力へ記録する。全文基準にも、省略のない ID・現在の版・実際の観察内容が必要になる。

```json
{
  "original_comparison": "全文を原文と照合して何を確認したか",
  "standalone_read": "修正文だけを想定読者として読んで何を確認したか",
  "checks": [
    {"criterion_id": "comparison", "criterion_version": "全文基準の提示された版", "status": "passed", "observation": "全文で保持した情報・条件・断定の具体的な確認"},
    {"criterion_id": "coherence", "criterion_version": "全文基準の提示された版", "status": "passed", "observation": "全文の説明順序・つながりについて確認した内容"}
  ]
}
```

```sh
python3 revision.py --state work.json final-review --token CURRENT_DOCUMENT_TOKEN --file final-result.json
python3 revision.py --state work.json finish --output revised.md --report result.json
```

全文確認で再修正が必要なら影響対象を再確認待ちへ戻す。全文の保留結果は、同じ文書の版で直接上書きできず、個別対象の結果を保存し直すだけでも解除できない。新しい根拠・判断を得たら、理由付きの `invalidate` で影響対象を再確認待ちへ戻し、必要な修正と対象の再確認を行ってから全文を確認し直す。未処理・再確認待ち・未確認の基準・現行版の全文確認の不足があると `finish` は出力しない。個別の表現・対象・全文のいずれかに保留がある場合は具体案付きで出力できるが、`fully_resolved` は `false` になる。修正版・主な変更・残る確認事項を提示し、保留まで解決済みと報告しない。

既存の通常ファイルへ出力する場合はアクセス権限を引き継ぐ。新規の本文・レポートは通常のファイル作成と同じ `0666 & ~umask` の権限にし、新規の状態ファイルは `0600` にする。シンボリックリンクを状態・出力先に指定した場合はリンクを置換せずエラーにする。本文とレポートは両方を準備してから置換し、途中のエラーでは復元を試みる。復元にも失敗した場合は回復用ファイルのパスを示す。

## 既存状態の移行と検証

形式1の状態は、新しい依頼条件と登録基準を用意して移行する。原文・現行本文・対象 ID・履歴・反復回数を保持し、古い確認を新しい基準の合格へ変換しない。全対象を再確認待ちへ戻し、新しい計画と結果・全文確認が揃うまで出力を拒否する。

```sh
python3 revision.py --state work.json upgrade --brief brief.json
```

移行と同時に上限を増やす場合は `upgrade --max-iterations` を使う。移行後の形式2には前述の `extend-limit` を使い、いずれも過去の回数は消さない。旧状態・作業ログ・原文などは指定された公開範囲に従って保持する。記録の充足と、依頼者が理解できる修正文になったかは別に確認する。

旧形式2で解除済みの対象には、解除した確認記録の版が保存されていない場合がある。偽フラグのまま未解除の状態もあるため、フラグだけで解除済みとは判断しない。旧実行ログなどで、その対象への理由付きの `invalidate` と解除の根拠を確認できた場合は、`migrate-release` で既存の解除を移行する。新たな `invalidate` は不要で、本文・状態・対象ID・世代・反復回数・既存の確認記録を保ち、移行根拠を履歴へ追加する。

```sh
python3 revision.py --state work.json show --unit u0004
python3 revision.py --state work.json migrate-release --unit u0004 --token CURRENT_TOKEN --review-token HELD_REVIEW_TOKEN --evidence "旧実行ログの対象・解除理由・根拠を確認した箇所"
```

`CURRENT_TOKEN` は現在の対象、`HELD_REVIEW_TOKEN` は `previous_review` にある保留記録のtokenを使う。移行は解除記録のない旧状態に限定し、現在と同じ版の保留・版の不一致・根拠の空欄は拒否する。形式や版の検査で過去の明示解除を証明できるわけではなく、根拠の確認は担当者が行う。確認できなければ保留を維持し、新しい情報・判断を得た場合は通常の `invalidate` を使う。

補助スクリプトの変更時は、依存導入済みの環境でスキルディレクトリから `python3 tests/test_revision.py` を実行する。
