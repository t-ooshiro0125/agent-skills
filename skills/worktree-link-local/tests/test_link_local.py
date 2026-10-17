"""Run all tests or select a unittest method from the command line."""
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/link_local.py'


def run(*args, input=None, ok=True):
    result = subprocess.run(list(map(str, args)), input=input, text=True, capture_output=True)
    if ok and result.returncode != 0:
        raise AssertionError(result.stderr)
    if not ok and result.returncode == 0:
        raise AssertionError(result.stdout)
    return result.stdout


class LinkLocalTest(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        environment.start()
        self.addCleanup(environment.stop)
        temp = tempfile.TemporaryDirectory(prefix='worktree-link-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bare = self.root / 'repo.git'
        self.source, self.target = self.root / 'source space', self.root / 'target space'
        run('git', 'init', '--bare', self.bare)
        ignore = self.git('hash-object', '-w', '--stdin', input='/local/\n/shared/\n/blocked/\n')
        tracked = self.git('hash-object', '-w', '--stdin', input='original\n')
        tree = self.git('mktree', input=f'100644 blob {ignore}\t.gitignore\n100644 blob {tracked}\ttracked.txt\n')
        self.commit = self.git('-c', 'user.name=Skill Test', '-c', 'user.email=skill@example.invalid',
                               'commit-tree', tree, input='fixture\n')
        self.git('update-ref', 'refs/heads/main', self.commit)
        self.git('worktree', 'add', self.source, 'main')
        self.git('worktree', 'add', '-b', 'feat/test', self.target, 'main')
        (self.source / 'local').mkdir()
        (self.source / 'local/config').write_text('shared content')
        (self.source / 'shared').mkdir()
        (self.source / 'shared/data').write_text('model')
        (self.source / 'tracked.txt').write_text('work in progress')
        (self.source / 'untracked.png').write_text('reference')
        self.source_index = Path(run('git', '-C', self.source, 'rev-parse', '--git-path', 'index').strip())
        self.target_index = Path(run('git', '-C', self.target, 'rev-parse', '--git-path', 'index').strip())
        self.status = run('git', '-C', self.source, 'status', '--porcelain')
        self.indices = (self.source_index.read_bytes(), self.target_index.read_bytes())
        self.exclude = self.bare / 'info/exclude'
        self.initial_exclude = self.exclude.read_bytes()

    def git(self, *args, input=None):
        return run('git', '-C', self.bare, *args, input=input).strip()

    def link(self, *args, ok=True):
        return run(sys.executable, SCRIPT, '--source', self.source, '--target', self.target, *args, ok=ok)

    def test_relative_links_dry_run_apply_and_rerun(self):
        self.link('--link', 'local/config', '--link', 'shared')
        self.assertFalse(os.path.lexists(self.target / 'local'))
        self.assertEqual(self.exclude.read_bytes(), self.initial_exclude)
        self.link('--link', 'local/config', '--link', 'shared', '--apply')
        self.assertEqual((self.target / 'local/config').read_text(), 'shared content')
        self.assertTrue((self.target / 'shared').is_symlink())
        self.assertFalse(os.readlink(self.target / 'shared').startswith('/'))
        self.assertEqual(run('git', '-C', self.target, 'status', '--porcelain'), '')
        self.assertEqual(run('git', '-C', self.source, 'status', '--porcelain'), self.status)
        self.assertEqual(self.indices, (self.source_index.read_bytes(), self.target_index.read_bytes()))
        first_exclude = self.exclude.read_bytes()
        self.link('--link', 'local/config', '--link', 'shared', '--apply')
        self.assertEqual(self.exclude.read_bytes(), first_exclude)

    def test_existing_file_blocks_entire_plan(self):
        (self.target / 'local').mkdir()
        first_exclude = self.exclude.read_bytes()
        (self.source / 'local/second').write_text('second')
        (self.target / 'local/conflict').write_text('keep')
        (self.source / 'local/conflict').write_text('other')
        self.link('--link', 'local/second', '--link', 'local/conflict', '--apply', ok=False)
        self.assertFalse(os.path.lexists(self.target / 'local/second'))
        self.assertEqual((self.target / 'local/conflict').read_text(), 'keep')
        self.assertEqual(self.exclude.read_bytes(), first_exclude)

    def test_broken_destination_blocks_entire_plan(self):
        (self.source / 'local/second').write_text('second')
        (self.target / 'local').mkdir()
        first_exclude = self.exclude.read_bytes()
        (self.source / 'local/broken').write_text('shared content')
        (self.target / 'local/broken').symlink_to('missing-reference')
        for mode in [[], ['--apply']]:
            result = subprocess.run([
                sys.executable, str(SCRIPT), '--source', str(self.source), '--target', str(self.target),
                '--link', 'local/second', '--link', 'local/broken', *mode,
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('既存のファイル・リンクは上書きしません', result.stderr, result.stderr)
            self.assertFalse(os.path.lexists(self.target / 'local/second'))
            self.assertTrue((self.target / 'local/broken').is_symlink())
            self.assertEqual(os.readlink(self.target / 'local/broken'), 'missing-reference')
            self.assertFalse((self.target / 'local/broken').exists())
            self.assertEqual((self.source / 'local/broken').read_text(), 'shared content')
            self.assertEqual(self.exclude.read_bytes(), first_exclude)
            self.assertEqual(self.indices, (self.source_index.read_bytes(), self.target_index.read_bytes()))

    def test_invalid_paths_are_rejected(self):
        (self.source / 'local/second').write_text('second')
        (self.target / 'local').mkdir()
        (self.target / 'local/wrong').symlink_to(self.source / 'local/second')
        (self.source / 'local/wrong').write_text('wrong')
        self.link('--link', 'local/wrong', '--apply', ok=False)
        self.link('--link', 'tracked.txt', '--apply', ok=False)
        self.link('--link', 'untracked.png', '--apply', ok=False)
        self.link('--link', 'local/missing', '--apply', ok=False)
        self.link('--link', '../source space/local', '--apply', ok=False)
        self.link('--link', '.git', '--apply', ok=False)
        self.link('--link', 'local', '--link', 'local/config', '--apply', ok=False)

    def test_symlink_parent_cannot_redirect_writes(self):
        (self.source / 'blocked').mkdir()
        (self.source / 'blocked/file').write_text('safe')
        outside = self.root / 'outside'
        outside.mkdir()
        (self.target / 'blocked').symlink_to(outside)
        self.link('--link', 'blocked/file', '--apply', ok=False)
        self.assertFalse(os.path.lexists(outside / 'file'))

    def prepare_conflict_target(self):
        # A clean target branch may intentionally unignore source-local data.
        nested_blob = self.git('hash-object', '-w', '--stdin', input='!secret\n')
        nested_tree = self.git('mktree', input=f'100644 blob {nested_blob}\t.gitignore\n')
        conflict_blob = self.git('hash-object', '-w', '--stdin', input='!/neg-root\n')
        conflict_tree = self.git('mktree', input=(
            f'100644 blob {conflict_blob}\t.gitignore\n'
            f'040000 tree {nested_tree}\tnested\n'))
        conflict_commit = self.git('-c', 'user.name=Skill Test', '-c', 'user.email=skill@example.invalid',
                              'commit-tree', conflict_tree, '-p', self.commit, input='ignore exceptions\n')
        conflict_target = self.root / 'conflict target'
        self.git('worktree', 'add', '-b', 'conflict', conflict_target, conflict_commit)
        return conflict_target

    def test_worktree_ignore_exceptions_block_entire_plan(self):
        conflict_target = self.prepare_conflict_target()
        (self.source / 'local/second').write_text('second')
        (self.source / 'neg-root').mkdir()
        (self.source / 'neg-root/data').write_text('private local data')
        (self.source / 'nested').mkdir()
        (self.source / 'nested/secret').write_text('private local config')
        with self.exclude.open('a') as stream:
            stream.write('/neg-root/\n/nested/secret\n')
        conflict_exclude = self.exclude.read_bytes()
        conflict_index = Path(run('git', '-C', conflict_target, 'rev-parse', '--git-path', 'index').strip())
        conflict_index_bytes = conflict_index.read_bytes()
        for name, rule in [('neg-root', '.gitignore:1: !/neg-root'),
                           ('nested/secret', 'nested/.gitignore:1: !secret')]:
            for mode in [[], ['--apply']]:
                result = subprocess.run([
                    sys.executable, str(SCRIPT), '--source', str(self.source), '--target', str(conflict_target),
                    '--link', 'local/second', '--link', name, *mode,
                ], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(rule, result.stderr, result.stderr)
                self.assertFalse(os.path.lexists(conflict_target / name))
                self.assertFalse(os.path.lexists(conflict_target / 'local'))
                self.assertEqual(self.exclude.read_bytes(), conflict_exclude)
                self.assertEqual(conflict_index.read_bytes(), conflict_index_bytes)
                self.assertEqual(run('git', '-C', conflict_target, 'status', '--porcelain'), '')

    def test_info_exclude_negation_is_overridden_and_rerun_is_stable(self):
        conflict_target = self.prepare_conflict_target()
        def link_to_conflict(*args):
            return run(sys.executable, SCRIPT, '--source', self.source, '--target', conflict_target, *args)
        # info/exclude and global rules have lower priority and are safe to override.
        (self.source / 'local/reincluded').write_text('local content')
        with self.exclude.open('a') as stream:
            stream.write('/local/reincluded\n!/local/reincluded\n')
        link_to_conflict('--link', 'local/reincluded', '--apply')
        self.assertEqual((conflict_target / 'local/reincluded').read_text(), 'local content')
        self.assertEqual(self.exclude.read_text().splitlines()[-1], '/local/reincluded')
        reincluded_exclude = self.exclude.read_bytes()
        link_to_conflict('--link', 'local/reincluded', '--apply')
        self.assertEqual(self.exclude.read_bytes(), reincluded_exclude)

    def test_global_ignore_exception_does_not_block_sharing(self):
        conflict_target = self.prepare_conflict_target()
        def link_to_conflict(*args):
            return run(sys.executable, SCRIPT, '--source', self.source, '--target', conflict_target, *args)
        global_ignore = self.root / 'global-ignore'
        global_ignore.write_text('!/local/global-config\n')
        self.git('config', 'core.excludesFile', str(global_ignore))
        (self.source / 'local/global-config').write_text('config')
        link_to_conflict('--link', 'local/global-config', '--apply')
        self.assertEqual((conflict_target / 'local/global-config').read_text(), 'config')

    def test_ordinary_ignored_directory(self):
        # Ordinary ignored directories have no repository-specific path restriction.
        vscode_status = run('git', '-C', self.target, 'status', '--porcelain')
        (self.source / '.vscode').mkdir()
        (self.source / '.vscode/settings.json').write_text('{}')
        with self.exclude.open('a') as stream:
            stream.write('/.vscode/\n')
        vscode_exclude = self.exclude.read_bytes()
        self.link('--link', '.vscode')
        self.assertFalse(os.path.lexists(self.target / '.vscode'))
        self.assertEqual(self.exclude.read_bytes(), vscode_exclude)
        self.link('--link', '.vscode', '--apply')
        self.assertTrue((self.target / '.vscode').is_symlink())
        self.assertFalse(os.readlink(self.target / '.vscode').startswith('/'))
        self.assertEqual((self.target / '.vscode/settings.json').read_text(), '{}')
        target_status = run('git', '-C', self.target, 'status', '--porcelain')
        self.assertEqual(target_status, vscode_status, target_status)
        self.assertEqual(self.indices, (self.source_index.read_bytes(), self.target_index.read_bytes()))

    def test_deleted_tracked_destination_blocks_entire_plan(self):
        (self.source / 'local/second').write_text('second')
        # An absent destination can still be tracked in the target index.
        tracked_target = self.root / 'tracked target'
        self.git('worktree', 'add', '-b', 'tracked-guard', tracked_target, self.commit)
        (tracked_target / 'local').mkdir()
        (tracked_target / 'local/protected').write_text('indexed content')
        run('git', '-C', tracked_target, 'add', '-f', 'local/protected')
        (tracked_target / 'local/protected').unlink()
        (self.source / 'local/protected').write_text('source content')
        tracked_status = run('git', '-C', tracked_target, 'status', '--porcelain')
        tracked_index = Path(run('git', '-C', tracked_target, 'rev-parse', '--git-path', 'index').strip())
        tracked_indices = (self.source_index.read_bytes(), tracked_index.read_bytes())
        tracked_exclude = self.exclude.read_bytes()
        for mode in [[], ['--apply']]:
            result = subprocess.run([
                sys.executable, str(SCRIPT), '--source', str(self.source), '--target', str(tracked_target),
                '--link', 'local/second', '--link', 'local/protected', *mode,
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(os.path.lexists(tracked_target / 'local/second'))
            self.assertFalse(os.path.lexists(tracked_target / 'local/protected'))
            self.assertEqual((self.source / 'local/protected').read_text(), 'source content')
            self.assertEqual(self.exclude.read_bytes(), tracked_exclude)
            self.assertEqual(tracked_indices, (self.source_index.read_bytes(), tracked_index.read_bytes()))
            self.assertEqual(run('git', '-C', tracked_target, 'status', '--porcelain'), tracked_status)

    def test_source_symlinks_dry_run_apply_and_rerun(self):
        target_status = run('git', '-C', self.target, 'status', '--porcelain')
        # Source links may resolve to files or directories outside the worktree.
        real_file = self.root / 'external file'
        real_file.write_text('external content')
        real_dir = self.root / 'external directory'
        real_dir.mkdir()
        (real_dir / 'data').write_text('external data')
        file_link, dir_link = self.source / 'local/file-link', self.source / 'local/dir-link'
        file_link.symlink_to(real_file)
        dir_link.symlink_to(os.path.relpath(real_dir, dir_link.parent), target_is_directory=True)
        source_links = (os.readlink(file_link), os.readlink(dir_link))
        symlink_exclude = self.exclude.read_bytes()
        self.link('--link', 'local/file-link', '--link', 'local/dir-link')
        for name in ['local/file-link', 'local/dir-link']:
            self.assertFalse(os.path.lexists(self.target / name))
        self.assertEqual(self.exclude.read_bytes(), symlink_exclude)
        self.link('--link', 'local/file-link', '--link', 'local/dir-link', '--apply')
        for name in ['local/file-link', 'local/dir-link']:
            destination = self.target / name
            self.assertTrue(destination.is_symlink())
            self.assertEqual(os.readlink(destination), os.path.relpath(self.source / name, destination.parent))
            self.assertEqual(destination.resolve(), (self.source / name).resolve())
        self.assertEqual((self.target / 'local/file-link').read_text(), 'external content')
        self.assertEqual((self.target / 'local/dir-link/data').read_text(), 'external data')
        symlink_exclude = self.exclude.read_bytes()
        self.link('--link', 'local/file-link', '--link', 'local/dir-link', '--apply')
        self.assertEqual(self.exclude.read_bytes(), symlink_exclude)
        self.assertEqual((os.readlink(file_link), os.readlink(dir_link)), source_links)
        self.assertEqual(self.indices, (self.source_index.read_bytes(), self.target_index.read_bytes()))
        self.assertEqual(run('git', '-C', self.target, 'status', '--porcelain'), target_status)

    def test_broken_source_link_blocks_entire_plan(self):
        (self.source / 'local/second').write_text('second')
        (self.source / 'local/broken-source').symlink_to('missing-source')
        symlink_exclude = self.exclude.read_bytes()
        for mode in [[], ['--apply']]:
            self.link('--link', 'local/second', '--link', 'local/broken-source', *mode, ok=False)
            self.assertFalse(os.path.lexists(self.target / 'local/second'))
            self.assertFalse(os.path.lexists(self.target / 'local/broken-source'))
            self.assertEqual(os.readlink(self.source / 'local/broken-source'), 'missing-source')
            self.assertEqual(self.exclude.read_bytes(), symlink_exclude)
            self.assertEqual(self.indices, (self.source_index.read_bytes(), self.target_index.read_bytes()))

    def test_directory_ignore_boundaries(self):
        boundary_ignore = self.git('hash-object', '-w', '--stdin',
                              input='/closed/\n!/closed/keep\n/mixed/*\n!/mixed/keep\n')
        boundary_tree = self.git('mktree', input=f'100644 blob {boundary_ignore}\t.gitignore\n')
        boundary_commit = self.git('-c', 'user.name=Skill Test', '-c', 'user.email=skill@example.invalid',
                              'commit-tree', boundary_tree, input='directory ignore boundaries\n')
        boundary_source, boundary_target = self.root / 'boundary source', self.root / 'boundary target'
        self.git('worktree', 'add', '-b', 'boundary-source', boundary_source, boundary_commit)
        self.git('worktree', 'add', '-b', 'boundary-target', boundary_target, boundary_commit)
        for name in ['closed', 'mixed']:
            (boundary_source / name).mkdir()
            (boundary_source / name / 'keep').write_text('local content')
        run('git', '-C', boundary_source, 'check-ignore', '-q', '--', 'closed/keep')
        run('git', '-C', boundary_source, 'check-ignore', '-q', '--', 'mixed/keep', ok=False)
        for mode in [[], ['--apply']]:
            before = self.exclude.read_bytes()
            run(sys.executable, SCRIPT, '--source', boundary_source, '--target', boundary_target,
                '--link', 'mixed', *mode, ok=False)
            self.assertFalse(os.path.lexists(boundary_target / 'mixed'))
            self.assertEqual(self.exclude.read_bytes(), before)
        before = self.exclude.read_bytes()
        run(sys.executable, SCRIPT, '--source', boundary_source, '--target', boundary_target,
            '--link', 'closed')
        self.assertFalse(os.path.lexists(boundary_target / 'closed'))
        self.assertEqual(self.exclude.read_bytes(), before)
        for _ in range(2):
            run(sys.executable, SCRIPT, '--source', boundary_source, '--target', boundary_target,
                '--link', 'closed', '--apply')
            self.assertEqual((boundary_target / 'closed/keep').read_text(), 'local content')
            run('git', '-C', boundary_target, 'check-ignore', '-q', '--', 'closed')

    def create_destination_conflict(self, kind, target, outside, real_file):
        parent = target / 'local/race'
        if kind == 'parent-link':
            parent.parent.mkdir()
            parent.symlink_to(outside, target_is_directory=True)
        else:
            parent.mkdir(parents=True, exist_ok=True)
            if kind == 'file':
                (parent / 'item').write_text('concurrent content')
            else:
                if kind == 'kept-link':
                    (parent / 'item').unlink()
                (parent / 'item').symlink_to(real_file)

    def test_concurrent_destination_conflicts_stop_before_writes(self):
        (self.source / 'local/second').write_text('second')
        outside = self.root / 'outside'
        outside.mkdir()
        real_file = self.root / 'external file'
        real_file.write_text('external content')
        # Inject changes after preflight output, before apply starts writing.
        main = runpy.run_path(str(SCRIPT), run_name='link_local_test')['main']
        (self.source / 'local/race').mkdir()
        (self.source / 'local/race/item').write_text('source item')
        for kind in ['file', 'wrong-link', 'parent-link', 'kept-link']:
            with self.subTest(kind=kind):
                race_target = self.root / ('race ' + kind)
                self.git('worktree', 'add', '-b', 'race-' + kind, race_target, self.commit)
                if kind == 'kept-link':
                    (race_target / 'local/race').mkdir(parents=True)
                    (race_target / 'local/race/item').symlink_to(self.source / 'local/race/item')
                race_index = Path(run('git', '-C', race_target, 'rev-parse', '--git-path', 'index').strip())
                before_indices = (self.source_index.read_bytes(), race_index.read_bytes())
                before = self.exclude.read_bytes()
                injected = []

                def inject(*args, **kwargs):
                    if not injected and args and str(args[0]).startswith('EXCLUDE'):
                        injected.append(True)
                        self.create_destination_conflict(kind, race_target, outside, real_file)

                argv = [str(SCRIPT), '--source', str(self.source), '--target', str(race_target),
                        '--link', 'local/second', '--link', 'local/race/item', '--apply']
                with patch.object(sys, 'argv', argv), patch('builtins.print', side_effect=inject):
                    with self.assertRaises(ValueError):
                        main()
                self.assertTrue(injected)
                self.assertFalse(os.path.lexists(race_target / 'local/second'))
                self.assertEqual(self.exclude.read_bytes(), before)
                self.assertEqual(before_indices, (self.source_index.read_bytes(), race_index.read_bytes()))
                if kind == 'file':
                    self.assertEqual((race_target / 'local/race/item').read_text(), 'concurrent content')
                elif kind in ['wrong-link', 'kept-link']:
                    self.assertEqual(os.readlink(race_target / 'local/race/item'), str(real_file))
                else:
                    self.assertEqual(os.readlink(race_target / 'local/race'), str(outside))
                    self.assertFalse(os.path.lexists(outside / 'item'))

    def test_unrelated_repository_is_rejected_before_writes(self):
        other = self.root / 'other'
        run('git', 'init', other)
        other_exclude = other / '.git/info/exclude'
        other_before = other_exclude.read_bytes()
        source_index_before = self.source_index.read_bytes()
        other_index = other / '.git/index'
        for mode in [[], ['--apply']]:
            with self.subTest(mode=mode):
                result = subprocess.run([
                    sys.executable, str(SCRIPT), '--source', str(self.source), '--target', str(other),
                    '--link', 'shared', *mode,
                ], text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(os.path.lexists(other / 'shared'))
                self.assertEqual(self.exclude.read_bytes(), self.initial_exclude)
                self.assertEqual(other_exclude.read_bytes(), other_before)
                self.assertEqual(self.source_index.read_bytes(), source_index_before)
                self.assertFalse(other_index.exists())
                self.assertEqual(set(other.iterdir()), {other / '.git'})
                self.assertIn('同じ Git リポジトリの worktree 間でのみ共有できます', result.stderr)
                self.assertEqual((self.source / 'shared/data').read_text(), 'model')


if __name__ == '__main__':
    unittest.main()
