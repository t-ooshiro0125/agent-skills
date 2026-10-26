# agent-skills

複数のリポジトリで共通利用するエージェントスキルを管理するリポジトリです。配布用スキルは `skills/<skill-name>/` にあります。

## 配布用スキル一覧

Codex では `$<skill-name>`、Claude Code では `/<skill-name>` を依頼に付け、対象と期待する作業を指定します。

| スキル                                                               | 用途                                                             | 使い方                                           |
| -------------------------------------------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------ |
| [edit-prose](skills/edit-prose/SKILL.md)                             | 既存の日本語文章の構文確認・リライト                             | [ガイド](docs/usage/edit-prose.md)               |
| [edit-rulebook](skills/edit-rulebook/SKILL.md)                       | 指示・ルール・運用文書の確認・リライト                           | [ガイド](docs/usage/edit-rulebook.md)            |
| [review-docs](skills/review-docs/SKILL.md)                           | 文書の正しさ・不足・不要な記述と、情報の配置・移動・分割を検討   | [ガイド](docs/usage/review-docs.md)              |
| [challenge-docs](skills/challenge-docs/SKILL.md)                     | 文書レビューの指摘や追加・削除案への反証                         | [ガイド](docs/usage/challenge-docs.md)           |
| [review-docs-workflow](skills/review-docs-workflow/SKILL.md)         | 文書の内容検討・反証・構成と表現の確認を順に実行                 | [ガイド](docs/usage/review-docs-workflow.md)     |
| [revise-docs-workflow](skills/revise-docs-workflow/SKILL.md)         | 文書の内容確認・指摘の検証・リライト・修正後の確認を一続きに実行 | [ガイド](docs/usage/revise-docs-workflow.md)     |
| [review](skills/review/SKILL.md)                                     | 差分の挙動・バグ・テストの穴をレビュー                           | [ガイド](docs/usage/review.md)                   |
| [review-tests](skills/review-tests/SKILL.md)                         | 言語共通のテストの回帰検知力・独立性・保守性をレビュー           | [ガイド](docs/usage/review-tests.md)             |
| [review-tests-rspec](skills/review-tests-rspec/SKILL.md)             | 共通基準に RSpec 固有の観点を加えてテストをレビュー              | [ガイド](docs/usage/review-tests-rspec.md)       |
| [review-tests-jest](skills/review-tests-jest/SKILL.md)               | 共通基準に Jest 固有の観点を加えてテストをレビュー               | [ガイド](docs/usage/review-tests-jest.md)        |
| [write-pr-text](skills/write-pr-text/SKILL.md)                       | コミット済み差分と要件から PR タイトルと本文を作成               | [ガイド](docs/usage/write-pr-text.md)            |
| [implementation-challenge](skills/implementation-challenge/SKILL.md) | レビュー結果と議論の結論に対する反証                             | [ガイド](docs/usage/implementation-challenge.md) |
| [review-refactor](skills/review-refactor/SKILL.md)                   | 挙動を維持するリファクタ候補の提示                               | [ガイド](docs/usage/review-refactor.md)          |
| [review-workflow](skills/review-workflow/SKILL.md)                   | レビュー・反証・リファクタ候補の確認を順に実行                   | [ガイド](docs/usage/review-workflow.md)          |
| [review-handoff](skills/review-handoff/SKILL.md)                     | レビュー後の判断を開発者向け対応指示に変換                       | [ガイド](docs/usage/review-handoff.md)           |
| [worktree-create](skills/worktree-create/SKILL.md)                   | Git worktree の作成・再利用                                      | [ガイド](docs/usage/worktree-create.md)          |
| [worktree-link-local](skills/worktree-link-local/SKILL.md)           | 既存 worktree 間のローカルファイル共有                           | [ガイド](docs/usage/worktree-link-local.md)      |
| [worktree-from-issue](skills/worktree-from-issue/SKILL.md)           | Issue を起点とした worktree・共有環境の準備                      | [ガイド](docs/usage/worktree-from-issue.md)      |
| [worktree-start](skills/worktree-start/SKILL.md)                     | 利用先の手順に従って既存 worktree の開発環境を起動               | [ガイド](docs/usage/worktree-start.md)           |
| [worktree-cleanup](skills/worktree-cleanup/SKILL.md)                 | worktree の一覧・削除と stale metadata の整理                    | [ガイド](docs/usage/worktree-cleanup.md)         |

## 導入方法

Codex CLI・IDE 拡張または Claude Code と、Git・Python 3 が利用できる環境を前提とします。登録スクリプトは macOS・Linux 向けです。スキルごとに必要な追加ツールは、各利用ガイドを参照してください。

