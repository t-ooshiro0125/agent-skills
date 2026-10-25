"""Git の作業ツリーからスキルをコピーし、配置履歴に基づいて同期する。"""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import subprocess
import tempfile
from urllib.parse import unquote

from install import (LINK_NAME, RESERVED_NAMES, exclusion_pattern, git, is_tracked,
                     occupied, render_exclude, same_link, update_exclude, validate_names)


MANAGEMENT = ".agent-skills-copy"
OWNER = {"installer": "agent-skills-copy", "version": 1}
OWNER_FILE = ".owner.json"
RELATIVE_REFERENCE = re.compile(r"(?<![\w/])(?:\.\.?/)+[^\s`()< >\[\]\"'#]+")
MARKDOWN_LINK = re.compile(r"\]\(\s*(?:<([^>]+)>|([^\s)]+))")


def regular_path(path, root):
    """途中のディレクトリも含め、リンク経由で読み書きしない。"""
    for part in (root, *reversed(path.parents)):
        if part == root or root in part.parents:
            if part.is_symlink():
                raise ValueError(f"シンボリックリンクには対応していません: {part}")
    if path.is_symlink():
        raise ValueError(f"シンボリックリンクには対応していません: {path}")


def read_json(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"管理情報を読み取れません: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ValueError(f"管理情報が壊れています: {path}") from error


def atomic_json(path, value):
    """同じファイルシステムで用意してから置き換える。"""
    descriptor, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def fingerprint(data, mode):
    return {"sha256": hashlib.sha256(data).hexdigest(), "mode": mode}


def parent_directories(files):
    directories = set()
    for name in files:
        directories.update(str(p) for p in PurePosixPath(name).parents if str(p) != ".")
    return sorted(directories)


def content_record(source, files):
    return {"version": 1, "source": str(source), "directories": parent_directories(files),
            "files": {name: fingerprint(data, mode) for name, (data, mode) in files.items()}}


def validate_record(record, source, path):
    try:
        if record["version"] != 1 or record["source"] != str(source):
            raise ValueError("形式または取得元が一致しません")
        if not isinstance(record["files"], dict) or not isinstance(record["directories"], list):
            raise ValueError("ファイル一覧が不正です")
        for name in [*record["files"], *record["directories"]]:
            pure = PurePosixPath(name)
            if not name or pure.is_absolute() or ".." in pure.parts or str(pure) != name:
                raise ValueError("相対パスが不正です")
        for value in record["files"].values():
            if (not isinstance(value, dict) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
                    or type(value["mode"]) is not int or not 0 <= value["mode"] <= 0o777):
                raise ValueError("ファイル情報が不正です")
        if parent_directories(record["files"]) != record["directories"] or "SKILL.md" not in record["files"]:
            raise ValueError("ディレクトリ一覧または SKILL.md が不正です")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"管理情報の形式・取得元を確認してください: {path}（{error}）") from error
    return record


def describe(path):
    if path.is_symlink():
        return {"kind": "link", "link": os.readlink(path)}
    if not path.exists():
        return None
    if not path.is_dir():
        raise ValueError(f"既存項目はディレクトリではありません: {path}")
    files, directories = {}, []
    for parent, dirs, names in os.walk(path, followlinks=False):
        for name in dirs + names:
            item = Path(parent) / name
            mode = item.lstat().st_mode
            relative = item.relative_to(path).as_posix()
            if stat.S_ISDIR(mode):
                directories.append(relative)
            elif stat.S_ISREG(mode):
                files[relative] = fingerprint(item.read_bytes(), stat.S_IMODE(mode))
            else:
                raise ValueError(f"コピー先に通常ファイル以外の項目があります: {item}")
    return {"kind": "copy", "files": files, "directories": sorted(directories)}


def record_description(record):
    if record is None:
        return None
    return {"kind": "copy", "files": record["files"], "directories": record["directories"]}


