"""配布用スキルを個別リンクで登録し、一括リンクから移行する。

使い方: python3 scripts/install.py <登録先ディレクトリ> [<skill-name> ...]
削除:   python3 scripts/install.py <登録先ディレクトリ> --remove <skill-name> ...
失敗前に完了した変更は残る。
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple
import os
import subprocess
import sys


BEGIN = "# BEGIN agent-skills"
END = "# END agent-skills"
LINK_NAME = "agent-skills"
RESERVED_NAMES = {LINK_NAME, "install-skills"}


@dataclass(frozen=True)
class InstallPlan:
    """変更対象と、既存項目を保護するための計画時点の判定。"""
    source_root: Path
    links: Tuple[Path, ...]
    bundle_link: Path
    migrate_bundle: bool = False
    remove: bool = False
    conflicts: Tuple[Path, ...] = ()
    stale_excludes: Tuple[Path, ...] = ()


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


# リンクの判定・登録計画・適用

def same_link(target, source):
    if not target.is_symlink():
        return False
    try:
        return target.resolve() == source.resolve()
    except (OSError, RuntimeError):
        return False


def occupied(target):
    return target.exists() or target.is_symlink()


def validate_names(names):
    for name in names:
        if not name or name in {".", ".."} or Path(name).name != name or name in RESERVED_NAMES:
            raise ValueError(f"配布用スキルの名前を指定してください: {name}")


def link_conflicts(target, source, repository):
    return ((repository is not None and is_tracked(repository, target))
            or (occupied(target) and not same_link(target, source)))


def plan_installation(source_root, destination, repository, skill_names=(), remove=False):
    """対象を事前検証する。計画だけではファイルを書き換えない。"""
    names = tuple(sorted(set(skill_names)))
    validate_names(names)
    available = tuple(sorted(path.name for path in source_root.iterdir()
                             if path.name not in RESERVED_NAMES and path.is_dir()
                             and (path / "SKILL.md").is_file()))
    bundle = destination / LINK_NAME
    own_bundle = same_link(bundle, source_root)
    if remove:
        if not names:
            raise ValueError("削除するスキルの名前を明示してください")
        if own_bundle:
            raise ValueError("一括リンクを個別リンクへ移行してから --remove を実行してください")
    else:
        unknown = set(names) - set(available)
        if unknown:
            raise ValueError("存在しない配布用スキル: " + ", ".join(sorted(unknown)))
        # 一括リンクで利用できていたスキルを暗黙に減らさない。
        if not names or own_bundle:
            names = available
        if not names:
            raise ValueError("登録できる配布用スキルがありません")

    links = tuple(destination / name for name in names)
    conflicts = tuple(target for target in links
                      if link_conflicts(target, source_root / target.name, repository))
    stale = list(conflicts)
    if own_bundle and repository and is_tracked(repository, bundle):
        conflicts += (bundle,)
        stale.append(bundle)
    elif not own_bundle:
        stale.append(bundle)
    return InstallPlan(source_root, links, bundle, own_bundle, remove, conflicts, tuple(stale))


def validate_plan(plan, repository):
    if plan.conflicts:
        raise ValueError("登録先に別の既存項目または Git 管理対象があります: "
                         + ", ".join(str(path) for path in plan.conflicts))
    if plan.migrate_bundle:
        if not same_link(plan.bundle_link, plan.source_root) or (
                repository and is_tracked(repository, plan.bundle_link)):
            raise ValueError(f"移行中に一括リンクの状態が変わりました: {plan.bundle_link}")
    elif same_link(plan.bundle_link, plan.source_root):
        raise ValueError(f"計画後に一括リンクが作成されました: {plan.bundle_link}")
    for target in plan.links:
        validate_target(plan, target, repository)


def validate_target(plan, target, repository):
    source = plan.source_root / target.name
    if link_conflicts(target, source, repository):
        raise ValueError(f"処理中に既存項目の状態が変わりました: {target}")
    if not plan.remove and not (source / "SKILL.md").is_file():
        raise ValueError(f"登録元のスキルが見つかりません: {source}")


def apply_link_migration(plan, repository):
    """適用前と各変更の直前に保護条件を再検証する。"""
    validate_plan(plan, repository)
    for target in plan.links:
        validate_target(plan, target, repository)
        if plan.remove:
            if target.is_symlink():
                target.unlink()
                print(f"削除: {target}")
            else:
                print(f"未登録: {target}")
        elif same_link(target, plan.source_root / target.name):
            print(f"登録済み: {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(plan.source_root / target.name, target_is_directory=True)
            print(f"登録: {target}")
    if plan.migrate_bundle:
        # 全リンクの作成を確認してから、一括リンクだけを削除する。
        validate_plan(plan, repository)
        if not all(same_link(target, plan.source_root / target.name) for target in plan.links):
            raise ValueError("個別リンクの作成を確認できないため、一括リンクを維持します")
        plan.bundle_link.unlink()
        print(f"一括リンクから移行: {plan.bundle_link}")


# 登録の進行とコマンドライン入口

def configure_git_excludes(repository, plan):
    patterns = []
    if not plan.conflicts and not plan.remove:
        patterns = [exclusion_pattern(target.relative_to(repository)) for target in plan.links]
    removals = [exclusion_pattern(target.relative_to(repository)) for target in plan.stale_excludes]
    exclude = update_exclude(repository, patterns, removals)
    for target in plan.links if patterns else ():
        result = git(repository, "check-ignore", "--no-index", "-q", "--",
                     target.relative_to(repository).as_posix())
        if result.returncode:
            raise ValueError(f"Git の除外が有効ではありません: {target}（.gitignore 等を確認してください）")
    print(f"Git 除外設定: {exclude}")


def install(destination, skill_names=(), remove=False):
    destination = Path(destination).expanduser().resolve()
    source_root = Path(__file__).resolve().parent.parent / "skills"
    if not source_root.is_dir():
        raise ValueError(f"登録元が見つかりません: {source_root}")
    if destination == source_root or source_root in destination.parents:
        raise ValueError("登録元の skills/ 内には登録できません")
    repository = repository_for(destination)
    plan = plan_installation(source_root, destination, repository, skill_names, remove)
    if repository:
        # マーカーと除外の有効性をリンク変更前に確認する。
        # 競合する項目の古い除外記録だけは、登録不可の場合も取り除く。
        configure_git_excludes(repository, plan)
    apply_link_migration(plan, repository)
    if repository:
        removed = plan.links if plan.remove else ((plan.bundle_link,) if plan.migrate_bundle else ())
        if removed:
            update_exclude(repository, [], [exclusion_pattern(target.relative_to(repository))
                                            for target in removed])


def main():
    parser = argparse.ArgumentParser(description="配布用スキルを個別リンクで登録する")
    parser.add_argument("destination", help="登録先ディレクトリ（必須）")
    parser.add_argument("skills", nargs="*", help="登録するスキル名（省略時は全スキル）")
    parser.add_argument("--remove", nargs="+", metavar="SKILL", help="明示したスキルのリンクだけを削除する")
    args = parser.parse_args()
    if not args.destination:
        parser.error("登録先ディレクトリを指定してください")
    if args.remove is not None and args.skills:
        parser.error("登録と削除は同時に指定できません")
    try:
        install(args.destination, args.remove if args.remove is not None else args.skills,
                remove=args.remove is not None)
    except (OSError, ValueError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
