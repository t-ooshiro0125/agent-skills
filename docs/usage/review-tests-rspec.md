# review-tests-rspec

[review-tests](review-tests.md) の共通基準に RSpec 固有の観点を加え、Ruby・Rails のテストをレビューします。共通スキルを別途実行する必要はありません。テストの作成・修正は行いません。

## 依頼例

```text
$review-tests-rspec spec/services/ 配下の今回の変更をレビューしてください。
```

エージェント向けの追加観点は [SKILL.md](../../skills/review-tests-rspec/SKILL.md)、表示用メタデータは [agents/openai.yaml](../../skills/review-tests-rspec/agents/openai.yaml) を参照してください。

example の構成、`let`・`subject`・hook、shared example、モックなどを確認します。Rails 固有の確認は Rails を使う場合だけ適用します。expect の数や DB・ビュー描画に関する運用は一律に固定せず、対象リポジトリの規約に従います。
