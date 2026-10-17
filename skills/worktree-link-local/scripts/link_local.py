#!/usr/bin/env python3
"""Link explicitly selected ignored paths between worktrees; dry-run by default."""

import argparse
import os
from pathlib import Path
import subprocess
import sys


def git(root, *args, check=True, input=None):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=check,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, input=input,
    )


def repository(path):
    root = Path(path).expanduser().resolve(strict=True)
    actual = Path(git(root, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if root != actual:
        raise ValueError(f"worktree のルートを指定してください: {root}")
    common = Path(git(root, "rev-parse", "--git-common-dir").stdout.strip())
    return root, (root / common).resolve()


def relative_path(value):
    name = value.rstrip("/")
    parts = name.split("/")
    if (not name or any(p in ("", ".", "..", ".git") for p in parts)
            or any(c in name for c in "*?[]\\\n\r\0")
            or any(p != p.strip() for p in parts)):
        raise ValueError(f"ワイルドカードのない相対パスを指定してください: {value!r}")
    return name


def check_parents(root, destination):
    for parent in destination.parents:
        if parent == root:
            break
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError(f"リンク先の親が通常のディレクトリではありません: {parent}")


def check_destination(root, src, dst):
    check_parents(root, dst)
    same = dst.is_symlink() and dst.resolve() == src.resolve()
    if os.path.lexists(dst) and not same:
        raise ValueError(f"既存のファイル・リンクは上書きしません: {dst}")
    return same


def exclusion_patterns(target, names, previous):
    """Check Git's effective rules before adding lower-priority info/exclude rules."""
    patterns = []
    existing = previous.splitlines()
    for name in names:
        result = git(target, "check-ignore", "-v", "-z", "--stdin",
                     input=name + "\0", check=False)
        if result.returncode not in (0, 1):
            raise ValueError(result.stderr.strip() or f"除外設定を確認できません: {target / name}")
        ignored = False
        if result.stdout:
            origin, line, pattern, _path, _end = result.stdout.split("\0")
            ignored = not pattern.startswith("!")
            # A negation in a worktree .gitignore outranks info/exclude.
            # Negations in info/exclude or core.excludesFile can be overridden.
            rule_file = (target / origin).resolve()
            local_rules = {(target / ".gitignore").resolve()}
            for parent in Path(name).parents:
                local_rules.add((target / parent / ".gitignore").resolve())
            if not ignored and rule_file in local_rules:
                raise ValueError(
                    f"共有先の除外解除と競合しています: {target / name} "
                    f"({origin}:{line}: {pattern})。info/exclude では上書きできません"
                )
        exact = "/" + name
        if exact not in existing or not ignored:
            patterns.append(exact)
    return patterns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--link", action="append", required=True, metavar="RELATIVE_PATH")
    parser.add_argument("--apply", action="store_true", help="確認したリンクと除外設定を適用")
    args = parser.parse_args()
    source, common = repository(args.source)
    target, target_common = repository(args.target)
    if common != target_common:
        raise ValueError("同じ Git リポジトリの worktree 間でのみ共有できます")
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("共有元と共有先は、互いに内包しない別の worktree にしてください")

    names = list(dict.fromkeys(relative_path(value) for value in args.link))
    for name in names:
        if any(name.startswith(other + "/") for other in names if name != other):
            raise ValueError(f"親子のリンク対象が重複しています: {name}")
    tracked = {
        root: git(root, "ls-files", "-z").stdout.split("\0")
        for root in (source, target)
    }
    plan = []
    for name in names:
        for root, files in tracked.items():
            if any(p == name or p.startswith(name + "/")
                   or name.startswith(p + "/") for p in files if p):
                raise ValueError(f"Git 管理対象を含むパスは共有できません: {root / name}")
        src, dst = source / name, target / name
        if not src.exists() or not (src.is_file() or src.is_dir()):
            raise ValueError(f"共有元がないか、通常のファイル・ディレクトリではありません: {src}")
        ignored = git(source, "check-ignore", "-q", "--", name, check=False)
        if ignored.returncode == 1:
            raise ValueError(f"ignored でない未追跡ファイルはコピーを検討してください: {src}")
        if ignored.returncode != 0:
            raise ValueError(ignored.stderr.strip())
        same = check_destination(target, src, dst)
        plan.append((name, src, dst, same))

    exclude = common / "info" / "exclude"
    previous = exclude.read_text() if exclude.exists() else ""
    patterns = exclusion_patterns(target, names, previous)
    for name, src, dst, same in plan:
        print(f"{'KEEP' if same else 'LINK'} {dst} -> {os.path.relpath(src, dst.parent)}")
    for pattern in patterns:
        print(f"EXCLUDE {exclude}: {pattern}")
    if not args.apply:
        print("dry-run: 変更していません。適用には --apply を付けてください。")
        return

    # Recheck every destination before the first write, including KEEP entries.
    plan = [(name, src, dst, check_destination(target, src, dst))
            for name, src, dst, _same in plan]
    if patterns:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        # Append rather than replacing shared local exclusion settings.
        with exclude.open("a") as stream:
            if previous and not previous.endswith("\n"):
                stream.write("\n")
            stream.write("\n".join(patterns) + "\n")
    for name, src, dst, same in plan:
        if not same:
            check_parents(target, dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.symlink_to(os.path.relpath(src, dst.parent), target_is_directory=src.is_dir())
        if not dst.is_symlink() or dst.resolve() != src.resolve():
            raise ValueError(f"リンクの参照先を確認できません: {dst}")
        ignored = git(target, "check-ignore", "-q", "--", name, check=False)
        if ignored.returncode != 0:
            raise ValueError(
                f"作成後のリンクを Git の除外対象として確認できません: {dst}。"
                "適用中に除外設定が変わっていないか確認してください"
                + (f" ({ignored.stderr.strip()})" if ignored.stderr.strip() else "")
            )
    print("リンク設定を適用しました。")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        detail = (exc.stderr.strip() or str(exc)) if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print(f"エラー: {detail}", file=sys.stderr)
        sys.exit(1)
