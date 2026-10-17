# worktree-from-issue

Issue の情報をもとに、worktree の作成・再利用と必要なローカルファイル共有をまとめて準備します。

## 依頼例

```text
$worktree-from-issue Issue #123 用の worktree を作成してください。共有対象はリポジトリの規約を参照してください。
```

GitHub 以外の Issue も、参照先や必要な情報を指定して依頼できます。Issue の編集・実装・push・PR 作成へ自動で進みません。

エージェント向けの手順は [SKILL.md](../../skills/worktree-from-issue/SKILL.md) を参照してください。作成・再利用は [worktree-create](worktree-create.md)、必要な共有は [worktree-link-local](worktree-link-local.md) が担当します。共有不要の場合はリンク設定を行いません。共有対象の記載方法は [共有の利用ガイド](worktree-link-local.md#共有対象の指定) を参照してください。

## 必要なツールと付属ファイル

worktree 操作には Git、リンク用の補助スクリプトには Python 3 と Git を使います。GitHub Issue の取得には認証済みの GitHub CLI（`gh`）が必要です。ほかの Issue 管理システムは、利用先の方法と利用可能な情報を使います。

表示用メタデータは [agents/openai.yaml](../../skills/worktree-from-issue/agents/openai.yaml) にあります。
