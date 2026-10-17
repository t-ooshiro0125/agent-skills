# challenge-docs

文書レビューの指摘や追加・削除案に反証を試み、維持・撤回・修正を判定します。全文の再レビューやファイル編集は行いません。

## 依頼例

```text
$challenge-docs 直前の文書レビューの指摘を、原文と根拠に照らして再検証してください。
```

[review-docs](review-docs.md) の結果だけでなく、ユーザーが提示した指摘・改訂案も検証できます。原文と検証したい指摘を指定してください。会話にある場合は再掲不要です。

手順と判断基準は [SKILL.md](../../skills/challenge-docs/SKILL.md)、表示用メタデータは [agents/openai.yaml](../../skills/challenge-docs/agents/openai.yaml) を参照してください。内容検討から構成・表現の確認まで一度に依頼する場合は [review-docs-workflow](review-docs-workflow.md) を使います。
