# review-tests

言語・フレームワークを問わず、テストの回帰検知力、独立性、構成、保守性をレビューします。指摘の提示までを行い、テストの作成・修正は行いません。

## 依頼例

```text
$review-tests skills/worktree-link-local/tests/test_link_local.py をレビューしてください。
```

エージェント向けの判断基準と手順は [SKILL.md](../../skills/review-tests/SKILL.md)、表示用メタデータは [agents/openai.yaml](../../skills/review-tests/agents/openai.yaml) を参照してください。RSpec のレビューには、共通基準を引き継ぐ [review-tests-rspec](review-tests-rspec.md) を使います。

差分全体の挙動・バグの確認は [review](review.md) が対象です。テストの構成や保守性まで詳しく確認したい場合に、このスキルを指定します。実行するテストと linter は、利用先の規約と対象に合わせて選びます。
