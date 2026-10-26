"""CLI と実ファイルで copy モードの保護条件・同期・復旧を検証する。"""
from contextlib import redirect_stdout
from io import StringIO
import errno
import importlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from git_test_environment import isolate_git_environment


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / 'scripts'))
copy_install = importlib.import_module('copy_install')


class CopyInstallTest(unittest.TestCase):
    def setUp(self):
        self.env = isolate_git_environment(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'source'
        (self.source / 'scripts').mkdir(parents=True)
        for script in (REPOSITORY / 'scripts').glob('*.py'):
            shutil.copyfile(script, self.source / 'scripts' / script.name)
        for name in ['alpha', 'beta', 'gamma']:
            self.put(name + '/SKILL.md', name)
        local = self.source / '.agents/skills/install-skills'
        local.mkdir(parents=True)
        (local / 'SKILL.md').write_text('repository-only')
        self.git('init', directory=self.source)
        self.git('add', '.', directory=self.source)
        self.repo = self.root / 'project'
        self.repo.mkdir()
        self.git('init')
        self.target = self.repo / '.agents/skills'
        self.exclude = self.repo / '.git/info/exclude'

    def git(self, *args, directory=None):
        return subprocess.run(['git', '-C', str(directory or self.repo), *args],
                              env=self.env, check=True, capture_output=True, text=True).stdout.strip()

    def put(self, name, text):
        path = self.source / 'skills' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def track(self):
        self.git('add', '-A', 'skills', directory=self.source)

    def run_install(self, names=(), mode='copy', remove=False, success=True, target=None):
        args = [sys.executable, str(self.source / 'scripts/install.py'), str(target or self.target)]
        if mode is not None:
            args += ['--mode', mode]
        if remove:
            args += ['--remove', *names]
        else:
            args += list(names)
        result = subprocess.run(args, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0 if success else 1, result.stdout + result.stderr)
        return result

    def tree(self, root=None):
        root = root or self.target
        if not root.exists():
            return None
        result = {}
        for parent, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                path = Path(parent) / name
                result[str(path.relative_to(root))] = (
                    ('link', os.readlink(path)) if path.is_symlink() else
                    ('dir',) if path.is_dir() else ('file', path.read_bytes(), path.stat().st_mode & 0o777))
        return result

    def manifest(self, name):
        return self.target / copy_install.MANAGEMENT / (name + '.json')

    def assert_preflight_failure(self, names=(), remove=False, message=None):
        before = self.tree(), self.exclude.read_bytes()
        result = self.run_install(names, remove=remove, success=False)
        self.assertEqual((self.tree(), self.exclude.read_bytes()), before)
        if message:
            self.assertIn(message, result.stderr)
        return result

    def direct(self, names=(), remove=False):
        with redirect_stdout(StringIO()):
            copy_install.install_copy(self.source / 'skills', self.target, self.repo, names, remove)

    def interrupt(self, name, remove=False, after_commit=False):
        script = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import copy_install as c
original = c.atomic_json
def interrupt(path, value):
    if (not AFTER and path.name == NAME + '.json') or (AFTER and path.name == 'committed' and path.parent.name == NAME):
        if AFTER:
            original(path, value)
        os._exit(42)
    return original(path, value)
c.atomic_json = interrupt
c.install_copy(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), ('alpha','beta'), REMOVE)
'''.replace('AFTER', repr(after_commit)).replace('NAME', repr(name)).replace('REMOVE', repr(remove))
        result = subprocess.run([sys.executable, '-c', script, str(self.source / 'scripts'),
                                 str(self.source / 'skills'), str(self.target), str(self.repo)],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)

    def assert_copied_references_readable(self):
        for skill in self.target.iterdir():
            if skill.name.startswith('.'):
                continue
            self.assertFalse(skill.is_symlink())
            files = {str(p.relative_to(skill)): (p.read_bytes(), p.stat().st_mode & 0o777)
                     for p in skill.rglob('*') if p.is_file()}
            for dependency, relative in copy_install.references(skill.name, files):
                target = self.target / dependency / relative
                self.assertTrue(target.is_file() or target.is_dir(), str(target))

    def test_inherited_git_environment_cannot_change_an_external_repository(self):
        for context in ['index', 'directory']:
            with self.subTest(context=context):
                outside = self.root / ('outside-' + context)
                outside.mkdir()
                self.git('init', directory=outside)
                (outside / 'sentinel.txt').write_text('outside staged file')
                self.git('add', 'sentinel.txt', directory=outside)
                before = self.tree(outside)
                environment = dict(self.env)
                if context == 'index':
                    environment['GIT_INDEX_FILE'] = str(outside / '.git/index')
                else:
                    environment.update(GIT_DIR=str(outside / '.git'), GIT_WORK_TREE=str(outside))
                result = subprocess.run([
                    sys.executable, str(REPOSITORY / 'tests/test_install.py'),
                    'InstallSkillsTest.test_registers_individual_links_and_preserves_user_rules',
                    'CopyInstallTest.test_copies_tracked_working_files_and_preserves_executable_mode',
                    'CopyInstallTest.test_failure_restores_current_skill_but_keeps_completed_skills',
                ], env=environment, capture_output=True, text=True)
                self.assertEqual(self.tree(outside), before, result.stdout + result.stderr)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_copies_tracked_working_files_and_preserves_executable_mode(self):
        support = self.put('alpha/scripts/run.py', 'print("support")')
        support.chmod(0o755)
        binary = self.source / 'skills/alpha/data.bin'
        binary.write_bytes(b'\x00\xff\x01')
        self.track()
        self.put('alpha/SKILL.md', 'unstaged content')
        self.put('alpha/__pycache__/cache.pyc', 'untracked')
        self.run_install(['alpha'])
        self.assertFalse((self.target / 'alpha').is_symlink())
        self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'unstaged content')
        self.assertEqual((self.target / 'alpha/scripts/run.py').stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.target / 'alpha/data.bin').read_bytes(), binary.read_bytes())
        self.assertFalse((self.target / 'alpha/__pycache__').exists())
        self.assertFalse((self.target / 'install-skills').exists())
        record = json.loads(self.manifest('alpha').read_text())
        self.assertEqual(record['source'], str(self.source / 'skills'))
        self.assertEqual(record['version'], 1)

    def test_new_untracked_skill_requires_git_add_without_commit(self):
        self.put('delta/SKILL.md', 'new skill')
        self.assert_preflight_failure(['delta'])
        self.run_install()
        self.assertFalse((self.target / 'delta').exists())
        self.track()
        self.run_install(['delta'])
        self.assertEqual((self.target / 'delta/SKILL.md').read_text(), 'new skill')

    def test_rerun_is_unchanged_and_selected_sync_preserves_other_registrations(self):
        self.run_install()
        before = self.tree(), self.exclude.read_bytes()
        inode = (self.target / 'alpha/SKILL.md').stat().st_ino
        self.run_install(['alpha'])
        self.assertEqual((self.tree(), self.exclude.read_bytes()), before)
        self.assertEqual((self.target / 'alpha/SKILL.md').stat().st_ino, inode)
        self.put('alpha/SKILL.md', 'changed')
        self.put('beta/SKILL.md', 'not selected')
        self.run_install(['alpha'])
        self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'changed')
        self.assertEqual((self.target / 'beta/SKILL.md').read_text(), 'beta')

    def test_sync_removes_and_renames_files_and_directories(self):
        self.put('alpha/old/reference.txt', 'old')
        self.track()
        self.run_install(['alpha'])
        (self.source / 'skills/alpha/old/reference.txt').unlink()
        self.put('alpha/new/renamed.txt', 'new')
        self.track()
        self.run_install(['alpha'])
        self.assertFalse((self.target / 'alpha/old').exists())
        self.assertEqual((self.target / 'alpha/new/renamed.txt').read_text(), 'new')

    def test_unstaged_file_deletion_is_reflected(self):
        self.put('alpha/deleted.txt', 'old')
        self.track()
        self.run_install(['alpha'])
        (self.source / 'skills/alpha/deleted.txt').unlink()
        self.run_install(['alpha'])
        self.assertFalse((self.target / 'alpha/deleted.txt').exists())

    def test_removed_skill_is_retained_reported_and_explicitly_removed(self):
        self.run_install()
        shutil.rmtree(self.source / 'skills/beta')
        result = self.run_install()
        self.assertIn('--mode copy --remove beta', result.stdout)
        self.assertEqual((self.target / 'beta/SKILL.md').read_text(), 'beta')
        self.run_install(['beta'], remove=True)
        self.assertFalse((self.target / 'beta').exists())
        self.assertFalse(self.manifest('beta').exists())

    def test_renamed_skill_registers_new_name_and_retains_old_name(self):
        self.run_install()
        (self.source / 'skills/beta').rename(self.source / 'skills/delta')
        self.track()
        result = self.run_install()
        self.assertIn('--mode copy --remove beta', result.stdout)
        self.assertEqual((self.target / 'beta/SKILL.md').read_text(), 'beta')
        self.assertEqual((self.target / 'delta/SKILL.md').read_text(), 'beta')
        self.run_install(['beta'], remove=True)
        self.assertTrue((self.target / 'delta/SKILL.md').is_file())

    def test_reports_retained_skills_when_no_source_skills_remain(self):
        self.run_install()
        shutil.rmtree(self.source / 'skills')
        result = self.run_install()
        self.assertIn('--mode copy --remove alpha', result.stdout)
        self.assertTrue((self.target / 'alpha/SKILL.md').is_file())
        self.run_install(['alpha'], remove=True)
        self.assertFalse((self.target / 'alpha').exists())

    def test_missing_transitive_dependencies_stop_before_any_writes(self):
        self.put('alpha/SKILL.md', '[beta](../beta/SKILL.md)')
        self.put('beta/SKILL.md', '`../gamma/SKILL.md`')
        result = self.assert_preflight_failure(['alpha'], message='依存スキルが未登録')
        self.assertIn('beta, gamma', result.stderr)
        self.run_install(['alpha', 'beta', 'gamma'])
        (self.source / 'skills').rename(self.source / 'hidden-skills')
        self.assertEqual((self.target / 'alpha/../beta/SKILL.md').read_text(), '`../gamma/SKILL.md`')
        self.assertEqual((self.target / 'alpha/../gamma/SKILL.md').read_text(), 'gamma')

    def test_existing_copied_dependencies_are_reused_and_cycles_terminate(self):
        self.put('alpha/SKILL.md', '[beta](../beta/SKILL.md)')
        self.put('beta/SKILL.md', '[alpha](../alpha/SKILL.md)')
        self.run_install(['alpha', 'beta'])
        self.run_install(['alpha'])
        self.assertFalse((self.target / 'gamma').exists())

    def test_selective_dependency_sync_cannot_break_an_installed_dependent(self):
        self.put('alpha/SKILL.md', '[guide](../beta/references/old.md)')
        self.put('beta/references/old.md', 'guide')
        self.track()
        self.run_install(['alpha', 'beta'])
        (self.source / 'skills/beta/references/old.md').rename(
            self.source / 'skills/beta/references/new.md')
        self.put('alpha/SKILL.md', '[guide](../beta/references/new.md)')
        self.track()
        result = self.assert_preflight_failure(['beta'], message='alpha')
        self.assertIn('beta/references/old.md', result.stderr)
        self.run_install(['alpha', 'beta'])
        self.assertEqual((self.target / 'alpha/SKILL.md').read_text(),
                         '[guide](../beta/references/new.md)')
        self.assertEqual((self.target / 'beta/references/new.md').read_text(), 'guide')
        self.assertFalse((self.target / 'beta/references/old.md').exists())

    def test_dependency_directory_sync_reports_all_installed_dependents(self):
        for name in ('alpha', 'gamma'):
            self.put(name + '/SKILL.md', '[guides](../beta/old/)')
        self.put('beta/old/guide.md', 'guide')
        self.track()
        self.run_install()
        (self.source / 'skills/beta/old').rename(self.source / 'skills/beta/new')
        for name in ('alpha', 'gamma'):
            self.put(name + '/SKILL.md', '[guides](../beta/new/)')
        self.track()
        result = self.assert_preflight_failure(['beta'], message='beta/old')
        self.assertIn('alpha', result.stderr)
        self.assertIn('gamma', result.stderr)
        self.assert_preflight_failure(['alpha', 'beta'], message='gamma')
        self.run_install()
        self.assertTrue((self.target / 'beta/new/guide.md').is_file())
        self.assertFalse((self.target / 'beta/old').exists())

    def test_compatible_dependency_sync_preserves_unselected_copies(self):
        self.put('alpha/SKILL.md', '[guide](../beta/references/guide.md)\n[script](scripts/run.py)')
        self.put('alpha/scripts/run.py', 'print("run")')
        self.put('beta/references/guide.md', 'old')
        self.track()
        self.run_install()
        (self.target / 'gamma/SKILL.md').write_text('unrelated user edit')
        unselected = self.tree(self.target / 'alpha'), self.tree(self.target / 'gamma')
        self.put('beta/references/guide.md', 'new')
        self.run_install(['beta'])
        self.assertEqual((self.tree(self.target / 'alpha'), self.tree(self.target / 'gamma')), unselected)
        self.assertEqual((self.target / 'beta/references/guide.md').read_text(), 'new')

    def test_dependency_sync_protects_a_dependent_removed_from_source(self):
        self.put('alpha/SKILL.md', '[guide](../beta/references/old.md)')
        self.put('beta/references/old.md', 'guide')
        self.track()
        self.run_install(['alpha', 'beta'])
        shutil.rmtree(self.source / 'skills/alpha')
        (self.source / 'skills/beta/references/old.md').unlink()
        self.track()
        self.assert_preflight_failure(['beta'], message='alpha')

    def test_fifo_in_unselected_copy_stops_sync_without_blocking_or_changes(self):
        self.run_install()
        path = self.target / 'gamma/SKILL.md'
        path.unlink()
        os.mkfifo(path)
        self.put('beta/SKILL.md', 'updated beta')
        metadata = self.target / copy_install.MANAGEMENT
        before = self.tree(self.target / 'beta'), self.tree(metadata), self.exclude.read_bytes()
        result = subprocess.run(
            [sys.executable, str(self.source / 'scripts/install.py'), str(self.target),
             'beta', '--mode', 'copy'], env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('通常ファイル以外', result.stderr)
        self.assertIn(str(path), result.stderr)
        self.assertTrue(stat.S_ISFIFO(path.stat().st_mode))
        self.assertEqual((self.tree(self.target / 'beta'), self.tree(metadata),
                          self.exclude.read_bytes()), before)

    def test_reference_file_replaced_with_fifo_at_open_is_rejected_without_blocking(self):
        path = self.root / 'reference.md'
        path.write_text('regular file')
        script = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import copy_install as c
path = Path(sys.argv[2])
original = os.open
def replace_before_open(file, flags, *args, **kwargs):
    if Path(file) == path:
        path.unlink()
        os.mkfifo(path)
    return original(file, flags, *args, **kwargs)
c.os.open = replace_before_open
try:
    c.read_regular_bytes(path)
except ValueError as error:
    print(error, file=sys.stderr)
    sys.exit(1)
'''
        result = subprocess.run([sys.executable, '-c', script, str(self.source / 'scripts'), str(path)],
                                env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('通常ファイル以外', result.stderr)
        self.assertTrue(stat.S_ISFIFO(path.stat().st_mode))

    def test_skill_directory_dependency_requires_a_managed_copy(self):
        self.put('alpha/SKILL.md', '[beta](../beta/)')
        self.assert_preflight_failure(['alpha'], message='beta')
        self.run_install(['beta'], mode=None)
        self.assert_preflight_failure(['alpha'], message='beta')
        self.run_install(['alpha', 'beta'])
        self.run_install(['alpha'])
        (self.source / 'skills').rename(self.source / 'hidden-skills')
        self.assertEqual((self.target / 'alpha/../beta/SKILL.md').read_text(), 'beta')

    def test_dependency_subdirectory_can_be_selected_and_reused(self):
        self.put('alpha/SKILL.md', '[beta references](../beta/references/)')
        self.put('beta/references/guide.md', 'guide')
        self.track()
        self.assert_preflight_failure(['alpha'], message='beta')
        self.run_install(['alpha', 'beta'])
        self.run_install(['alpha'])
        (self.source / 'skills').rename(self.source / 'hidden-skills')
        self.assertEqual((self.target / 'alpha/../beta/references/guide.md').read_text(), 'guide')

    def test_directory_dependencies_are_checked_transitively(self):
        self.put('alpha/SKILL.md', '[beta](../beta/)')
        self.put('beta/SKILL.md', '[gamma](../gamma/)')
        result = self.assert_preflight_failure(['alpha'], message='beta, gamma')
        self.assertIn('依存スキルが未登録', result.stderr)
        self.run_install(['alpha', 'beta', 'gamma'])

    def test_nonexistent_dependency_directory_stops_before_changes(self):
        self.put('alpha/SKILL.md', '[beta references](../beta/reference/)')
        self.put('beta/references/guide.md', 'guide')
        self.track()
        self.assert_preflight_failure(['alpha', 'beta'], message='依存参照先がありません')

    def test_symlink_dependency_is_not_sufficient_for_copy(self):
        self.put('alpha/SKILL.md', '[beta](../beta/SKILL.md)')
        self.run_install(['beta'], mode=None)
        self.assert_preflight_failure(['alpha'], message='beta')

    def test_internal_reference_must_be_tracked(self):
        self.put('alpha/SKILL.md', '[note](references/note.md)')
        self.put('alpha/references/note.md', 'not staged')
        self.assert_preflight_failure(['alpha'], message='git add')
        self.track()
        self.run_install(['alpha'])
        self.assertTrue((self.target / 'alpha/references/note.md').is_file())

    def test_source_symlinks_and_symlink_ancestors_stop_before_writes(self):
        foreign = self.root / 'foreign'
        foreign.mkdir()
        (foreign / 'note.txt').write_text('external')
        link = self.source / 'skills/beta/link'
        link.symlink_to(foreign)
        self.track()
        self.assert_preflight_failure(['alpha', 'beta'], message='シンボリックリンク')
        link.unlink()
        link.mkdir()
        (link / 'note.txt').write_text('tracked')
        self.track()
        shutil.rmtree(link)
        link.symlink_to(foreign)
        self.assert_preflight_failure(['beta'], message='シンボリックリンク')
        self.assertEqual((foreign / 'note.txt').read_text(), 'external')

    def test_broken_skill_file_link_is_rejected_in_all_registration(self):
        path = self.source / 'skills/beta/SKILL.md'
        path.unlink()
        path.symlink_to(self.root / 'missing')
        self.track()
        self.assert_preflight_failure(message='シンボリックリンク')

    def test_skill_name_owner_does_not_collide_with_management_marker(self):
        self.put('owner/SKILL.md', 'owner skill')
        self.track()
        self.run_install(['owner'])
        self.run_install(['owner'])
        self.assertEqual((self.target / 'owner/SKILL.md').read_text(), 'owner skill')

    def test_unrelated_files_directories_and_foreign_links_are_protected(self):
        for kind in ['file', 'directory', 'link']:
            with self.subTest(kind=kind):
                self.target.mkdir(parents=True, exist_ok=True)
                target = self.target / 'beta'
                if kind == 'file': target.write_text('user')
                elif kind == 'directory': target.mkdir()
                else: target.symlink_to(self.root / 'absent')
                self.assert_preflight_failure(['alpha', 'beta'])
                copy_install.remove_item(target)

    def test_independent_add_modify_delete_chmod_and_empty_directory_are_protected(self):
        for mutation in ['add', 'modify', 'delete', 'chmod', 'empty-directory', 'link']:
            with self.subTest(mutation=mutation):
                self.run_install(['alpha', 'beta'])
                target = self.target / 'beta'
                original = (target / 'SKILL.md').read_bytes()
                original_mode = (target / 'SKILL.md').stat().st_mode & 0o777
                if mutation == 'add': (target / 'user.txt').write_text('user')
                elif mutation == 'modify': (target / 'SKILL.md').write_text('user')
                elif mutation == 'delete': (target / 'SKILL.md').unlink()
                elif mutation == 'chmod': (target / 'SKILL.md').chmod(0o755)
                elif mutation == 'empty-directory': (target / 'user').mkdir()
                else: (target / 'user').symlink_to(self.root / 'outside')
                self.assert_preflight_failure(['alpha', 'beta'])
                self.assert_preflight_failure(['alpha', 'beta'], remove=True)
                shutil.rmtree(target)
                target.mkdir()
                (target / 'SKILL.md').write_bytes(original)
                (target / 'SKILL.md').chmod(original_mode)

    def test_missing_corrupt_foreign_and_unsafe_metadata_stop_before_writes(self):
        self.run_install()
        original = self.manifest('beta').read_bytes()
        valid = json.loads(original)
        invalid = [None, b'broken', json.dumps([]).encode(),
                   json.dumps({**valid, 'source': '/foreign'}).encode(),
                   json.dumps({**valid, 'version': 2}).encode(),
                   json.dumps({**valid, 'files': {'../outside': {'sha256': '0'*64, 'mode': 420}}}).encode()]
        for value in invalid:
            with self.subTest(value=value):
                if value is None: self.manifest('beta').unlink()
                else: self.manifest('beta').write_bytes(value)
                self.assert_preflight_failure(['alpha', 'beta'])
                self.assert_preflight_failure(['alpha', 'beta'], remove=True)
                self.manifest('beta').write_bytes(original)

    def test_tracked_target_and_metadata_are_protected(self):
        self.run_install()
        self.git('add', '-f', '.agents/skills/beta/SKILL.md')
        self.assert_preflight_failure(['alpha', 'beta'], message='Git 管理対象')
        self.assert_preflight_failure(['alpha', 'beta'], remove=True)
        self.git('rm', '--cached', '.agents/skills/beta/SKILL.md')
        self.git('add', '-f', str(self.manifest('beta').relative_to(self.repo)))
        self.assert_preflight_failure(['alpha'])

    def test_explicit_copy_removal_and_default_link_behavior_are_separate(self):
        self.run_install(['alpha'])
        self.run_install(['alpha'], mode=None, remove=True, success=False)
        self.assertTrue((self.target / 'alpha/SKILL.md').is_file())
        self.run_install(['alpha'], remove=True)
        self.run_install(['alpha'], mode=None)
        self.assertTrue((self.target / 'alpha').is_symlink())
        self.assert_preflight_failure(['alpha'], remove=True, message='copy 方式で管理していない')

    def test_rejected_symlink_operations_preserve_managed_copy_exclusions(self):
        self.run_install(['alpha', 'beta'])
        for state in ('unchanged', 'edited', 'corrupt-metadata'):
            if state == 'edited':
                (self.target / 'alpha/SKILL.md').write_text('user edit')
            elif state == 'corrupt-metadata':
                self.manifest('alpha').write_text('invalid json')
            before = self.tree(), self.exclude.read_bytes()
            self.assertEqual(self.git('status', '--porcelain', '--untracked-files=all'), '')
            for mode in (None, 'symlink'):
                for names, remove in ((['alpha'], False), ([], False), (['alpha'], True)):
                    with self.subTest(state=state, mode=mode, names=names, remove=remove):
                        result = self.run_install(names, mode=mode, remove=remove, success=False)
                        self.assertIn('コピーの管理情報', result.stderr)
                        self.assertEqual((self.tree(), self.exclude.read_bytes()), before)
                        self.assertEqual(self.git('status', '--porcelain', '--untracked-files=all'), '')

    def test_own_individual_link_migrates_without_modifying_source(self):
        self.run_install(['alpha'], mode=None)
        source_before = (self.source / 'skills/alpha/SKILL.md').read_bytes()
        self.run_install(['alpha'])
        self.assertFalse((self.target / 'alpha').is_symlink())
        self.assertEqual((self.source / 'skills/alpha/SKILL.md').read_bytes(), source_before)
        self.run_install(['alpha'], mode=None, success=False)
        self.assertFalse((self.target / 'alpha').is_symlink())

    def test_bundle_requires_normal_migration_first(self):
        self.target.mkdir(parents=True)
        (self.target / 'agent-skills').symlink_to(self.source / 'skills')
        self.assert_preflight_failure(['alpha'], message='旧一括リンク')
        self.run_install(['alpha'], mode=None)
        self.run_install(['alpha'])
        self.assertFalse((self.target / 'alpha').is_symlink())
        self.assertTrue((self.target / 'beta').is_symlink())

    def test_exclude_negation_and_malformed_markers_stop_without_writes(self):
        self.exclude.write_text('# BEGIN agent-skills\n')
        self.assert_preflight_failure()
        self.exclude.write_text('# user\nprivate.txt\n')
        (self.repo / '.gitignore').write_text('!/.agents/skills/beta\n')
        self.assert_preflight_failure()

    def test_removal_also_checks_metadata_exclusion_before_changes(self):
        self.run_install()
        (self.repo / '.gitignore').write_text('!/.agents/skills/.agent-skills-copy\n')
        self.assert_preflight_failure(['alpha'], remove=True, message='除外が有効ではありません')

    def test_metadata_links_and_non_regular_locks_are_protected(self):
        self.run_install()
        root = self.target / copy_install.MANAGEMENT
        lock = root / '.lock'
        lock.unlink()
        lock.symlink_to(self.root / 'external')
        self.assert_preflight_failure(['alpha'])
        lock.unlink()
        lock.write_bytes(b'')
        (root / 'transactions').rmdir()
        (root / 'transactions').symlink_to(self.root / 'external')
        self.assert_preflight_failure(['alpha'])

    def test_staging_failure_leaves_copy_unchanged_and_retry_succeeds(self):
        self.run_install(['alpha'])
        self.put('alpha/SKILL.md', 'new')
        original = Path.write_bytes
        def fail_stage(path, data):
            if path.name == 'SKILL.md' and 'transactions' in path.parts:
                raise OSError('simulated staging failure')
            return original(path, data)
        with patch.object(Path, 'write_bytes', fail_stage), self.assertRaisesRegex(ValueError, '実行前へ復旧'):
            self.direct(['alpha'])
        self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'alpha')
        self.run_install(['alpha'])
        self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'new')

    def test_copy_lock_excludes_another_installer(self):
        self.run_install(['alpha'])
        before = self.tree(), self.exclude.read_bytes()
        with copy_install.copy_lock(self.target / copy_install.MANAGEMENT):
            self.run_install(['alpha'], success=False)
        self.assertEqual((self.tree(), self.exclude.read_bytes()), before)

    def test_interrupted_management_initialization_allows_retry(self):
        for phase in ('writing-owner', 'owner-ready'):
            with self.subTest(phase=phase):
                target = self.target / phase
                script = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import copy_install as c
