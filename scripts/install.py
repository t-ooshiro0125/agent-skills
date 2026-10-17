"""配布用スキルを一括リンクで登録し、旧個別リンクから移行する。

Git リポジトリ内では、登録するリンクをローカル除外設定に追加し、
古い除外記録を整理する。既存項目は保護し、移行可能な旧リンクだけを削除する。

使い方: python3 scripts/install.py <登録先ディレクトリ>
失敗前に完了した変更は残る。
"""

from pathlib import Path
from dataclasses import dataclass
from typing import Tuple
import os
import subprocess
import sys


BEGIN = "# BEGIN agent-skills"
END = "# END agent-skills"
LINK_NAME = "agent-skills"


@dataclass(frozen=True)
class InstallPlan:
    """計画時点の登録可否と、リンク移行・除外記録の整理対象。

    can_register は登録済みの場合も True。適用時には状態を再検証する。
    登録不可でも exclude_paths_to_remove の除外記録を掃除してから停止する。
    このフィールドは除外記録を指定するもので、既存項目の削除は指示しない。
    """
    source_root: Path
    bundle_link: Path
    can_register: bool
    legacy_links_to_remove: Tuple[Path, ...] = ()
    exclude_paths_to_remove: Tuple[Path, ...] = ()
    already_registered: bool = False
    protected_legacy_links: Tuple[Path, ...] = ()


# Git リポジトリの照会

def git(directory, *args):
    options = ["--literal-pathspecs"] if args[0] == "ls-files" else []
    return subprocess.run(
        ["git", *options, "-C", str(directory), *args],
        capture_output=True, text=True, env=dict(os.environ, LC_ALL="C"),
    )


def repository_for(destination):
    ancestor = destination
    while not ancestor.exists():
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise ValueError(f"登録先の親がディレクトリではありません: {ancestor}")
    result = git(ancestor, "rev-parse", "--show-toplevel")
    if result.returncode:
        if "not a git repository" in result.stderr:
            return None
        raise ValueError(result.stderr.strip())
    return Path(result.stdout.rstrip("\n")).resolve()


def is_tracked(repository, target):
    result = git(repository, "ls-files", "--", target.relative_to(repository).as_posix())
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return bool(result.stdout)


# Git 除外設定の編集と適用

def exclusion_pattern(relative_path):
    value = relative_path.as_posix()
    if "\n" in value or "\r" in value:
        raise ValueError("改行を含む登録先には対応していません")
    return "/" + "".join("\\" + char if char in "\\ *?[]#!" else char for char in value)


def find_managed_block(lines):
    """専用範囲のマーカーを検証し、開始・終了位置を返す。"""
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == END]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError("専用範囲の開始・終了マーカーが不正です")
    return starts[0], ends[0]


def append_managed_block(original, patterns):
    if not patterns:
        return original
    separator = "" if not original or original.endswith("\n") else "\n"
    return original + separator + "\n" + BEGIN + "\n" + "\n".join(patterns) + "\n" + END + "\n"


def update_managed_block(original, lines, bounds, patterns, removals):
    start, end = bounds
    existing = [line.rstrip("\r\n") for line in lines[start + 1:end]]
    retained = [pattern for pattern in existing if pattern not in removals]
    additions = [pattern for pattern in patterns if pattern not in retained]
    if retained + additions == existing:
        return original
    block = BEGIN + "\n" + "".join(pattern + "\n" for pattern in retained + additions) + END + "\n"
    return "".join(lines[:start]) + block + "".join(lines[end + 1:])


def render_exclude(original, patterns, removals=()):
    """専用範囲を編集し、それ以外のテキストを保持する。"""
    lines = original.splitlines(keepends=True)
    bounds = find_managed_block(lines)
    if bounds is None:
        return append_managed_block(original, patterns)
    return update_managed_block(original, lines, bounds, patterns, removals)


