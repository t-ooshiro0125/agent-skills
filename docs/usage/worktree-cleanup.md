# worktree-cleanup

Git worktree の一覧、不要な worktree の削除、stale metadata の整理を行います。

## 依頼例

```text
$worktree-cleanup worktree の一覧と状態を確認してください。
```

```text
$worktree-cleanup /path/to/worktree を削除してください。ブランチは残してください。
```

一覧確認だけでは変更しません。削除前に未コミット変更、除外対象のローカルデータ、共有リンク、稼働プロセスを確認します。通常は Git の保護を維持して削除し、ブランチは別途明示された場合だけ削除します。stale metadata の整理は、dry-run の結果を確認し、整理が依頼されている場合に実行します。

エージェント向けの手順は [SKILL.md](../../skills/worktree-cleanup/SKILL.md)、表示用メタデータは [agents/openai.yaml](../../skills/worktree-cleanup/agents/openai.yaml) を参照してください。
