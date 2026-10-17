# worktree-link-local

同じリポジトリの既存 worktree 間で、必要なローカル専用ファイルを共有します。

## 依頼例

```text
$worktree-link-local 共有元 /path/to/repo、共有先 /path/to/worktree に、規約に記載したローカルファイルを共有してください。
```

エージェント向けの手順は [SKILL.md](../../skills/worktree-link-local/SKILL.md) を参照してください。worktree を先に作成する場合は [worktree-create](worktree-create.md)、Issue を起点に両方を準備する場合は [worktree-from-issue](worktree-from-issue.md) を使います。

## 共有対象の指定

共有対象は依頼時に指定するほか、対象リポジトリの文書に記載できます。チーム共通の指定は `AGENTS.md` や開発規約、個人環境の指定はリポジトリ内の `AGENTS.override.md` に記載します。個人用の `AGENTS.override.md` は Git 共通ディレクトリの `info/exclude` に登録し、Git 管理対象にしません。

例えば、`AGENTS.override.md` に次のように記載します。共有するパスは対象リポジトリのルートを基準に示します。

```markdown
## worktree で共有するローカルファイル

パスはリポジトリのルートを基準とする。

- `<共有するパス>`：<用途・共有が必要な理由>
```

文書に指定があれば、依頼のたびに共有対象を列挙する必要はありません。スキルが指定を読み、補助スクリプトの `--link` に渡します。補助スクリプト自体は文書を読み取りません。指定がない場合も除外対象を一括共有せず、必要性と影響を確認して対象を選びます。これらは共有対象の記載方法の案内であり、worktree の準備依頼だけで、規約や個人用の文書を自動作成・変更することはありません。

補助スクリプトがリンクできるのは、共有元で Git の除外対象になっているパスだけです。指定があっても、Git 管理対象や共有先の既存ファイルを上書きしない保護条件は維持します。ユーザー共通のスキル保存先はリンク不要です。

リンク経由の編集は共有元にも反映され、共有元の移動・削除でリンクが切れるため、元フォルダはそのまま残します。リンクによる相互影響が不適切な場合や、除外されていない未追跡ファイルには、コピーや worktree ごとの個別管理を代替案として提示します。コピーは自動では実行せず、明示的に依頼された場合だけ別の操作として扱います。その場合も既存ファイルを上書きしません。

## リンクと Git 除外設定

補助スクリプトは、Git 共通ディレクトリ内の `info/exclude` に、リンクそのものに一致する除外パターンを追加します。末尾が `/` のディレクトリ除外パターンだけでは、シンボリックリンク自体を除外できないためです。この設定は同じリポジトリのすべての worktree に適用されます。

## 必要なツールと付属ファイル

補助スクリプトとテストには Python 3 と Git を使います。

[scripts/link_local.py](../../skills/worktree-link-local/scripts/link_local.py) は、既存 worktree 間のリンク設定だけを扱います。表示用メタデータは [agents/openai.yaml](../../skills/worktree-link-local/agents/openai.yaml) にあります。dry-run、既存項目の保護、Git 除外設定と失敗時の扱いは [スキルの実行手順](../../skills/worktree-link-local/SKILL.md#リンク設定の実行) を参照してください。

## 補助スクリプトを変更したときの検証

このリポジトリのルートで次のテストを実行します。

```sh
python3 skills/worktree-link-local/tests/test_link_local.py
```
