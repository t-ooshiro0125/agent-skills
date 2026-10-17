# worktree-create

元の作業状態を残したまま、Git worktree を作成・再利用します。Issue の指定は不要です。

## 依頼例

```text
$worktree-create 基点 develop、ブランチ feature/example の worktree を、元フォルダの隣に作成してください。
```

既存ブランチの利用や、既存 worktree の再利用も依頼できます。新規ブランチの基点を省略した場合は、原則として fetch 時点のリモートデフォルトブランチを使います。明示した基点と利用先の規約を優先し、既存ブランチ・worktree の利用では基点を勝手に変更しません。

ブランチ名、リモート、作成先は、指定と対象リポジトリの規約・実際の状態から判断します。標準手順ではローカルのデフォルトブランチを更新しません。リモートや基点を特定できない場合、取得に失敗した場合は推測で作成せず、必要な情報を確認します。

命名規則がなければ、作業内容が分かる簡潔なブランチ名を選びます。同名ブランチがある場合は新規作成せず、別名にするか既存ブランチを使うかをユーザーの意図に沿って判断します。既存ブランチへの切り替えを自動では選びません。

エージェント向けの作成手順と保護条件は [SKILL.md](../../skills/worktree-create/SKILL.md) を参照してください。

## 作成後の準備

未コミット変更・未追跡ファイル・除外対象ファイルは、新しい worktree に自動で引き継がれません。必要なローカルファイルの共有には [worktree-link-local](worktree-link-local.md) を使います。Issue を起点に準備をまとめて依頼する場合は [worktree-from-issue](worktree-from-issue.md) を使います。

## 必要なツールと付属ファイル

Git が必要です。表示用メタデータは [agents/openai.yaml](../../skills/worktree-create/agents/openai.yaml) にあります。
