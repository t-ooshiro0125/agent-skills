# review-docs-workflow

文書の内容検討、指摘への反証、構成・表現の確認をまとめて行います。

[review-docs](review-docs.md) → [challenge-docs](challenge-docs.md) → 構成・表現の確認の順で進みます。最後の工程では、ルール・運用文書は [edit-rulebook](edit-rulebook.md)、日本語の一般文章は [edit-prose](edit-prose.md) を確認のみで使います。明示された文章の目的・利用場面・依頼条件を引き継ぎ、編集スキルの手順と最終確認まで適用します。

配置に関する指摘・根拠・未確定事項も引き継ぎ、移動・分割の必要性を反証します。文書の役割や移動先にユーザー判断が必要な場合もレビューを続け、その判断に依存する細かな表現案だけを保留し、判断事項と影響範囲を最後に示します。仕様や方針など、調査では決まらない判断だけを選択肢・推奨とともに提示します。

## 依頼例

最後に、有効な指摘、反証で撤回・修正した指摘、未確認事項を整理し、必要な修正案とともに提示します。文書は編集せず、修正・リライトは別途依頼してください。

```text
$review-docs-workflow skills/review-tests-rspec/SKILL.md の内容を検討し、その指摘を再検証したうえで、構成・表現も確認してください。
```

内容の確認だけなら [review-docs](review-docs.md)、指摘の再検証だけなら [challenge-docs](challenge-docs.md) を単独で使えます。構成・表現の確認やリライトだけなら、ルール・運用文書は [edit-rulebook](edit-rulebook.md)、日本語の一般文章は [edit-prose](edit-prose.md) を使います。

手順は [SKILL.md](../../skills/review-docs-workflow/SKILL.md)、表示用メタデータは [agents/openai.yaml](../../skills/review-docs-workflow/agents/openai.yaml) を参照してください。