1. まだ取得していない場合は、任意の保存先でリポジトリを取得して移動します。

   ```sh
   git clone https://github.com/t-ooshiro0125/agent-skills.git &&
       cd agent-skills
   ```

2. 利用するエージェントの配置先を指定して登録します。次は Codex でユーザー全体に全スキルを登録する例です。エージェントを起動せず実行できます。

   ```sh
   python3 scripts/install.py "$HOME/.agents/skills"
   ```

既定では各スキルのディレクトリへの個別リンクを作成します。Claude Code では `~/.claude/skills`、プロジェクト単位では対象プロジェクトの `.agents/skills` または `.claude/skills` を指定します。Codex から `$install-skills` で登録を依頼することもできます。

Dev Containers などで登録先だけをマウントする場合は、実ファイルを配置するコピー方式を選べます。

```sh
python3 scripts/install.py "$HOME/.agents/skills" --mode copy
```

| 方式              | 内容の反映                           | 取得元への依存                                 |
| ----------------- | ------------------------------------ | ---------------------------------------------- |
| `symlink`（既定） | 取得元の変更がリンク経由で反映される | 利用時も取得元のパスが必要                     |
| `copy`            | 同じコマンドの再実行で同期する       | 配置したスキルは取得元が見えなくても利用できる |

コピーするのは Git 管理対象ファイルの作業ツリー上の内容です。新規ファイルは `git add` 後に対象となり、コミットは不要です。コピー先に独自変更があれば上書きせず停止します。リンク方式では、取得したリポジトリを移動・削除するとリンクや依存スキルの参照が切れるため、そのまま残してください。

4通りの配置先、選択登録と依存スキル、方式切替・削除・失敗時の手順は [install-skills の利用ガイド](docs/usage/install-skills.md)を参照してください。登録後は Codex では `/skills` または `$`、Claude Code では `/` の候補一覧で読み込みを確認してください。

## 更新方法

取得元リポジトリの更新は、登録・コピーの同期とは別の操作です。取得元の更新を行う場合は、そのリポジトリのルートで次を実行します。

```sh
git pull --ff-only
```

リンク方式では、既存のリンクを通じて内容に反映されます。コピー方式では、更新後に `--mode copy` の登録コマンドを再実行してください。手動編集や `git pull` だけではコピーへ自動同期されません。

新しいスキルを追加登録する場合も、インストーラーを再実行します。名前を省略すると全登録、登録先に続けてスキル名を指定すると選択登録になります。指定外の既存登録は削除しません。個人用の指示によってコピーの同期漏れを減らす運用例も [利用ガイド](docs/usage/install-skills.md#個人用の同期指示)にあります。

## スキルの追加・更新

作成・更新の方針は [AGENTS.md](AGENTS.md) に従ってください。開発時は [開発規約](docs/development.md)に従ってください。配布用スキルは `skills/<skill-name>/SKILL.md` に配置し、先頭の YAML メタデータに `name` と `description` を記載します。

変更に応じて、次の項目を確認します。

- スキルの追加・削除・改名や用途の変更に合わせて、この README のスキル一覧と、`docs/usage/` の該当する利用ガイドも更新します。
- 表示名や説明、呼び出し例を変更した場合は、付属する `agents/openai.yaml` との整合性を確認します。
- 補助スクリプトを変更した場合は、該当スキルの検証手順に従って付属テストを実行します。

```text
agent-skills/
├── .agents/skills/
│   └── install-skills/       # このリポジトリ専用（登録対象外）
│       └── SKILL.md
├── AGENTS.md
├── README.md
├── docs/usage/               # スキルごとの利用ガイド
├── scripts/
│   ├── install.py     # CLI・リンク登録・Git 除外設定
│   └── copy_install.py # コピーの同期・保護・復旧
├── tests/
│   └── test_install.py
└── skills/
    └── <skill-name>/
        ├── SKILL.md
        ├── agents/openai.yaml  # 表示用メタデータ（任意）
        ├── scripts/           # 補助スクリプト（任意）
        └── tests/             # スクリプトのテスト（任意）
```

## 参考資料

- [Codex のスキル配置先とシンボリックリンクの対応](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)
- [Claude Code のスキル配置先とシンボリックリンクの対応](https://code.claude.com/docs/en/skills#choose-where-skills-load)
- [スキルの呼び出し方法](https://learn.chatgpt.com/docs/build-skills#how-chatgpt-and-codex-use-skills)

## ライセンス

このリポジトリは [MIT ライセンス](LICENSE)で公開しています。
