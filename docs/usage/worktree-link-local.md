# worktree-link-local

同じリポジトリの既存 worktree 間で、必要なローカル専用ファイルを共有します。

## 依頼例

```text
$worktree-link-local 共有元 /path/to/repo、共有先 /path/to/worktree に、規約に記載したローカルファイルを共有してください。
```

エージェント向けの手順は [SKILL.md](../../skills/worktree-link-local/SKILL.md) を参照してください。worktree を先に作成する場合は [worktree-create](worktree-create.md)、Issue を起点に両方を準備する場合は [worktree-from-issue](worktree-from-issue.md) を使います。

## 共有対象の指定

共有対象の文書への記載方法、個人用ファイルの除外、共有による影響と代替案は、[共有対象と Git 除外設定](../../skills/worktree-link-local/references/local-files.md#共有対象の指定)を参照してください。

## リンクと Git 除外設定

リンクそのものに一致するパターンと worktree 共通の除外設定については、[リンクと Git 除外設定](../../skills/worktree-link-local/references/local-files.md#リンクと-git-除外設定)を参照してください。

## 必要なツールと付属ファイル

補助スクリプトとテストには Python 3 と Git を使います。

[scripts/link_local.py](../../skills/worktree-link-local/scripts/link_local.py) は、既存 worktree 間のリンク設定だけを扱います。表示用メタデータは [agents/openai.yaml](../../skills/worktree-link-local/agents/openai.yaml) にあります。dry-run、既存項目の保護、Git 除外設定と失敗時の扱いは [スキルの実行手順](../../skills/worktree-link-local/SKILL.md#リンク設定の実行) を参照してください。

## 補助スクリプトを変更したときの検証

このリポジトリのルートで次のテストを実行します。

```sh
python3 skills/worktree-link-local/tests/test_link_local.py
```
