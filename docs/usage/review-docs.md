# review-docs

既存文書の内容について、正しさ・矛盾・不足・不要な記述と、文書の役割に応じた情報の配置を確認します。指摘と変更案の提示までを行い、ファイルは変更しません。移動・分割案では、対象箇所・移動先・理由・元の文書に残す概要や案内を示します。

## 依頼例

```text
$review-docs skills/review-tests-rspec/SKILL.md 全体を確認し、内容の誤りと追加・削除した方がよい点を挙げてください。
```

手順と判断基準は [SKILL.md](../../skills/review-docs/SKILL.md)、表示用メタデータは [agents/openai.yaml](../../skills/review-docs/agents/openai.yaml) を参照してください。

指摘への反証は [challenge-docs](challenge-docs.md)、内容検討・反証・構成と表現の確認をまとめて依頼する場合は [review-docs-workflow](review-docs-workflow.md) を使います。表現・構成だけの整理には、[edit-rulebook](edit-rulebook.md) または [edit-prose](edit-prose.md) を使います。