def update_exclude(repository, patterns, removals=()):
    result = git(repository, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude")
    if result.returncode:
        raise ValueError(result.stderr.strip())
    path = Path(result.stdout.rstrip("\n"))
    original = ""
    if path.exists():
        with path.open("r", newline="") as file:
            original = file.read()
    try:
        updated = render_exclude(original, patterns, removals)
    except ValueError as error:
        raise ValueError(f"{error}: {path}") from error
    if updated != original:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as file:
            file.write(updated)
    return path


def configure_git_excludes(repository, plan):
    relative_link = plan.bundle_link.relative_to(repository)
    patterns = [exclusion_pattern(relative_link)] if plan.can_register else []
    removal_patterns = [exclusion_pattern(target.relative_to(repository)) for target in plan.exclude_paths_to_remove]
    exclude = update_exclude(repository, patterns, removal_patterns)
    if plan.can_register:
        result = git(repository, "check-ignore", "--no-index", "-q", "--", relative_link.as_posix())
        if result.returncode:
            raise ValueError(f"Git の除外が有効ではありません: {plan.bundle_link}（.gitignore 等を確認してください）")
    print(f"Git 除外設定: {exclude}")


# リンクの判定・登録計画・適用

def same_link(target, source):
    if not target.is_symlink():
        return False
    try:
        return target.resolve() == source.resolve()
    except (OSError, RuntimeError):
        return False


def plan_legacy_migration(source_root, destination, repository):
    """移行可能な旧リンクと、古い除外記録の削除対象を調べる。"""
    legacy_links_to_remove = []
    protected_legacy_links = []
    old_targets = [destination / source.name for source in sorted(source_root.iterdir())
                   if (source / "SKILL.md").is_file() and source.name != LINK_NAME]
    for old_target in old_targets:
        source = source_root / old_target.name
        if same_link(old_target, source):
            if repository and is_tracked(repository, old_target):
                protected_legacy_links.append(old_target)
            else:
                legacy_links_to_remove.append(old_target)
    return tuple(legacy_links_to_remove), tuple(old_targets), tuple(protected_legacy_links)


def plan_installation(source_root, destination, repository):
    """登録可否、移行する旧リンク、除外記録の削除対象を決める。"""
    target = destination / LINK_NAME
    if repository and is_tracked(repository, target):
        return InstallPlan(source_root=source_root, bundle_link=target, can_register=False, exclude_paths_to_remove=(target,))
    already_registered = same_link(target, source_root)
    if not already_registered and (target.exists() or target.is_symlink()):
        return InstallPlan(source_root=source_root, bundle_link=target, can_register=False, exclude_paths_to_remove=(target,))

    legacy_links_to_remove, exclude_paths_to_remove, protected_legacy_links = plan_legacy_migration(
        source_root, destination, repository,
    )
    return InstallPlan(
        source_root=source_root,
        bundle_link=target,
        can_register=True,
        legacy_links_to_remove=legacy_links_to_remove,
        exclude_paths_to_remove=exclude_paths_to_remove,
        already_registered=already_registered,
        protected_legacy_links=protected_legacy_links,
    )


def apply_link_migration(plan, repository):
    """一括リンクを作成してから、検証済みの旧個別リンクを削除する。"""
    if not plan.can_register:
        raise ValueError(f"登録先に別の既存項目または Git 管理対象があります: {plan.bundle_link}")
    if repository and is_tracked(repository, plan.bundle_link):
        raise ValueError(f"登録中に Git 管理対象になりました: {plan.bundle_link}")
    plan.bundle_link.parent.mkdir(parents=True, exist_ok=True)
    if not same_link(plan.bundle_link, plan.source_root):
        if plan.bundle_link.exists() or plan.bundle_link.is_symlink():
            raise ValueError(f"登録中に既存項目の状態が変わりました: {plan.bundle_link}")
        plan.bundle_link.symlink_to(plan.source_root, target_is_directory=True)
        print(f"登録: {plan.bundle_link}")
    for target in plan.legacy_links_to_remove:
        source = plan.source_root / target.name
        if not same_link(target, source) or (repository and is_tracked(repository, target)):
            raise ValueError(f"移行中に既存リンクの状態が変わりました: {target}")
        target.unlink()
        print(f"個別リンクから移行: {target}")


# 登録の進行とコマンドライン入口

def report_installation_plan(plan):
    if plan.already_registered:
        print(f"登録済み: {plan.bundle_link}")
    for target in plan.protected_legacy_links:
        print(f"移行対象外（Git 管理対象）: {target}")


def install(destination):
    destination = Path(destination).expanduser().resolve()
    repository = repository_for(destination)
    source_root = Path(__file__).resolve().parent.parent / "skills"
    if not source_root.is_dir():
        raise ValueError(f"登録元が見つかりません: {source_root}")
    if destination == source_root or source_root in destination.parents:
        raise ValueError("登録元の skills/ 内には登録できません")
    plan = plan_installation(source_root, destination, repository)
    report_installation_plan(plan)
    if repository:
        # リンクを変更する前に除外の有効性を確認する。
        # 登録不可の場合も、競合する項目の古い除外記録を削除する。
        configure_git_excludes(repository, plan)
    apply_link_migration(plan, repository)


def main():
    if len(sys.argv) != 2 or not sys.argv[1]:
        print("使い方: python3 scripts/install.py <登録先ディレクトリ>", file=sys.stderr)
        return 2
    try:
        install(sys.argv[1])
    except (OSError, ValueError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
