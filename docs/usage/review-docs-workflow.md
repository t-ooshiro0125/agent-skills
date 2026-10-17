# review-docs-workflow

文書の内容検討、指摘への反証、構成・表現の確認をまとめて行います。指摘と必要な修正案の提示までで終了し、文書は編集しません。

## 依頼例

```text
$review-docs-workflow skills/review-tests-rspec/SKILL.md の内容を検討・反証し、構成・表現も含めてレビューしてください。
```

[review-docs](review-docs.md) → [challenge-docs](challenge-docs.md) → 構成・表現の確認の順で進みます。最後の工程では、ルール・運用文書は [edit-rulebook](edit-rulebook.md)、日本語の一般文章は [edit-prose](edit-prose.md) を確認のみで使います。

最後に有効な指摘、反証で撤回・修正した指摘、未確認事項を整理します。仕様や方針など、調査では決まらない判断だけを選択肢・推奨とともに提示します。修正・リライトは別途依頼してください。

確認だけなら [review-docs](review-docs.md)、指摘の再検証だけなら [challenge-docs](challenge-docs.md) を単独で使えます。

手順は [SKILL.md](../../skills/review-docs-workflow/SKILL.md)、表示用メタデータは [agents/openai.yaml](../../skills/review-docs-workflow/agents/openai.yaml) を参照してください。
