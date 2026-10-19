# worktree-start

既存 worktree の開発環境を、利用先に定義された手順で起動します。worktree の作成や削除は行いません。

## 依頼例

```text
$worktree-start /path/to/worktree の環境を、プロジェクトの起動手順に従って起動してください。
```

設定、依存関係、接続先と並行稼働の条件を確認してから起動します。起動に必要な依存関係は利用先の lockfile を保持する方法で導入します。OS ツールの導入や migration は自動では実行しません。

プロジェクト固有の環境判定、依存関係のコマンド、起動スクリプトと復旧方法は、利用先の規約や専用スキルに記載できます。

エージェント向けの手順は [SKILL.md](../../skills/worktree-start/SKILL.md)、表示用メタデータは [agents/openai.yaml](../../skills/worktree-start/agents/openai.yaml) を参照してください。
