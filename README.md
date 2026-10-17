# agent-skills

複数のリポジトリで共通利用するエージェントスキルを管理するリポジトリです。配布用スキルは `skills/<skill-name>/` にあります。

## 配布用スキル一覧

依頼に `$<skill-name>` を付け、対象と期待する作業を指定します。

| スキル | 用途 | 使い方 |
| --- | --- | --- |
| [edit-prose](skills/edit-prose/SKILL.md) | 既存の日本語文章の構文確認・リライト | [ガイド](docs/usage/edit-prose.md) |
| [edit-rulebook](skills/edit-rulebook/SKILL.md) | 指示・ルール・運用文書の確認・リライト | [ガイド](docs/usage/edit-rulebook.md) |
| [review-docs](skills/review-docs/SKILL.md) | 文書の正しさ・不足・不要な記述を検討 | [ガイド](docs/usage/review-docs.md) |
| [challenge-docs](skills/challenge-docs/SKILL.md) | 文書レビューの指摘や追加・削除案への反証 | [ガイド](docs/usage/challenge-docs.md) |
| [review-docs-workflow](skills/review-docs-workflow/SKILL.md) | 文書の内容検討・反証・構成と表現の確認を順に実行 | [ガイド](docs/usage/review-docs-workflow.md) |
| [review](skills/review/SKILL.md) | 差分の挙動・バグ・テストの穴をレビュー | [ガイド](docs/usage/review.md) |
| [review-tests](skills/review-tests/SKILL.md) | 言語共通のテストの回帰検知力・独立性・保守性をレビュー | [ガイド](docs/usage/review-tests.md) |
| [review-tests-rspec](skills/review-tests-rspec/SKILL.md) | 共通基準に RSpec 固有の観点を加えてテストをレビュー | [ガイド](docs/usage/review-tests-rspec.md) |
| [implementation-challenge](skills/implementation-challenge/SKILL.md) | レビュー結果と議論の結論に対する反証 | [ガイド](docs/usage/implementation-challenge.md) |
| [review-refactor](skills/review-refactor/SKILL.md) | 挙動を維持するリファクタ候補の提示 | [ガイド](docs/usage/review-refactor.md) |
| [review-workflow](skills/review-workflow/SKILL.md) | レビュー・反証・リファクタ候補の確認を順に実行 | [ガイド](docs/usage/review-workflow.md) |
| [review-handoff](skills/review-handoff/SKILL.md) | レビュー後の判断を開発者向け対応指示に変換 | [ガイド](docs/usage/review-handoff.md) |
| [worktree-create](skills/worktree-create/SKILL.md) | Git worktree の作成・再利用 | [ガイド](docs/usage/worktree-create.md) |
| [worktree-link-local](skills/worktree-link-local/SKILL.md) | 既存 worktree 間のローカルファイル共有 | [ガイド](docs/usage/worktree-link-local.md) |
| [worktree-from-issue](skills/worktree-from-issue/SKILL.md) | Issue を起点とした worktree・共有環境の準備 | [ガイド](docs/usage/worktree-from-issue.md) |

## 導入方法

Codex CLI または IDE 拡張と、Git・Python 3 が利用できる環境を前提とします。登録スクリプトは macOS・Linux 向けです。スキルごとに必要な追加ツールは、各利用ガイドを参照してください。

1. まだ取得していない場合は、任意の保存先でリポジトリを取得して移動します。

   ```sh
   git clone https://github.com/t-ooshiro0125/agent-skills.git &&
       cd agent-skills
   ```

2. Codex CLI は取得したリポジトリのルートで起動し、IDE 拡張ではそのフォルダを開きます。

3. 次のように登録を依頼します。

   ```text
   $install-skills このリポジトリのスキルを登録してください。
   ```

Codex が登録先を確認し、配布用の `skills/` フォルダ全体へのリンクを作成します。取得したリポジトリを移動・削除するとリンクが切れるため、そのまま残してください。登録後は `/skills` または `$` で読み込みを確認してください。登録の詳細は、[install-skills の利用ガイド](docs/usage/install-skills.md)を参照してください。

## 更新方法

取得したリポジトリのルートで次を実行すると、既存のリンクを通じて登録済みスキルにも更新が反映されます。

```sh
git pull --ff-only
```

新しいスキルもリンク先に反映されるため、再登録は不要です。

## スキルの追加・更新

作成・更新の方針は [AGENTS.md](AGENTS.md) に従ってください。配布用スキルは `skills/<skill-name>/SKILL.md` に配置し、先頭の YAML メタデータに `name` と `description` を記載します。

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
│   └── install.py     # スキルの登録・Git 除外設定
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
- [スキルの呼び出し方法](https://learn.chatgpt.com/docs/build-skills#how-chatgpt-and-codex-use-skills)
