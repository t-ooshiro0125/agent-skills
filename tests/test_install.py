"""Run with: python3 tests/test_install.py"""
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
import runpy
import subprocess
import sys
import tempfile
import unittest


INSTALL_SCRIPT = Path(__file__).resolve().parent.parent / 'scripts/install.py'


class InstallSkillsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        installer = runpy.run_path(str(INSTALL_SCRIPT))
        cls.plan_installation = staticmethod(installer['plan_installation'])
        cls.apply_link_migration = staticmethod(installer['apply_link_migration'])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        self.prepare_source_repository()
        self.set_project_paths(self.root / 'project')
        self.repo.mkdir()
        self.git('init')

    def prepare_source_repository(self):
        self.source = self.root / 'source'
        (self.source / 'scripts').mkdir(parents=True)
        shutil.copyfile(INSTALL_SCRIPT, self.source / 'scripts/install.py')
        for name in ['alpha', 'beta', 'gamma']:
            self.add_skill(name)
        local = self.source / '.agents/skills/install-skills'
        local.mkdir(parents=True)
        (local / 'SKILL.md').write_text('repository-only')

    def set_project_paths(self, repository):
        self.repo = repository
        self.target = self.repo / '.agents/skills'
        self.bundle = self.target / 'agent-skills'
        self.exclude = self.repo / '.git/info/exclude'

    def add_skill(self, name):
        folder = self.source / 'skills' / name
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text(name)

    def create_legacy_link(self, name):
        self.target.mkdir(parents=True, exist_ok=True)
        link = self.target / name
        link.symlink_to(self.source / 'skills' / name)
        return link

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], env=self.env,
                              capture_output=True, text=True, check=True).stdout.strip()

    def install(self, target=None, success=True):
        result = subprocess.run([sys.executable, str(self.source / 'scripts/install.py'), str(target or self.target)],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
        return result

    def ignored(self, path, repo=None):
        result = subprocess.run(
            ['git', '-C', str(repo or self.repo), 'check-ignore', '--no-index', '-q', '--', path],
            env=self.env, capture_output=True, text=True,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode == 0

    def test_ignored_rejects_git_execution_errors(self):
        non_git = self.root / 'non-git'
        non_git.mkdir()

        with self.assertRaises(AssertionError):
            self.ignored('private.txt', repo=non_git)

    def test_registers_bundle_and_preserves_existing_exclude_rules(self):
        original = '# User rules\nprivate.txt\n'
        self.exclude.write_text(original)

        self.install()

        self.assertEqual(self.bundle.resolve(), self.source / 'skills')
        self.assertTrue(self.exclude.read_text().startswith(original))
        self.assertTrue(self.ignored('private.txt'))
        self.assertTrue(self.ignored('.agents/skills/agent-skills'))
        self.assertFalse(self.ignored('.agents/skills/other'))

    def test_repository_only_installer_is_preserved_and_not_distributed(self):
        local = self.target / 'install-skills'
        local.mkdir(parents=True)
        (local / 'SKILL.md').write_text('local')

        self.install()

        self.assertEqual((local / 'SKILL.md').read_text(), 'local')
        self.assertFalse(self.ignored('.agents/skills/install-skills/SKILL.md'))
        self.assertFalse((self.bundle / 'install-skills').exists())

    def test_new_skills_are_available_without_reinstalling(self):
        self.install()
        original_exclude = self.exclude.read_bytes()
        self.add_skill('delta')

        self.assertTrue((self.bundle / 'delta/SKILL.md').is_file())
        self.assertEqual(self.exclude.read_bytes(), original_exclude)

    def test_reinstall_preserves_bundle_and_exclude_rules(self):
        self.install()
        original_exclude = self.exclude.read_bytes()
        original_link = os.readlink(self.bundle)
        self.install()

        self.assertEqual(os.readlink(self.bundle), original_link)
        self.assertEqual(self.exclude.read_bytes(), original_exclude)

    def test_another_destination_preserves_previous_exclusion(self):
        self.install()
        another = self.repo / 'another/skills'
        self.install(another)

        self.assertEqual((another / 'agent-skills').resolve(), self.source / 'skills')
        self.assertTrue(self.ignored('another/skills/agent-skills'))
        self.assertTrue(self.ignored('.agents/skills/agent-skills'))
        self.assertEqual(self.exclude.read_text().count('# BEGIN agent-skills'), 1)

    def test_migration_removes_only_matching_untracked_legacy_links(self):
        self.create_legacy_link('alpha')
        (self.target / 'beta').write_text('user file')
        self.create_legacy_link('gamma')
        self.git('add', '.agents/skills/gamma')
        self.exclude.write_text(
            '# User rules\n'
            '*.log\n'
            '# BEGIN agent-skills\n'
            '/.agents/skills/alpha\n'
            '/.agents/skills/beta\n'
            '/.agents/skills/gamma\n'
            '/another/skills/alpha\n'
            '# END agent-skills\n'
            '# User suffix\n'
        )

        self.install()

        self.assertFalse((self.target / 'alpha').is_symlink())
        self.assertEqual((self.target / 'beta').read_text(), 'user file')
        self.assertTrue((self.target / 'gamma').is_symlink())
        self.assertEqual(self.bundle.resolve(), self.source / 'skills')
        updated = self.exclude.read_text()
        for name in ['alpha', 'beta', 'gamma']:
            self.assertNotIn(f'/.agents/skills/{name}\n', updated)
        self.assertIn('/another/skills/alpha\n', updated)
        self.assertTrue(updated.startswith('# User rules\n*.log\n'))
        self.assertTrue(updated.endswith('# User suffix\n'))

    def test_foreign_legacy_links_are_preserved(self):
        self.target.mkdir(parents=True)
        missing = self.root / 'missing'
        (self.target / 'alpha').symlink_to(missing)

        self.install()

        self.assertEqual(os.readlink(self.target / 'alpha'), str(missing))
        self.assertFalse(self.ignored('.agents/skills/alpha'))

    def test_planning_is_silent_and_does_not_change_files(self):
        legacy = self.create_legacy_link('alpha')
        protected = self.create_legacy_link('gamma')
        self.bundle.symlink_to(self.source / 'skills')
        self.git('add', '.agents/skills/gamma')
        original_exclude = self.exclude.read_bytes()
        output = StringIO()

        with redirect_stdout(output):
            plan = self.plan_installation(self.source / 'skills', self.target, self.repo)

        self.assertEqual(output.getvalue(), '')
        self.assertEqual(self.exclude.read_bytes(), original_exclude)
        self.assertTrue(legacy.is_symlink())
        self.assertTrue(protected.is_symlink())
        self.assertTrue(plan.can_register)
        self.assertTrue(plan.already_registered)
        self.assertEqual(plan.legacy_links_to_remove, (legacy,))
        self.assertEqual(plan.protected_legacy_links, (protected,))
        self.assertIn(protected, plan.exclude_paths_to_remove)

    def test_execution_preserves_log_order(self):
        legacy = self.create_legacy_link('alpha')
        protected = self.create_legacy_link('gamma')
        self.bundle.symlink_to(self.source / 'skills')
        self.git('add', '.agents/skills/gamma')
        result = self.install()

        self.assertEqual(result.stdout.splitlines(), [
            f'登録済み: {self.bundle}',
            f'移行対象外（Git 管理対象）: {protected}',
            f'Git 除外設定: {self.exclude}',
            f'個別リンクから移行: {legacy}',
        ])
        self.assertFalse(legacy.is_symlink())
        self.assertTrue(protected.is_symlink())

    def test_conflicting_bundle_is_preserved_and_stale_rule_is_removed(self):
        self.install()
        self.bundle.unlink()
        self.bundle.write_text('user file')
        self.install(success=False)

        self.assertEqual(self.bundle.read_text(), 'user file')
        self.assertFalse(self.ignored('.agents/skills/agent-skills'))

    def test_tracked_bundle_is_preserved(self):
        self.install()
        self.git('add', '-f', '.agents/skills/agent-skills')
        self.install(success=False)

        self.assertEqual(self.bundle.resolve(), self.source / 'skills')
        self.assertFalse(self.ignored('.agents/skills/agent-skills'))

    def test_conflict_does_not_remove_old_links(self):
        self.create_legacy_link('alpha')
        self.bundle.symlink_to(self.root / 'missing')

        self.install(success=False)

        self.assertTrue((self.target / 'alpha').is_symlink())
        self.assertEqual(os.readlink(self.bundle), str(self.root / 'missing'))

    def test_apply_rejects_foreign_link_created_after_planning(self):
        legacy = self.create_legacy_link('alpha')

        plan = self.plan_installation(self.source / 'skills', self.target, self.repo)
        self.bundle.symlink_to(self.root / 'missing')
        with self.assertRaises(ValueError):
            self.apply_link_migration(plan, self.repo)

        self.assertEqual(legacy.resolve(), self.source / 'skills/alpha')
        self.assertEqual(os.readlink(self.bundle), str(self.root / 'missing'))

    def test_apply_rejects_file_created_after_planning(self):
        legacy = self.create_legacy_link('alpha')

        plan = self.plan_installation(self.source / 'skills', self.target, self.repo)
        self.bundle.write_text('user file')
        with self.assertRaises(ValueError):
            self.apply_link_migration(plan, self.repo)

        self.assertEqual(legacy.resolve(), self.source / 'skills/alpha')
        self.assertEqual(self.bundle.read_text(), 'user file')

    def test_apply_rejects_directory_created_after_planning(self):
        legacy = self.create_legacy_link('alpha')

        plan = self.plan_installation(self.source / 'skills', self.target, self.repo)
        self.bundle.mkdir()
        with self.assertRaises(ValueError):
            self.apply_link_migration(plan, self.repo)

        self.assertEqual(legacy.resolve(), self.source / 'skills/alpha')
        self.assertTrue(self.bundle.is_dir())

    def test_apply_rejects_foreign_link_replaced_after_planning(self):
        legacy = self.create_legacy_link('alpha')
        self.bundle.symlink_to(self.source / 'skills')

        plan = self.plan_installation(self.source / 'skills', self.target, self.repo)
        self.bundle.unlink()
        self.bundle.symlink_to(self.root / 'missing')
        with self.assertRaises(ValueError):
            self.apply_link_migration(plan, self.repo)

        self.assertEqual(legacy.resolve(), self.source / 'skills/alpha')
        self.assertEqual(os.readlink(self.bundle), str(self.root / 'missing'))

    def test_apply_rejects_file_replaced_after_planning(self):
        legacy = self.create_legacy_link('alpha')
        self.bundle.symlink_to(self.source / 'skills')

        plan = self.plan_installation(self.source / 'skills', self.target, self.repo)
        self.bundle.unlink()
        self.bundle.write_text('user file')
        with self.assertRaises(ValueError):
            self.apply_link_migration(plan, self.repo)

        self.assertEqual(legacy.resolve(), self.source / 'skills/alpha')
        self.assertEqual(self.bundle.read_text(), 'user file')

    def test_apply_rejects_directory_replaced_after_planning(self):
        legacy = self.create_legacy_link('alpha')
        self.bundle.symlink_to(self.source / 'skills')

        plan = self.plan_installation(self.source / 'skills', self.target, self.repo)
        self.bundle.unlink()
        self.bundle.mkdir()
        with self.assertRaises(ValueError):
            self.apply_link_migration(plan, self.repo)

        self.assertEqual(legacy.resolve(), self.source / 'skills/alpha')
        self.assertTrue(self.bundle.is_dir())

    def test_apply_preserves_links_tracked_after_planning(self):
        legacy = self.create_legacy_link('alpha')

        plan = self.plan_installation(self.source / 'skills', self.target, self.repo)
        self.bundle.symlink_to(self.source / 'skills')
        self.git('add', '.agents/skills/agent-skills')
        with self.assertRaises(ValueError):
            self.apply_link_migration(plan, self.repo)

        self.assertEqual(self.bundle.resolve(), self.source / 'skills')
        self.assertEqual(legacy.resolve(), self.source / 'skills/alpha')

    def test_apply_preserves_legacy_link_replaced_with_file_after_planning(self):
        legacy = self.create_legacy_link('alpha')
        plan = self.plan_installation(self.source / 'skills', self.target, self.repo)
        legacy.unlink()
        legacy.write_text('user file')

        with self.assertRaises(ValueError):
            self.apply_link_migration(plan, self.repo)

        self.assertEqual(legacy.read_text(), 'user file')
        self.assertEqual(self.bundle.resolve(), self.source / 'skills')

    def test_apply_preserves_legacy_link_tracked_after_planning(self):
        legacy = self.create_legacy_link('alpha')
        plan = self.plan_installation(self.source / 'skills', self.target, self.repo)
        self.git('add', '.agents/skills/alpha')

        with self.assertRaises(ValueError):
            self.apply_link_migration(plan, self.repo)

        self.assertEqual(legacy.resolve(), self.source / 'skills/alpha')
        self.assertEqual(self.bundle.resolve(), self.source / 'skills')
        self.assertEqual(self.git('ls-files', '--', '.agents/skills/alpha'), '.agents/skills/alpha')

    def test_literal_patterns(self):
        self.install(self.repo / '[private]* skills')

        self.assertTrue(self.ignored('[private]* skills/agent-skills'))
        self.assertFalse(self.ignored('p skills/agent-skills'))

    def test_repository_name_with_trailing_space(self):
        renamed = self.root / 'project '
        self.repo.rename(renamed)
        self.set_project_paths(renamed)

        self.install()

        self.assertEqual(self.bundle.resolve(), self.source / 'skills')
        self.assertTrue(self.ignored('.agents/skills/agent-skills'))
        first = self.exclude.read_bytes()
        self.install()
        self.assertEqual(self.exclude.read_bytes(), first)

    def test_exclude_file_preserves_line_endings_outside_managed_block(self):
        prefix = b'# user\r\nprivate.txt\r\n'
        suffix = b'# suffix\r\n*.log\r\n'
        managed_block = (
            b'# BEGIN agent-skills\r\n'
            b'/another/skills/agent-skills\r\n'
            b'# END agent-skills\r\n'
        )
        for managed in [b'', managed_block]:
            with self.subTest(managed=bool(managed)):
                self.exclude.write_bytes(prefix + managed + suffix)
                self.install()
                updated = self.exclude.read_bytes()
                self.assertTrue(updated.startswith(prefix))
                self.assertIn(suffix, updated)
                self.assertTrue(self.ignored('private.txt'))
                self.assertTrue(self.ignored('debug.log'))
                self.assertTrue(self.ignored('.agents/skills/agent-skills'))
                if managed:
                    self.assertTrue(updated.endswith(suffix))
                    self.assertTrue(self.ignored('another/skills/agent-skills'))
                self.install()
                self.assertEqual(self.exclude.read_bytes(), updated)

    def test_malformed_block_stops_without_writes(self):
        original = '# BEGIN agent-skills\n'
        self.exclude.write_text(original)

        self.install(success=False)

        self.assertFalse(self.target.exists())
        self.assertEqual(self.exclude.read_text(), original)

    def test_ignore_exception_stops_before_links(self):
        (self.repo / '.gitignore').write_text('!/.agents/skills/agent-skills\n')

        self.install(success=False)

        self.assertFalse(self.target.exists())

    def test_non_git_destination(self):
        target = self.root / 'home/skills'

        self.install(target)

        self.assertEqual({p.name for p in target.iterdir()}, {'agent-skills'})
        self.assertTrue((target / 'agent-skills/alpha/SKILL.md').exists())
        self.assertFalse((self.root / 'home/.git').exists())

    def test_missing_destination_does_not_write(self):
        for arguments in [[], [''], ['one', 'two']]:
            result = subprocess.run([sys.executable, str(self.source / 'scripts/install.py'), *arguments],
                                    cwd=self.root, env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)

        self.assertFalse((self.root / 'one').exists())

    def test_source_directory_cannot_be_destination(self):
        self.install(self.source / 'skills/nested', success=False)

        self.assertFalse((self.source / 'skills/nested').exists())

    def test_worktree_uses_common_exclude(self):
        (self.repo / 'README.md').write_text('project')
        self.git('add', 'README.md')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'initial')
        worktree = self.root / 'worktree'
        self.git('worktree', 'add', '--detach', str(worktree))

        self.install(worktree / '.agents/skills')

        self.assertTrue(self.ignored('.agents/skills/agent-skills', repo=worktree))
        self.assertIn('# BEGIN agent-skills', self.exclude.read_text())


class RenderExcludeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.render = staticmethod(runpy.run_path(str(INSTALL_SCRIPT))['render_exclude'])

    def test_no_change_preserves_line_endings(self):
        original = (
            '# user\r\n'
            '# BEGIN agent-skills\r\n'
            '/keep\r\n'
            '# END agent-skills\r\n'
        )

        self.assertEqual(self.render(original, ['/keep']), original)

    def test_append_preserves_existing_text_and_handles_missing_newline(self):
        block = (
            '\n'
            '# BEGIN agent-skills\n'
            '/new\n'
            '# END agent-skills\n'
        )
        for original in ['', '# user\n', '# user']:
            with self.subTest(original=original):
                separator = '\n' if original and not original.endswith('\n') else ''
                self.assertEqual(self.render(original, ['/new']), original + separator + block)
                self.assertEqual(self.render(original, [], ['/old']), original)

    def test_only_managed_patterns_are_changed(self):
        prefix = '# user\r\n/remove\r\n'
        suffix = '# user suffix\r\n/remove\r\n'
        original = prefix + (
            '# BEGIN agent-skills\n'
            '/remove\n'
            '/keep\n'
            '# END agent-skills\n'
        ) + suffix
        updated = self.render(original, ['/keep', '/new'], ['/remove'])

        self.assertEqual(updated, prefix + (
            '# BEGIN agent-skills\n'
            '/keep\n'
            '/new\n'
            '# END agent-skills\n'
        ) + suffix)
        self.assertEqual(self.render(updated, ['/keep', '/new']), updated)

    def test_invalid_markers_are_rejected_without_file_operations(self):
        for original in [
            '# BEGIN agent-skills\n',
            ('# END agent-skills\n'
             '# BEGIN agent-skills\n'),
            ('# BEGIN agent-skills\n'
             '# BEGIN agent-skills\n'
             '# END agent-skills\n'),
        ]:
            with self.subTest(original=original), self.assertRaises(ValueError):
                self.render(original, ['/new'])


if __name__ == '__main__':
    unittest.main()
