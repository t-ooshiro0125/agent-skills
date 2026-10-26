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
from unittest.mock import patch

from git_test_environment import isolate_git_environment


INSTALL_SCRIPT = Path(__file__).resolve().parent.parent / 'scripts/install.py'


class InstallSkillsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        installer = runpy.run_path(str(INSTALL_SCRIPT))
        cls.plan_installation = staticmethod(installer['plan_installation'])
        cls.apply_link_migration = staticmethod(installer['apply_link_migration'])

    def setUp(self):
        self.env = isolate_git_environment(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
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

    def install(self, target=None, success=True, skills=(), remove=False):
        result = subprocess.run([sys.executable, str(self.source / 'scripts/install.py'), str(target or self.target),
                                 *(['--remove'] if remove else []), *skills],
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

    def assert_registered(self, names, target=None):
        target = target or self.target
        for name in names:
            self.assertTrue((target / name).is_symlink())
            self.assertEqual((target / name).resolve(), self.source / 'skills' / name)
            self.assertEqual((target / name / 'SKILL.md').read_text(),
                             (self.source / 'skills' / name / 'SKILL.md').read_text())

    def test_registers_individual_links_and_preserves_user_rules(self):
        original = '# User rules\nprivate.txt\n'
        self.exclude.write_text(original)
        (self.source / 'skills/alpha/support.txt').write_text('support')

        self.install()

        self.assert_registered(['alpha', 'beta', 'gamma'])
        self.assertFalse(self.bundle.exists())
        self.assertEqual((self.target / 'alpha/support.txt').read_text(), 'support')
        self.assertTrue(self.exclude.read_text().startswith(original))
        self.assertTrue(self.ignored('private.txt'))
        for name in ['alpha', 'beta', 'gamma']:
            self.assertTrue(self.ignored('.agents/skills/' + name))
        self.assertFalse(self.ignored('.agents/skills/other'))

    def test_repository_only_installer_is_preserved_and_not_distributed(self):
        local = self.target / 'install-skills'
        local.mkdir(parents=True)
        (local / 'SKILL.md').write_text('local')

        self.install()

        self.assertEqual((local / 'SKILL.md').read_text(), 'local')
        self.assertFalse(self.ignored('.agents/skills/install-skills/SKILL.md'))
        self.install(skills=['install-skills'], success=False)

    def test_selects_only_named_skills_without_duplicates(self):
        self.install(skills=['gamma', 'alpha', 'alpha'])

        self.assertEqual({p.name for p in self.target.iterdir()}, {'alpha', 'gamma'})
        self.assert_registered(['alpha', 'gamma'])
        self.assertFalse(self.ignored('.agents/skills/beta'))
        self.assertEqual(self.exclude.read_text().count('/.agents/skills/alpha\n'), 1)

    def test_dependencies_remain_readable_without_registering_them(self):
        (self.source / 'skills/alpha/SKILL.md').write_text('[beta](../beta/SKILL.md)')
        (self.source / 'skills/beta/SKILL.md').write_text('[gamma](../gamma/SKILL.md)')

        self.install(skills=['alpha'])

        self.assertFalse((self.target / 'beta').exists())
        self.assertFalse((self.target / 'gamma').exists())
        self.assertEqual((self.target / 'alpha/../beta/SKILL.md').read_text(),
                         '[gamma](../gamma/SKILL.md)')
        self.assertEqual((self.target / 'alpha/../gamma/SKILL.md').read_text(), 'gamma')

    def test_unknown_and_invalid_names_are_detected_before_writes(self):
        before = self.exclude.read_bytes()
        for name in ['missing', '../alpha', '.', '/alpha', 'install-skills', 'agent-skills']:
            with self.subTest(name=name):
                self.install(skills=['alpha', name], success=False)
                self.assertFalse(self.target.exists())
                self.assertEqual(self.exclude.read_bytes(), before)

    def test_new_skills_require_reregistration_but_content_updates_do_not(self):
        self.install()
        (self.source / 'skills/alpha/SKILL.md').write_text('updated')
        self.add_skill('delta')

        self.assertEqual((self.target / 'alpha/SKILL.md').read_text(), 'updated')
        self.assertFalse((self.target / 'delta').exists())
        self.install()
        self.assert_registered(['delta'])

    def test_all_and_selected_registration_preserve_unspecified_links_and_excludes(self):
        self.install(skills=['alpha'])
        self.assertFalse((self.target / 'beta').exists())
        self.install()
        original_exclude = self.exclude.read_bytes()
        original_links = {p.name: (os.readlink(p), p.lstat().st_ino) for p in self.target.iterdir()}

        self.install(skills=['alpha'])
        self.install()

        self.assertEqual(self.exclude.read_bytes(), original_exclude)
        self.assertEqual({p.name: (os.readlink(p), p.lstat().st_ino) for p in self.target.iterdir()},
                         original_links)

    def test_another_destination_preserves_previous_exclusion(self):
        self.install()
        another = self.repo / '.claude/skills'
        self.install(another)

        self.assert_registered(['alpha', 'beta', 'gamma'], another)
        self.assertTrue(self.ignored('.claude/skills/alpha'))
        self.assertTrue(self.ignored('.agents/skills/alpha'))
        self.assertEqual(self.exclude.read_text().count('# BEGIN agent-skills'), 1)

    def create_bundle(self):
        self.target.mkdir(parents=True, exist_ok=True)
        self.bundle.symlink_to(self.source / 'skills')

    def test_migration_preserves_existing_links_and_unrelated_excludes(self):
        self.create_bundle()
        existing = self.create_legacy_link('alpha')
        original_inode = existing.lstat().st_ino
        prefix = '# User rules\n*.log\n'
        suffix = '# User suffix\n'
        self.exclude.write_text(prefix + '# BEGIN agent-skills\n'
                                '/.agents/skills/agent-skills\n'
                                '/.agents/skills/alpha\n'
                                '/another/skills/agent-skills\n'
                                '# END agent-skills\n' + suffix)

        self.install()

        self.assert_registered(['alpha', 'beta', 'gamma'])
        self.assertEqual(existing.lstat().st_ino, original_inode)
        self.assertFalse(self.bundle.is_symlink())
        updated = self.exclude.read_text()
        self.assertNotIn('/.agents/skills/agent-skills\n', updated)
        self.assertIn('/another/skills/agent-skills\n', updated)
        self.assertTrue(updated.startswith(prefix))
        self.assertTrue(updated.endswith(suffix))

    def test_selected_registration_migrates_whole_bundle_to_preserve_available_skills(self):
        self.create_bundle()

        self.install(skills=['alpha'])

        self.assert_registered(['alpha', 'beta', 'gamma'])
        self.assertFalse(self.bundle.is_symlink())

    def test_link_creation_failure_preserves_bundle_until_migration_can_complete(self):
        self.create_bundle()
        plan = self.plan_installation(self.source / 'skills', self.target, self.repo)
        original_symlink_to = Path.symlink_to

        def fail_second_link(path, *args, **kwargs):
            if path == self.target / 'beta':
                raise OSError('simulated link creation failure')
            return original_symlink_to(path, *args, **kwargs)

        with patch.object(Path, 'symlink_to', fail_second_link), redirect_stdout(StringIO()):
            with self.assertRaisesRegex(OSError, 'simulated link creation failure'):
                self.apply_link_migration(plan, self.repo)

        self.assert_registered(['alpha'])
        self.assertFalse((self.target / 'beta').exists())
        self.assertTrue(self.bundle.is_symlink())
        self.assertEqual(self.bundle.resolve(), self.source / 'skills')
        self.assertEqual((self.bundle / 'beta/SKILL.md').read_text(), 'beta')

        self.install()

        self.assert_registered(['alpha', 'beta', 'gamma'])
        self.assertFalse(self.bundle.is_symlink())

    def test_missing_bundle_loses_stale_rule_without_hiding_future_items(self):
        self.create_legacy_link('gamma')
        prefix = '# User rules\n*.log\n'
        suffix = '# User suffix\nprivate.txt\n'
        self.exclude.write_text(prefix + '# BEGIN agent-skills\n'
                                '/.agents/skills/agent-skills\n'
                                '/.agents/skills/gamma\n'
                                '/another/skills/agent-skills\n'
                                '# END agent-skills\n' + suffix)

        self.install(skills=['alpha'])

        self.assert_registered(['alpha', 'gamma'])
        self.assertFalse((self.target / 'beta').exists())
        updated = self.exclude.read_text()
        self.assertNotIn('/.agents/skills/agent-skills\n', updated)
        self.assertIn('/.agents/skills/gamma\n', updated)
        self.assertIn('/another/skills/agent-skills\n', updated)
        self.assertTrue(updated.startswith(prefix))
        self.assertTrue(updated.endswith(suffix))
        self.bundle.write_text('unrelated file')
        self.assertIn('.agents/skills/agent-skills',
                      self.git('status', '--porcelain', '--untracked-files=all'))
        self.install(skills=['alpha'])
        self.assertEqual(self.bundle.read_text(), 'unrelated file')
        self.assertEqual(self.exclude.read_text(), updated)

    def test_conflict_during_migration_keeps_bundle_and_other_links(self):
        self.create_bundle()
        (self.target / 'beta').write_text('user file')

        self.install(success=False)

        self.assertEqual((self.target / 'beta').read_text(), 'user file')
        self.assertEqual(self.bundle.resolve(), self.source / 'skills')
        self.assertFalse((self.target / 'alpha').exists())

    def test_foreign_bundle_is_not_deleted_or_replaced(self):
        self.target.mkdir(parents=True)
        self.bundle.symlink_to(self.root / 'missing')
        self.exclude.write_text('# BEGIN agent-skills\n/.agents/skills/agent-skills\n# END agent-skills\n')

        self.install()

        self.assertEqual(os.readlink(self.bundle), str(self.root / 'missing'))
        self.assert_registered(['alpha', 'beta', 'gamma'])
        self.assertFalse(self.ignored('.agents/skills/agent-skills'))

    def test_tracked_bundle_is_preserved_and_stale_rule_is_removed(self):
        self.create_bundle()
        self.git('add', '.agents/skills/agent-skills')
        self.exclude.write_text('# BEGIN agent-skills\n/.agents/skills/agent-skills\n# END agent-skills\n')

        self.install(success=False)

        self.assertEqual(self.bundle.resolve(), self.source / 'skills')
        self.assertFalse((self.target / 'alpha').exists())
        self.assertNotIn('/.agents/skills/agent-skills\n', self.exclude.read_text())

    def test_existing_files_directories_and_foreign_links_are_protected(self):
        for kind in ['file', 'directory', 'foreign-link', 'tracked-link']:
            with self.subTest(kind=kind):
                target = self.repo / kind / 'skills'
                target.mkdir(parents=True)
                conflict = target / 'beta'
                if kind == 'file':
                    conflict.write_text('user file')
                elif kind == 'directory':
                    conflict.mkdir()
                    (conflict / 'SKILL.md').write_text('user skill')
                else:
                    conflict.symlink_to(self.source / 'skills/beta' if kind == 'tracked-link'
                                        else self.root / 'missing')
                if kind == 'tracked-link':
                    self.git('add', str(conflict))
                self.install(target, success=False)
                self.assertFalse((target / 'alpha').exists())
                self.assertTrue(conflict.exists() or conflict.is_symlink())
                if kind == 'file':
                    self.assertEqual(conflict.read_text(), 'user file')
                elif kind == 'directory':
                    self.assertEqual((conflict / 'SKILL.md').read_text(), 'user skill')
                else:
                    self.assertEqual(os.readlink(conflict), str(self.source / 'skills/beta'
                                     if kind == 'tracked-link' else self.root / 'missing'))

    def test_conflicting_selected_link_loses_only_its_stale_rule(self):
        self.install()
        (self.target / 'alpha').unlink()
        (self.target / 'alpha').write_text('user file')

        self.install(skills=['alpha'], success=False)

        self.assertEqual((self.target / 'alpha').read_text(), 'user file')
        self.assertFalse(self.ignored('.agents/skills/alpha'))
        self.assertTrue(self.ignored('.agents/skills/beta'))

    def test_unselected_foreign_item_and_its_rule_are_untouched(self):
        self.target.mkdir(parents=True)
        (self.target / 'beta').write_text('user file')
        self.exclude.write_text('# BEGIN agent-skills\n/.agents/skills/beta\n# END agent-skills\n')

        self.install(skills=['alpha'])

        self.assertEqual((self.target / 'beta').read_text(), 'user file')
        self.assertTrue(self.ignored('.agents/skills/beta'))

    def test_planning_is_silent_and_does_not_change_files(self):
        self.create_bundle()
        before = self.exclude.read_bytes()
        output = StringIO()

        with redirect_stdout(output):
            plan = self.plan_installation(self.source / 'skills', self.target, self.repo)

        self.assertEqual(output.getvalue(), '')
        self.assertEqual(self.exclude.read_bytes(), before)
        self.assertTrue(self.bundle.is_symlink())
        self.assertFalse((self.target / 'alpha').exists())
        self.assertTrue(plan.migrate_bundle)

    def test_apply_protects_targets_changed_after_planning(self):
        for kind in ['file', 'directory', 'foreign-link', 'tracked-link']:
            with self.subTest(kind=kind):
                target = self.repo / kind / 'skills'
                plan = self.plan_installation(self.source / 'skills', target, self.repo)
                target.mkdir(parents=True)
                changed = target / 'beta'
                if kind == 'file':
                    changed.write_text('user')
                elif kind == 'directory':
                    changed.mkdir()
                else:
                    changed.symlink_to(self.source / 'skills/beta' if kind == 'tracked-link'
                                       else self.root / 'missing')
                if kind == 'tracked-link':
                    self.git('add', str(changed))
                with self.assertRaises(ValueError):
                    self.apply_link_migration(plan, self.repo)
                self.assertFalse((target / 'alpha').exists())
                self.assertTrue(changed.exists() or changed.is_symlink())

    def test_apply_protects_bundle_changed_after_planning(self):
        for kind in ['file', 'directory', 'foreign-link', 'tracked-link']:
            with self.subTest(kind=kind):
                target = self.repo / kind / 'skills'
                target.mkdir(parents=True)
                bundle = target / 'agent-skills'
                bundle.symlink_to(self.source / 'skills')
                plan = self.plan_installation(self.source / 'skills', target, self.repo)
                if kind == 'tracked-link':
                    self.git('add', str(bundle))
                else:
                    bundle.unlink()
                    if kind == 'file':
                        bundle.write_text('user')
                    elif kind == 'directory':
                        bundle.mkdir()
                    else:
                        bundle.symlink_to(self.root / 'missing')
                with self.assertRaises(ValueError):
                    self.apply_link_migration(plan, self.repo)
                self.assertFalse((target / 'alpha').exists())
                self.assertTrue(bundle.exists() or bundle.is_symlink())

    def test_apply_rejects_bundle_added_after_planning(self):
        plan = self.plan_installation(self.source / 'skills', self.target, self.repo, ['alpha'])
        self.create_bundle()

        with self.assertRaises(ValueError):
            self.apply_link_migration(plan, self.repo)

        self.assertTrue(self.bundle.is_symlink())
        self.assertFalse((self.target / 'alpha').exists())

    def test_remove_only_explicit_links_and_their_managed_rules(self):
        source_skill = self.source / 'skills/alpha'
        support = source_skill / 'references/details.md'
        support.parent.mkdir()
        support.write_text('supporting content')
        self.install()
        self.exclude.write_text(self.exclude.read_text() + '# User rule\n/.agents/skills/alpha\n')

        self.install(skills=['alpha'], remove=True)

        self.assertFalse((self.target / 'alpha').is_symlink())
        self.assertEqual((source_skill / 'SKILL.md').read_text(), 'alpha')
        self.assertEqual(support.read_text(), 'supporting content')
        self.assert_registered(['beta', 'gamma'])
        updated = self.exclude.read_bytes()
        self.assertTrue(updated.endswith(b'# User rule\n/.agents/skills/alpha\n'))
        self.assertEqual(updated.count(b'/.agents/skills/alpha\n'), 1)
        self.assertTrue(self.ignored('.agents/skills/beta'))
        self.install(skills=['alpha'], remove=True)
        self.assertEqual(self.exclude.read_bytes(), updated)
        self.assertEqual((source_skill / 'SKILL.md').read_text(), 'alpha')
        self.assertEqual(support.read_text(), 'supporting content')

    def test_remove_protects_foreign_and_tracked_items_before_any_deletion(self):
        for kind in ['file', 'directory', 'foreign-link', 'tracked-link']:
            with self.subTest(kind=kind):
                target = self.repo / kind / 'skills'
                self.install(target)
                beta = target / 'beta'
                if kind == 'tracked-link':
                    self.git('add', '-f', str(beta))
                else:
                    beta.unlink()
                    if kind == 'file':
                        beta.write_text('user')
                    elif kind == 'directory':
                        beta.mkdir()
                    else:
                        beta.symlink_to(self.root / 'missing')
                self.install(target, skills=['alpha', 'beta'], remove=True, success=False)
                self.assertTrue((target / 'alpha').is_symlink())
                self.assertTrue(beta.exists() or beta.is_symlink())

    def test_remove_after_source_skill_was_deleted(self):
        self.install()
        shutil.rmtree(self.source / 'skills/alpha')

        self.install(skills=['alpha'], remove=True)

        self.assertFalse((self.target / 'alpha').is_symlink())
        self.assertFalse(self.ignored('.agents/skills/alpha'))
        self.assert_registered(['beta', 'gamma'])

    def test_remove_requires_bundle_migration_first(self):
        self.create_bundle()
        before = self.exclude.read_bytes()

        self.install(skills=['alpha'], remove=True, success=False)

        self.assertTrue(self.bundle.is_symlink())
        self.assertEqual(self.exclude.read_bytes(), before)

    def test_remove_revalidates_ownership_after_planning(self):
        self.install()
        plan = self.plan_installation(self.source / 'skills', self.target, self.repo,
                                      ['alpha', 'beta'], remove=True)
        (self.target / 'beta').unlink()
        (self.target / 'beta').write_text('user')

        with self.assertRaises(ValueError):
            self.apply_link_migration(plan, self.repo)

        self.assertTrue((self.target / 'alpha').is_symlink())
        self.assertEqual((self.target / 'beta').read_text(), 'user')

    def test_literal_patterns(self):
        target = self.repo / '[private]* skills'
        self.install(target)

        self.assertTrue(self.ignored('[private]* skills/alpha'))
        self.assertFalse(self.ignored('p skills/alpha'))

    def test_repository_name_with_trailing_space(self):
        renamed = self.root / 'project '
        self.repo.rename(renamed)
        self.set_project_paths(renamed)

        self.install()

        self.assert_registered(['alpha', 'beta', 'gamma'])
        self.assertTrue(self.ignored('.agents/skills/alpha'))
        first = self.exclude.read_bytes()
        self.install()
        self.assertEqual(self.exclude.read_bytes(), first)

    def test_exclude_preserves_line_endings_outside_managed_block(self):
        prefix = b'# user\r\nprivate.txt\r\n'
        suffix = b'# suffix\r\n*.log\r\n'
        managed_block = (b'# BEGIN agent-skills\r\n'
                         b'/another/skills/alpha\r\n'
                         b'# END agent-skills\r\n')
        for managed in [b'', managed_block]:
            with self.subTest(managed=bool(managed)):
                self.exclude.write_bytes(prefix + managed + suffix)
                self.install()
                updated = self.exclude.read_bytes()
                self.assertTrue(updated.startswith(prefix))
                self.assertIn(suffix, updated)
                self.assertTrue(self.ignored('private.txt'))
                self.assertTrue(self.ignored('debug.log'))
                self.assertTrue(self.ignored('.agents/skills/alpha'))
                if managed:
                    self.assertTrue(updated.endswith(suffix))
                    self.assertTrue(self.ignored('another/skills/alpha'))
                self.install()
                self.assertEqual(self.exclude.read_bytes(), updated)

    def test_malformed_block_stops_without_writes(self):
        original = '# BEGIN agent-skills\n'
        self.exclude.write_text(original)

        self.install(success=False)

        self.assertFalse(self.target.exists())
        self.assertEqual(self.exclude.read_text(), original)

    def test_malformed_block_prevents_removal(self):
        self.install()
        self.exclude.write_text('# BEGIN agent-skills\n')

        self.install(skills=['alpha'], remove=True, success=False)

        self.assertTrue((self.target / 'alpha').is_symlink())

    def test_ignore_exception_stops_before_links_and_preserves_bundle(self):
        self.create_bundle()
        (self.repo / '.gitignore').write_text('!/.agents/skills/beta\n')

        self.install(success=False)

        self.assertTrue(self.bundle.is_symlink())
        self.assertFalse((self.target / 'alpha').exists())

    def test_non_git_destinations_for_both_agents(self):
        for agent in ['.agents', '.claude']:
            with self.subTest(agent=agent):
                target = self.root / 'home' / agent / 'skills'
                self.install(target)
                self.assertEqual({p.name for p in target.iterdir()}, {'alpha', 'beta', 'gamma'})
                self.assert_registered(['alpha', 'beta', 'gamma'], target)
        self.assertFalse((self.root / 'home/.git').exists())

    def test_invalid_cli_arguments_do_not_write(self):
        for arguments in [[], [''], ['one', '--remove'], ['one', 'alpha', '--remove', 'beta']]:
            result = subprocess.run([sys.executable, str(self.source / 'scripts/install.py'), *arguments],
                                    cwd=self.root, env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
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

        self.assertTrue(self.ignored('.agents/skills/alpha', repo=worktree))
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
    from test_copy_install import CopyInstallTest
    unittest.main()