def tracked_files(source):
    result = git(source.parent, "ls-files", "--stage", "-z", "--", source.name)
    if result.returncode:
        raise ValueError(f"コピー元の Git 管理対象を取得できません: {result.stderr.strip()}")
    by_skill = {}
    for entry in result.stdout.split("\0"):
        if not entry:
            continue
        attributes, name = entry.split("\t", 1)
        mode, _, stage = attributes.split()
        path = PurePosixPath(name).relative_to(source.name)
        if len(path.parts) < 2 or path.parts[0] in RESERVED_NAMES:
            continue
        by_skill.setdefault(path.parts[0], []).append((str(PurePosixPath(*path.parts[1:])), mode, stage))
    return by_skill


def source_content(source, name, entries):
    files = {}
    for relative, git_mode, stage in entries:
        path = source / name / relative
        regular_path(path, source)
        if stage != "0" or git_mode == "160000":
            raise ValueError(f"競合中のファイルまたはサブモジュールはコピーできません: {path}")
        if not path.exists():
            continue  # 作業ツリーで削除済みのファイルも同期する。
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise ValueError(f"通常ファイルではないコピー対象です: {path}")
        files[relative] = (path.read_bytes(), stat.S_IMODE(mode))
    if "SKILL.md" not in files:
        raise ValueError(f"Git 管理対象の SKILL.md が見つかりません: {source / name}")
    return files


def management_root(destination):
    root = destination / MANAGEMENT
    regular_path(root, destination)
    if occupied(root):
        if not root.is_dir() or read_json(root / OWNER_FILE) != OWNER:
            raise ValueError(f"別の既存項目または未対応の管理情報があります: {root}")
        lock = root / ".lock"
        if occupied(lock) and (lock.is_symlink() or not lock.is_file()):
            raise ValueError(f"管理情報のロックが通常ファイルではありません: {lock}")
        transactions = root / "transactions"
        if occupied(transactions) and (transactions.is_symlink() or not transactions.is_dir()):
            raise ValueError(f"復旧情報が通常のディレクトリではありません: {transactions}")
    return root


def managed_target(source, destination, name, repository):
    root = management_root(destination)
    target, manifest = destination / name, root / (name + ".json")
    if repository and (is_tracked(repository, target) or is_tracked(repository, root)):
        raise ValueError(f"Git 管理対象は変更できません: {target} / {root}")
    record = validate_record(read_json(manifest), source, manifest) if occupied(manifest) else None
    current = describe(target)
    if record is not None:
        if current != record_description(record):
            raise ValueError(f"コピー先に独自変更（追加・変更・削除）があります: {target}")
    elif current is not None and not same_link(target, source / name):
        raise ValueError(f"別の既存項目または管理情報のないコピーがあります: {target}")
    return current, record


def references(name, files):
    """Markdown に記載された相対パスから兄弟スキルへの参照を得る。"""
    found = set()
    for filename, (data, _) in files.items():
        if not filename.lower().endswith(".md"):
            continue
        text = data.decode("utf-8")
        paths = {m.group() for m in RELATIVE_REFERENCE.finditer(text)}
        paths.update(m.group(1) or m.group(2) for m in MARKDOWN_LINK.finditer(text))
        for value in paths:
            if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", value) or value.startswith(("/", "#")):
                continue
            value = unquote(value.split("#", 1)[0].split("?", 1)[0])
            relative = posixpath.normpath(posixpath.join(name, posixpath.dirname(filename), value))
            parts = PurePosixPath(relative).parts
            if relative == ".." or relative.startswith("../"):
                raise ValueError(f"配布対象外への相対参照があります: {name}/{filename}: {value}")
            if not parts:
                continue
            within_skill = str(PurePosixPath(*parts[1:]))
            if parts[0] != name:
                validate_names([parts[0]])
                found.add((parts[0], within_skill))
            elif (within_skill != "." and within_skill not in files
                  and within_skill not in parent_directories(files)):
                raise ValueError(f"参照先がコピー対象にありません（git add を確認してください）: {name}/{filename}: {value}")
    return found


