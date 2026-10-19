# review-tests-jest

[review-tests](review-tests.md) の共通基準に Jest 固有の観点を加え、テストをレビューします。共通スキルを別途実行する必要はありません。テストの作成・修正は行いません。

## 依頼例

```text
$review-tests-jest 今回変更した Jest テストをレビューしてください。
```

非同期処理の完了、mock と timer の後始末、テストの収集、実行環境、snapshot を確認します。コンポーネントテストでは、利用先の UI フレームワークに応じて描画と操作の待機も確認します。ケース名の言語と assertion 数は利用先の規約に従います。

エージェント向けの追加観点は [SKILL.md](../../skills/review-tests-jest/SKILL.md)、表示用メタデータは [agents/openai.yaml](../../skills/review-tests-jest/agents/openai.yaml) を参照してください。Jest の仕様確認には、利用先のバージョンに対応する [設定](https://jestjs.io/docs/configuration) と [Timer Mocks](https://jestjs.io/docs/timer-mocks) などの公式資料を使います。