if sys.argv[5] == 'writing-owner':
    original = c.tempfile.mkstemp
    def interrupt(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(42)
    c.tempfile.mkstemp = interrupt
else:
    original = c.atomic_json
    def interrupt(path, value):
        original(path, value)
        if path.name == c.OWNER_FILE:
            os._exit(42)
    c.atomic_json = interrupt
c.install_copy(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), ('alpha',))
'''
                result = subprocess.run(
                    [sys.executable, '-c', script, str(self.source / 'scripts'),
                     str(self.source / 'skills'), str(target), str(self.repo), phase],
                    env=self.env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
                self.assertFalse((target / copy_install.MANAGEMENT).exists())
                self.assertFalse((target / 'alpha').exists())
                self.run_install(['alpha'], target=target)
                self.assertEqual((target / 'alpha/SKILL.md').read_text(), 'alpha')
                self.assertEqual(json.loads((target / copy_install.MANAGEMENT /
                                            copy_install.OWNER_FILE).read_text()), copy_install.OWNER)

    def test_concurrent_management_initialization_preserves_other_installation(self):
        original = copy_install.publish_directory
        root = self.target / copy_install.MANAGEMENT
        published = []
        def publish_other(path, destination):
            if path.name.startswith(copy_install.MANAGEMENT + '-init-') and destination == root:
                self.run_install(['beta'])
                published.append(root.stat().st_ino)
            return original(path, destination)
        with patch.object(copy_install, 'publish_directory', publish_other):
            self.direct(['alpha'])
        self.assertEqual(published, [root.stat().st_ino])
        for name in ('alpha', 'beta'):
            self.assertEqual((self.target / name / 'SKILL.md').read_text(), name)
            self.assertTrue(self.manifest(name).is_file())
        self.assertEqual(list(self.target.glob(copy_install.MANAGEMENT + '-init-*')), [])
        self.assertEqual(self.git('status', '--porcelain', '--untracked-files=all'), '')

    def test_management_initialization_does_not_replace_an_unrelated_directory(self):
        original = copy_install.atomic_json
        root = self.target / copy_install.MANAGEMENT
        def create_unrelated(path, value):
            original(path, value)
            if path.name == copy_install.OWNER_FILE:
                root.mkdir()
        with patch.object(copy_install, 'atomic_json', create_unrelated):
            with self.assertRaisesRegex(ValueError, '管理情報を読み取れません'):
                self.direct(['alpha'])
        self.assertEqual(list(root.iterdir()), [])
        self.assertFalse((self.target / 'alpha').exists())
        self.assertEqual(list(self.target.glob(copy_install.MANAGEMENT + '-init-*')), [])
        self.run_install(['alpha'], success=False)

    def test_management_publication_does_not_replace_a_directory_created_at_rename(self):
        original = copy_install.publish_directory
        root = self.target / copy_install.MANAGEMENT
        created = []
        def create_at_rename(path, destination):
            if path.name.startswith(copy_install.MANAGEMENT + '-init-') and destination == root:
                root.mkdir()
                root.chmod(0o755)
                created.append(root.stat().st_ino)
            return original(path, destination)
        with patch.object(copy_install, 'publish_directory', create_at_rename):
            with self.assertRaisesRegex(ValueError, '管理情報を読み取れません'):
                self.direct(['alpha'])
        self.assertEqual(created, [root.stat().st_ino])
        self.assertEqual(root.stat().st_mode & 0o777, 0o755)
        self.assertEqual(list(root.iterdir()), [])
        self.assertFalse((self.target / 'alpha').exists())
        self.assertEqual(list(self.target.glob(copy_install.MANAGEMENT + '-init-*')), [])

    def test_failed_management_initialization_cleans_staging_and_allows_retry(self):
        original = copy_install.atomic_json
        def fail_owner(path, value):
            if path.name == copy_install.OWNER_FILE:
                raise OSError('simulated owner write failure')
            return original(path, value)
        with patch.object(copy_install, 'atomic_json', fail_owner):
            with self.assertRaisesRegex(OSError, 'simulated owner write failure'):
                self.direct(['alpha'])
        self.assertFalse((self.target / copy_install.MANAGEMENT).exists())
        self.assertEqual(list(self.target.glob(copy_install.MANAGEMENT + '-init-*')), [])
        self.run_install(['alpha'])
        self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'alpha')

    def test_management_publication_without_exclusive_rename_stops_safely(self):
        with patch.object(copy_install.ctypes, 'CDLL', return_value=object()):
            with self.assertRaises(OSError) as raised:
                self.direct(['alpha'])
        self.assertEqual(raised.exception.errno, errno.ENOTSUP)
        self.assertFalse((self.target / copy_install.MANAGEMENT).exists())
        self.assertFalse((self.target / 'alpha').exists())
        self.assertEqual(list(self.target.glob(copy_install.MANAGEMENT + '-init-*')), [])
        self.run_install(['alpha'])
        self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'alpha')

    def test_other_destinations_skills_and_user_exclude_rules_are_preserved(self):
        original = '# user\r\nprivate.txt\r\n'
        self.exclude.write_bytes(original.encode())
        self.run_install()
        another = self.repo / '.claude/skills'
        self.run_install(['beta'], target=another)
        self.run_install(['alpha'], remove=True)
        content = self.exclude.read_bytes()
        self.assertTrue(content.startswith(original.encode()))
        self.assertIn(b'/.agents/skills/beta\n', content)
        self.assertIn(b'/.claude/skills/beta\n', content)
        self.assertNotIn(b'/.agents/skills/alpha\n', content)
        self.assertTrue((self.target / 'beta/SKILL.md').is_file())

    def test_both_agent_home_destinations_work_without_git(self):
        for agent in ['.agents', '.claude']:
            target = self.root / 'home' / agent / 'skills'
            self.run_install(['alpha'], target=target)
            self.assertEqual((target / 'alpha/SKILL.md').read_text(), 'alpha')

    def test_worktree_uses_common_exclude(self):
        (self.repo / 'README.md').write_text('project')
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'initial')
        worktree = self.root / 'project-worktree'
        self.git('worktree', 'add', '--detach', str(worktree))
        self.run_install(target=worktree / '.agents/skills')
        self.assertIn(b'/.agents/skills/alpha\n', self.exclude.read_bytes())

    def test_failure_restores_current_skill_but_keeps_completed_skills(self):
        self.run_install()
        self.put('alpha/SKILL.md', 'new alpha')
        self.put('beta/SKILL.md', 'new beta')
        before_record = self.manifest('beta').read_bytes()
        original = copy_install.atomic_json
        def fail_manifest(path, value):
            if path == self.manifest('beta'):
                raise OSError('simulated metadata failure')
            return original(path, value)
        with patch.object(copy_install, 'atomic_json', fail_manifest):
            with self.assertRaisesRegex(ValueError, '実行前へ復旧'):
                self.direct(['alpha', 'beta'])
        self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'new alpha')
        self.assertEqual((self.target / 'beta/SKILL.md').read_text(), 'beta')
        self.assertEqual(self.manifest('beta').read_bytes(), before_record)
        self.run_install(['alpha', 'beta'])
        self.assertEqual((self.target / 'beta/SKILL.md').read_text(), 'new beta')

    def test_failed_link_migration_restores_link_and_source(self):
        self.run_install(['alpha'], mode=None)
        original = copy_install.atomic_json
        def fail_manifest(path, value):
            if path == self.manifest('alpha'):
                raise OSError('simulated migration failure')
            return original(path, value)
        with patch.object(copy_install, 'atomic_json', fail_manifest), self.assertRaises(ValueError):
            self.direct(['alpha'])
        self.assertTrue((self.target / 'alpha').is_symlink())
        self.assertEqual((self.source / 'skills/alpha/SKILL.md').read_text(), 'alpha')
        self.run_install(['alpha'])

    def test_hard_interruption_recovers_copy_and_metadata_on_rerun(self):
        self.run_install()
        self.put('alpha/SKILL.md', 'new alpha')
        self.put('beta/SKILL.md', 'new beta')
        self.interrupt('beta')
        self.assertTrue((self.target / copy_install.MANAGEMENT / 'transactions/beta/before').exists())
        result = self.run_install(['alpha', 'beta'])
        self.assertIn('復旧:', result.stdout)
        self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'new alpha')
        self.assertEqual((self.target / 'beta/SKILL.md').read_text(), 'new beta')

    def test_failed_recovery_keeps_backup_and_rerun_recovers_after_cause_is_fixed(self):
        self.run_install(['alpha'])
        self.put('alpha/SKILL.md', 'new alpha')
        write_json, rename = copy_install.atomic_json, Path.rename
        def fail_manifest(path, value):
            if path == self.manifest('alpha'):
                raise OSError('simulated metadata failure')
            return write_json(path, value)
        def fail_restore(path, target):
            if path.name == 'before' and path.parent.name == 'alpha':
                raise OSError('simulated recovery failure')
            return rename(path, target)
        with patch.object(copy_install, 'atomic_json', fail_manifest), patch.object(Path, 'rename', fail_restore):
            with self.assertRaisesRegex(ValueError, '復旧待ち'):
                self.direct(['alpha'])
        backup = self.target / copy_install.MANAGEMENT / 'transactions/alpha/before'
        self.assertEqual((backup / 'SKILL.md').read_text(), 'alpha')
        self.run_install(['alpha'])
        self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'new alpha')
        self.assertFalse(backup.exists())

    def test_committed_interruption_does_not_roll_back_completed_copy(self):
        self.run_install()
        self.put('alpha/SKILL.md', 'new alpha')
        self.put('beta/SKILL.md', 'new beta')
        self.interrupt('beta', after_commit=True)
        self.run_install(['alpha', 'beta'])
        self.assertEqual((self.target / 'beta/SKILL.md').read_text(), 'new beta')

    def test_retry_after_partial_rollback_cleanup_restores_previous_state(self):
        self.put('alpha/extra.txt', 'extra')
        self.track()
        for installed in [False, True]:
            with self.subTest(installed=installed):
                self.put('alpha/SKILL.md', 'alpha')
                if installed:
                    self.run_install(['alpha'])
                before_record = self.manifest('alpha').read_bytes() if installed else None
                self.put('alpha/SKILL.md', 'new alpha')
                original_json = copy_install.atomic_json
                original_remove = copy_install.remove_item

                def fail_manifest(path, value):
                    if path == self.manifest('alpha'):
                        raise OSError('simulated manifest failure')
                    return original_json(path, value)

                def fail_after_partial_removal(path):
                    skill = path / 'SKILL.md'
                    if path.is_dir() and skill.is_file() and skill.read_text() == 'new alpha':
                        skill.unlink()
                        raise OSError('simulated partial cleanup failure')
                    return original_remove(path)

                with patch.object(copy_install, 'atomic_json', fail_manifest), \
                        patch.object(copy_install, 'remove_item', fail_after_partial_removal):
                    with self.assertRaisesRegex(ValueError, '復旧待ち'):
                        self.direct(['alpha'])
                if installed:
                    self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'alpha')
                    self.assertEqual(self.manifest('alpha').read_bytes(), before_record)
                else:
                    self.assertFalse((self.target / 'alpha').exists())
                    self.assertFalse(self.manifest('alpha').exists())
                transaction = self.target / copy_install.MANAGEMENT / 'transactions/alpha'
                self.assertTrue((transaction / 'journal.json').is_file())
                if installed:
                    (self.target / 'alpha/SKILL.md').write_text('user edit during recovery')
                    self.assert_preflight_failure(['alpha'], message='復旧')
                    (self.target / 'alpha/SKILL.md').write_text('alpha')
                self.run_install(['alpha'])
                self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'new alpha')
                self.assertEqual((self.target / 'alpha/extra.txt').read_text(), 'extra')
                self.assertFalse(transaction.exists())

    def test_retry_after_partial_committed_cleanup_keeps_completed_copy(self):
        self.run_install(['alpha'])
        self.put('alpha/SKILL.md', 'new alpha')
        original_unlink = os.unlink

        def fail_after_removing_old_skill(path, *args, **kwargs):
            if Path(path).name == 'SKILL.md':
                descriptor = os.open(path, os.O_RDONLY, dir_fd=kwargs.get('dir_fd'))
                try:
                    data = os.read(descriptor, 1024)
                finally:
                    os.close(descriptor)
                if data == b'alpha':
                    original_unlink(path, *args, **kwargs)
                    raise OSError(errno.EIO, 'simulated partial committed cleanup failure')
            return original_unlink(path, *args, **kwargs)

        with patch.object(os, 'unlink', fail_after_removing_old_skill):
            with self.assertRaisesRegex(OSError, 'partial committed cleanup'):
                self.direct(['alpha'])
        self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'new alpha')
        record = self.manifest('alpha').read_bytes()
        self.run_install(['alpha'])
        self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'new alpha')
        self.assertEqual(self.manifest('alpha').read_bytes(), record)
        self.assertFalse((self.target / copy_install.MANAGEMENT / 'transactions/alpha').exists())

    def test_removal_failure_restores_current_skill_and_allows_retry(self):
        self.run_install()
        original = Path.unlink
        def fail_metadata(path, *args, **kwargs):
            if path == self.manifest('beta'):
                raise OSError('simulated remove failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_metadata), self.assertRaisesRegex(ValueError, '実行前へ復旧'):
            self.direct(['alpha', 'beta'], remove=True)
        self.assertFalse((self.target / 'alpha').exists())
        self.assertEqual((self.target / 'beta/SKILL.md').read_text(), 'beta')
        self.run_install(['alpha', 'beta'], remove=True)
        self.assertFalse((self.target / 'beta').exists())

    def test_unrecognized_management_directory_is_preserved(self):
        root = self.target / copy_install.MANAGEMENT
        root.mkdir(parents=True)
        (root / 'private.txt').write_text('user')
        self.assert_preflight_failure()

    def test_real_distribution_can_be_read_without_source_repository(self):
        for folder in ['skills', '.agents']:
            shutil.rmtree(self.source / folder)
        shutil.copytree(REPOSITORY / 'skills', self.source / 'skills', ignore=shutil.ignore_patterns('__pycache__'))
        self.track()
        self.run_install()
        (self.source / 'skills').rename(self.source / 'unavailable-skills')
        self.assert_copied_references_readable()
        self.assertTrue((self.target / 'worktree-link-local/references/local-files.md').is_file())

    def test_distribution_reference_check_accepts_directories_and_rejects_missing_targets(self):
        self.put('alpha/SKILL.md', '[beta](../beta/)\n'
                 '[references](../beta/references/)\n'
                 '[guide](../beta/references/guide.md)')
        self.put('beta/references/guide.md', 'guide')
        self.track()
        self.run_install()
        (self.source / 'skills').rename(self.source / 'unavailable-skills')
        self.assert_copied_references_readable()
        (self.target / 'beta/references/guide.md').unlink()
        with self.assertRaisesRegex(AssertionError, 'guide.md'):
            self.assert_copied_references_readable()


if __name__ == '__main__':
    unittest.main()