def validate_dependencies(source, destination, contents, repository, available):
    queue = list(contents.items())
    visited, missing = set(), set()
    while queue:
        name, files = queue.pop()
        if name in visited:
            continue
        visited.add(name)
        for dependency, relative in sorted(references(name, files)):
            if dependency in contents:
                dependency_files = contents[dependency]
            else:
                target = destination / dependency
                try:
                    _, record = managed_target(source, destination, dependency, repository)
                except ValueError as error:
                    raise ValueError(f"依存スキル {dependency} を確認できません: {error}") from error
                if record is None:
                    missing.add(dependency)
                    if dependency not in available:
                        continue
                    dependency_files = source_content(source, dependency, available[dependency])
                else:
                    dependency_files = {p: ((target / p).read_bytes(), value["mode"])
                                        for p, value in record["files"].items()}
            if (relative != "." and relative not in dependency_files
                    and relative not in parent_directories(dependency_files)):
                raise ValueError(f"依存参照先がありません: {name} → {dependency}/{relative}")
            queue.append((dependency, dependency_files))
    if missing:
        raise ValueError("依存スキルが未登録です。--mode copy で追加指定してください: " + ", ".join(sorted(missing)))


def exclude_path(repository):
    result = git(repository, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude")
    if result.returncode:
        raise ValueError(result.stderr.strip())
    path = Path(result.stdout.rstrip("\n"))
    if path.is_symlink():
        raise ValueError(f"Git 除外ファイルがシンボリックリンクです: {path}")
    return path


def check_excludes(repository, destination, names, remove):
    """仮の Git ディレクトリで、実際の除外設定を書き換えずに確認する。"""
    if repository is None:
        return
    path = exclude_path(repository)
    original = path.read_bytes().decode("utf-8") if path.exists() else ""
    targets = [destination / name for name in names]
    metadata = destination / MANAGEMENT
    patterns = [exclusion_pattern(p.relative_to(repository)) for p in targets]
    updated = render_exclude(original, [exclusion_pattern(metadata.relative_to(repository))]
                             + ([] if remove else patterns), patterns if remove else [])
    with tempfile.TemporaryDirectory(prefix="agent-skills-exclude-") as temporary:
        root = Path(temporary)
        proposed = root / "exclude"
        proposed.write_text(updated, encoding="utf-8")
        command = ["git", "init", "--bare", "-q", str(root / "git")]
        initialized = subprocess.run(command, capture_output=True, text=True)
        if initialized.returncode:
            raise ValueError(f"Git 除外設定を事前確認できません: {initialized.stderr.strip()}")
        for target in ([] if remove else targets) + [metadata]:
            result = subprocess.run(
                ["git", "--git-dir=" + str(root / "git"), "--work-tree=" + str(repository),
                 "-c", "core.excludesFile=" + str(proposed), "check-ignore", "--no-index", "-q", "--",
                 target.relative_to(repository).as_posix()], cwd=repository, capture_output=True, text=True)
            if result.returncode:
                raise ValueError(f"Git の除外が有効ではありません: {target}（{result.stderr.strip()} .gitignore 等を確認してください）")


def update_copy_exclude(repository, destination, name, remove):
    if repository is None:
        return
    metadata = exclusion_pattern((destination / MANAGEMENT).relative_to(repository))
    pattern = exclusion_pattern((destination / name).relative_to(repository))
    path = update_exclude(repository, [metadata] + ([] if remove else [pattern]), [pattern] if remove else [])
    print(f"Git 除外設定: {path}")


@contextmanager
def copy_lock(root):
    created = not root.exists()
    if created:
        root.mkdir(parents=True)
        try:
            atomic_json(root / OWNER_FILE, OWNER)
        except BaseException:
            root.rmdir()
            raise
    lock = root / ".lock"
    regular_path(lock, root)
    with lock.open("a+b") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(f"同じ登録先で別のコピー処理が実行中です: {root}") from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def remove_item(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def cleanup_transaction(transaction):
    """退避内容を削除し終えるまで、再実行に必要な管理情報を残す。"""
    metadata = ("journal.json", "committed", "owner.json")
    for item in list(transaction.iterdir()):
        if item.name not in metadata:
            remove_item(item)
    for name in metadata:
        (transaction / name).unlink(missing_ok=True)
    transaction.rmdir()


def recover_transaction(source, destination, name, repository):
    root = management_root(destination)
    transaction = root / "transactions" / name
    regular_path(transaction, root)
    if not transaction.exists():
        return
    journal_path = transaction / "journal.json"
    if not journal_path.exists():
        # ステージング中の中断か、本体・管理情報の復元／確定後に片付けだけが残った状態。
        if not any(transaction.iterdir()):
            transaction.rmdir()
            return
        if read_json(transaction / "owner.json") != {**OWNER, "source": str(source), "name": name}:
            raise ValueError(f"復旧情報の所有元を確認できません: {transaction}")
        cleanup_transaction(transaction)
        return
    journal = read_json(journal_path)
    if (not isinstance(journal, dict) or journal.get("version") != 1
            or journal.get("source") != str(source) or journal.get("name") != name
            or not {"before_record", "after_record", "before"} <= journal.keys()):
        raise ValueError(f"復旧情報の形式・取得元が一致しません: {transaction}")
    before_record, after_record = journal["before_record"], journal["after_record"]
    for record in [before_record, after_record]:
        if record is not None:
            validate_record(record, source, journal_path)
    before, after = journal["before"], record_description(after_record)
    if before is not None and (not isinstance(before, dict) or before.get("kind") not in {"copy", "link"}):
        raise ValueError(f"復旧用の変更前情報が不正です: {journal_path}")
    if before_record is not None and before != record_description(before_record):
        raise ValueError(f"復旧用の変更前コピーと管理情報が一致しません: {journal_path}")
    target, manifest = destination / name, root / (name + ".json")
    if repository and (is_tracked(repository, target) or is_tracked(repository, root)):
        raise ValueError(f"復旧対象が Git 管理対象です: {target} / {root}")
    if (transaction / "committed").is_file():
        cleanup_transaction(transaction)
        return
    current_record = read_json(manifest) if occupied(manifest) else None
    if current_record not in (before_record, after_record):
        raise ValueError(f"復旧中に管理情報の変更を検出しました: {manifest}")
    backup = transaction / "before"
    current = describe(target)
    has_backup = occupied(backup)
    if has_backup:
        if describe(backup) != before or current not in (None, after):
            raise ValueError(f"復旧対象に独自変更があります。退避先を維持します: {transaction}")
    elif before is None:
        if current not in (None, after):
            raise ValueError(f"復旧対象に別の既存項目があります: {target}")
    elif current != before:
        raise ValueError(f"復旧用の退避ファイルがありません: {transaction}")
    if has_backup or before is None:
        # 新コピーはその場で削除せず退避する。部分削除で中断しても旧状態を復元できる。
        if current is not None:
            discarded = transaction / "discard"
            if occupied(discarded):
                raise ValueError(f"復旧用の退避先に既存項目があります: {discarded}")
            target.rename(discarded)
        if has_backup:
            backup.rename(target)
    if before_record is None:
        manifest.unlink(missing_ok=True)
    elif current_record != before_record:
        atomic_json(manifest, before_record)
    cleanup_transaction(transaction)
    print(f"復旧: {target}（実行前のコピー・管理情報へ復元）")


def apply_copy(source, destination, name, files, before, before_record, repository, remove):
    root = management_root(destination)
    transaction = root / "transactions" / name
    transaction.mkdir(parents=True)
    after_record = None if remove else content_record(source, files)
    target, manifest = destination / name, root / (name + ".json")
    try:
        atomic_json(transaction / "owner.json", {**OWNER, "source": str(source), "name": name})
        if not remove:
            stage = transaction / "after"
            stage.mkdir()
            for relative, (data, mode) in files.items():
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(mode)
        atomic_json(transaction / "journal.json", {"version": 1, "source": str(source), "name": name,
                    "before": before, "before_record": before_record, "after_record": after_record})
        if managed_target(source, destination, name, repository) != (before, before_record):
            raise ValueError(f"計画後に既存項目が変更されました: {target}")
        if before is not None:
            target.rename(transaction / "before")
        if not remove:
            (transaction / "after").rename(target)
            atomic_json(manifest, after_record)
        else:
            manifest.unlink(missing_ok=True)
        atomic_json(transaction / "committed", {"committed": True})
    except BaseException as error:
        try:
            recover_transaction(source, destination, name, repository)
        except (OSError, ValueError) as recovery_error:
            raise ValueError(f"{name} の処理に失敗: {error}。復旧待ち: {transaction}（{recovery_error}）。"
                             "状態を保護し、原因解消後に同じコマンドを再実行してください") from error
        raise ValueError(f"{name} の処理に失敗: {error}。このスキルは実行前へ復旧しました。"
                         "完了済みの別スキルは維持しています。原因解消後に再実行してください") from error
    cleanup_transaction(transaction)


def install_copy(source, destination, repository, skill_names=(), remove=False):
    names = tuple(sorted(set(skill_names)))
    validate_names(names)
    if any(name.startswith(".") for name in names):
        raise ValueError("隠しディレクトリは配布用スキルとして指定できません")
    if same_link(destination / LINK_NAME, source):
        raise ValueError("旧一括リンクがあります。先に方式未指定の通常登録で個別リンクへ移行してください")
    root = management_root(destination)
    if repository and is_tracked(repository, root):
        raise ValueError(f"管理情報が Git 管理対象です: {root}")
    available = {} if remove else tracked_files(source)
    available = {name: entries for name, entries in available.items()
                 if not name.startswith(".") and ((source / name / "SKILL.md").is_file()
                     or (source / name / "SKILL.md").is_symlink() or (source / name).is_symlink())
                 and any(p == "SKILL.md" for p, _, _ in entries)}
    if remove and not names:
        raise ValueError("削除するスキルの名前を明示してください")
    if not remove:
        unknown = set(names) - set(available)
        if unknown:
            raise ValueError("Git 管理対象の配布用スキルがありません: " + ", ".join(sorted(unknown)))
        names = names or tuple(sorted(available))
    # 前の実行で中断した対象だけを、保存した復旧情報に従って戻す。
    pending = [name for name in names if occupied(root / "transactions" / name)]
    if pending:
        with copy_lock(root):
            for name in pending:
                recover_transaction(source, destination, name, repository)
    contents = {} if remove else {name: source_content(source, name, available[name]) for name in names}
    states = {name: managed_target(source, destination, name, repository) for name in names}
    if remove:
        for name, (before, record) in states.items():
            if before is not None and record is None:
                raise ValueError(f"copy 方式で管理していない項目は削除できません: {destination / name}")
    else:
        validate_dependencies(source, destination, contents, repository, available)
    stale = []
    if not remove:
        for manifest in sorted(root.glob("*.json")):
            if manifest.name == OWNER_FILE:
                continue
            try:
                record = read_json(manifest)
            except ValueError:
                continue  # 指定外の管理情報を自動修復・削除しない。
            if isinstance(record, dict) and record.get("source") == str(source) and manifest.stem not in available:
                stale.append(manifest.stem)
        for name in stale:
            print(f"取得元から消えたスキルを維持: {destination / name}。"
                  f"不要なら --mode copy --remove {name} を指定してください")
    if not names:
        print("登録できる配布用スキルがありません（コピーの自動削除は行いません）")
        return
    check_excludes(repository, destination, names, remove)
    with copy_lock(root):
        # 計画後に別プロセスが変更していた場合も、全体を変更せず停止する。
        if same_link(destination / LINK_NAME, source):
            raise ValueError("計画後に旧一括リンクが作成されました")
        for name in names:
            if managed_target(source, destination, name, repository) != states[name]:
                raise ValueError(f"計画後に既存項目が変更されました: {destination / name}")
        if not remove:
            validate_dependencies(source, destination, contents, repository, available)
        for name in names:
            before, record = states[name]
            if not remove:
                if source_content(source, name, available[name]) != contents[name]:
                    raise ValueError(f"処理中に取得元が変更されました: {source / name}。再実行してください")
                update_copy_exclude(repository, destination, name, False)
            after_record = None if remove else content_record(source, contents[name])
            if (before is None and remove) or (record == after_record and record is not None):
                print(f"{'未登録' if remove else '同期済み'}: {destination / name}")
            else:
                apply_copy(source, destination, name, contents.get(name), before, record, repository, remove)
                print(f"{'削除' if remove else 'コピー'}: {destination / name}")
            if remove:
                update_copy_exclude(repository, destination, name, True)
