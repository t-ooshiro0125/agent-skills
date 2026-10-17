# review-workflow

差分レビュー・反証・リファクタ候補の確認を順に実行します。

## 依頼例

```text
$review-workflow 現在の未コミット差分について、レビュー・反証・リファクタ候補の確認を実行してください。
```

[review](review.md)、[implementation-challenge](implementation-challenge.md)、[review-refactor](review-refactor.md) と一緒に登録してください。

レビューをまとめて進める場合は、まず `review-workflow` を使います。調査では決まらない仕様上の意思決定やリスク受容などが残った場合は、その判断を確定させてから [review-handoff](review-handoff.md) を呼び出し、対応指示にまとめます。個別の工程だけを行いたい場合は、[review](review.md)、[implementation-challenge](implementation-challenge.md)、[review-refactor](review-refactor.md) を直接呼び出します。

エージェント向けの手順は [SKILL.md](../../skills/review-workflow/SKILL.md)、表示用メタデータは [agents/openai.yaml](../../skills/review-workflow/agents/openai.yaml) を参照してください。
