"""Tests for backend/coding_runtime/files.py (Claude-owned coding MVP file store).

Covers path validation, atomic/hash-guarded writes, exact edits, bounded listing and
search, the grep subprocess deadline, symlink/hardlink/special-file refusal, real
descriptor races, and the snapshot transport in both directions.
"""
import base64
import errno
import hashlib
import importlib.util
import os
import re
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('coding_files', SOURCE / 'backend/coding_runtime/files.py')
files = importlib.util.module_from_spec(spec)
spec.loader.exec_module(files)
FileStore, FileStoreError = files.FileStore, files.FileStoreError
sha = lambda data: hashlib.sha256(data).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name).resolve()
        self.root = self.area / 'workspace'
        self.root.mkdir()
        self.outside = self.area / 'outside'
        self.outside.mkdir()
        (self.outside / 'file.txt').write_text('OUTSIDE\n')
        (self.outside / 'secret.txt').write_text('OUTSIDE_TOKEN\n')
        self.store = FileStore(self.root)

    def put(self, rel, text):
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def code(self, skill_id, args):
        with self.assertRaises(FileStoreError) as caught:
            self.store.execute(skill_id, args)
        return caught.exception.code

    def listing(self):
        return sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob('*'))

    def leftovers(self):
        return sorted(entry.name for entry in self.area.iterdir()
                      if entry.name.startswith(('.hms-stage-', '.hms-retired-')))


class Construction(Base):
    def test_relative_root_is_refused(self):
        with self.assertRaises(FileStoreError) as caught:
            FileStore(Path('workspace'))
        self.assertEqual(caught.exception.code, 'invalid_root')

    def test_missing_root_is_refused(self):
        with self.assertRaises(FileStoreError) as caught:
            FileStore(self.root / 'absent')
        self.assertEqual(caught.exception.code, 'not_found')

    def test_file_as_root_is_refused(self):
        target = self.put('plain.txt', 'x\n')
        with self.assertRaises(FileStoreError) as caught:
            FileStore(target)
        self.assertEqual(caught.exception.code, 'invalid_root')


class Paths(Base):
    def setUp(self):
        super().setUp()
        self.put('keep.txt', 'kept\n')

    def test_traversal_is_refused(self):
        for candidate in ('../file.txt', 'pkg/../../outside/file.txt', '..', 'a/b/../../..'):
            self.assertEqual(self.code('fs.read', {'path': candidate}), 'traversal', candidate)

    def test_absolute_and_home_paths_are_refused(self):
        self.assertEqual(self.code('fs.read', {'path': str(self.outside / 'file.txt')}), 'absolute_path')
        self.assertEqual(self.code('fs.read', {'path': '/etc/hosts'}), 'absolute_path')
        self.assertEqual(self.code('fs.read', {'path': '~/notes.txt'}), 'absolute_path')
        self.assertEqual(self.code('fs.read', {'path': 'C:/windows/win.ini'}), 'absolute_path')

    def test_backslash_is_not_a_separator(self):
        self.assertEqual(self.code('fs.read', {'path': 'pkg\\app.py'}), 'invalid_path')

    def test_control_characters_are_refused(self):
        for candidate in ('keep\n.txt', 'keep\x00.txt', 'keep\x1b[0m.txt', 'keep\x7f.txt'):
            self.assertEqual(self.code('fs.read', {'path': candidate}), 'control_character')

    def test_long_paths_are_refused(self):
        self.assertEqual(self.code('fs.read', {'path': 'a/' * 118 + 'b.txt'}), 'path_too_long')
        self.assertEqual(self.code('fs.read', {'path': '/'.join('x' * 40)}), 'path_too_deep')

    def test_internal_temp_prefix_is_reserved(self):
        self.assertEqual(self.code('fs.write', {'path': '.hms-tmp-abc', 'content': 'x'}), 'reserved_name')

    def test_credential_and_private_paths_are_refused(self):
        candidates = (
            '.env', '.env.local', '.env.production', '.ssh/id_rsa', '.ssh', 'id_rsa',
            'deploy.pem', 'server.key', 'store.p12', '.npmrc', '.netrc', '.pypirc',
            '.aws/credentials', '.gnupg/secring.gpg', '.docker/config.json',
            '.git/config', '.git', '.git-credentials', '.pgpass', '.vault-token',
            'secrets.yaml', 'app/secret_key.py', 'config/credentials.json',
            'auth/password_hash.py', 'etc/passwd', 'keys/id_ed25519.pub',
        )
        for candidate in candidates:
            self.assertEqual(self.code('fs.read', {'path': candidate}), 'credential_path', candidate)

    def test_public_github_config_is_allowed(self):
        self.put('.github/workflows/ci.yml', 'name: ci\n')
        result = self.store.execute('fs.read', {'path': '.github/workflows/ci.yml'})
        self.assertEqual(result['content'], 'name: ci\n')

    def test_dot_and_empty_path_mean_the_root(self):
        for candidate in ('.', '', './'):
            self.assertEqual(self.store.execute('fs.list', {'path': candidate})['path'], '.')

    def test_root_itself_cannot_be_read_or_written(self):
        self.assertEqual(self.code('fs.read', {'path': '.'}), 'is_a_directory')
        self.assertEqual(self.code('fs.write', {'path': '.', 'content': 'x'}), 'invalid_path')

    def test_non_string_path_is_refused(self):
        self.assertEqual(self.code('fs.read', {'path': 7}), 'invalid_path')
        self.assertEqual(self.code('fs.read', {'path': None}), 'invalid_path')


class ReadRange(Base):
    def setUp(self):
        super().setUp()
        self.text = 'one\ntwo\nthree\nfour\n'
        self.put('doc.txt', self.text)

    def test_full_read_is_byte_faithful(self):
        result = self.store.execute('fs.read', {'path': 'doc.txt'})
        self.assertEqual(result['content'], self.text)
        self.assertEqual(result['sha256'], sha(self.text.encode()))
        self.assertEqual((result['total_lines'], result['start_line'], result['end_line']), (4, 1, 4))
        self.assertFalse(result['truncated'])

    def test_range_keeps_line_terminators(self):
        result = self.store.execute('fs.read', {'path': 'doc.txt', 'start_line': 2, 'end_line': 3})
        self.assertEqual(result['content'], 'two\nthree\n')
        self.assertTrue(result['truncated'])
        self.assertEqual(result['sha256'], sha(self.text.encode()), 'hash must cover the whole file')

    def test_range_without_final_newline(self):
        self.put('tail.txt', 'a\nb')
        result = self.store.execute('fs.read', {'path': 'tail.txt', 'start_line': 2})
        self.assertEqual(result['content'], 'b')
        self.assertEqual(result['total_lines'], 2)

    def test_range_beyond_end_is_clamped(self):
        result = self.store.execute('fs.read', {'path': 'doc.txt', 'start_line': 4, 'end_line': 99})
        self.assertEqual((result['content'], result['end_line']), ('four\n', 4))

    def test_empty_file(self):
        self.put('empty.txt', '')
        result = self.store.execute('fs.read', {'path': 'empty.txt'})
        self.assertEqual((result['content'], result['total_lines'], result['start_line']), ('', 0, 0))

    def test_invalid_ranges(self):
        self.assertEqual(self.code('fs.read', {'path': 'doc.txt', 'start_line': 0}), 'invalid_range')
        self.assertEqual(self.code('fs.read', {'path': 'doc.txt', 'start_line': -1}), 'invalid_range')
        self.assertEqual(self.code('fs.read', {'path': 'doc.txt', 'start_line': 3, 'end_line': 2}), 'invalid_range')
        self.assertEqual(self.code('fs.read', {'path': 'doc.txt', 'start_line': True}), 'invalid_range')
        self.assertEqual(self.code('fs.read', {'path': 'doc.txt', 'start_line': '2'}), 'invalid_range')

    def test_binary_and_oversize_are_refused(self):
        (self.root / 'blob.txt').write_bytes(b'\xff\xfe\x00bad')
        self.assertEqual(self.code('fs.read', {'path': 'blob.txt'}), 'binary_file')
        (self.root / 'big.txt').write_bytes(b'a' * (files.MAX_FILE_BYTES + 1))
        self.assertEqual(self.code('fs.read', {'path': 'big.txt'}), 'file_too_large')

    def test_missing_and_directory_targets(self):
        self.assertEqual(self.code('fs.read', {'path': 'nope.txt'}), 'not_found')
        (self.root / 'pkg').mkdir()
        self.assertEqual(self.code('fs.read', {'path': 'pkg'}), 'is_a_directory')
        self.assertEqual(self.code('fs.read', {'path': 'doc.txt/inner'}), 'not_a_directory')

    def test_code_read_is_the_same_operation(self):
        result = self.store.execute('code.read', {'path': 'doc.txt'})
        self.assertEqual(result['skill_id'], 'code.read')
        self.assertEqual(result['content'], self.text)


class Writes(Base):
    def test_create_is_the_default_and_refuses_to_replace(self):
        result = self.store.execute('fs.write', {'path': 'pkg/new.py', 'content': 'x = 1\n'})
        self.assertTrue(result['created'])
        self.assertEqual((self.root / 'pkg/new.py').read_text(), 'x = 1\n')
        self.assertEqual(result['sha256'], sha(b'x = 1\n'))
        self.assertEqual(self.code('fs.write', {'path': 'pkg/new.py', 'content': 'x = 2\n'}), 'exists')
        self.assertEqual((self.root / 'pkg/new.py').read_text(), 'x = 1\n')

    def test_created_file_is_not_group_or_world_writable(self):
        self.store.execute('fs.write', {'path': 'mode.txt', 'content': 'x\n'})
        mode = stat.S_IMODE(os.stat(str(self.root / 'mode.txt')).st_mode)
        self.assertEqual(mode & 0o022, 0, oct(mode))

    def test_overwrite_requires_the_expected_hash(self):
        self.put('doc.txt', 'first\n')
        self.assertEqual(self.code('fs.write', {'path': 'doc.txt', 'content': 'second\n',
                                                'overwrite': True}), 'expected_sha256_required')
        self.assertEqual(self.code('fs.write', {'path': 'doc.txt', 'content': 'second\n',
                                                'overwrite': True,
                                                'expected_sha256': sha(b'wrong')}), 'sha256_conflict')
        self.assertEqual((self.root / 'doc.txt').read_text(), 'first\n')
        result = self.store.execute('fs.write', {'path': 'doc.txt', 'content': 'second\n',
                                                 'overwrite': True, 'expected_sha256': sha(b'first\n')})
        self.assertFalse(result['created'])
        self.assertTrue(result['overwrote'])
        self.assertEqual((self.root / 'doc.txt').read_text(), 'second\n')

    def test_expected_hash_without_overwrite_is_refused(self):
        self.put('doc.txt', 'first\n')
        self.assertEqual(self.code('fs.write', {'path': 'doc.txt', 'content': 'x\n',
                                                'expected_sha256': sha(b'first\n')}), 'invalid_argument')

    def test_malformed_hash_is_refused(self):
        self.put('doc.txt', 'first\n')
        for candidate in ('deadbeef', sha(b'x').upper() + 'ff', 12, 'z' * 64):
            self.assertEqual(self.code('fs.write', {'path': 'doc.txt', 'content': 'x\n',
                                                    'overwrite': True,
                                                    'expected_sha256': candidate}), 'invalid_hash', candidate)

    def test_uppercase_hash_is_accepted(self):
        self.put('doc.txt', 'first\n')
        self.store.execute('fs.write', {'path': 'doc.txt', 'content': 'next\n', 'overwrite': True,
                                        'expected_sha256': sha(b'first\n').upper()})
        self.assertEqual((self.root / 'doc.txt').read_text(), 'next\n')

    def test_overwrite_of_missing_file_reports_not_found(self):
        self.assertEqual(self.code('fs.write', {'path': 'gone.txt', 'content': 'x', 'overwrite': True,
                                                'expected_sha256': sha(b'x')}), 'not_found')

    def test_bad_argument_types(self):
        self.assertEqual(self.code('fs.write', {'path': 'a.txt', 'content': 5}), 'invalid_content')
        self.assertEqual(self.code('fs.write', {'path': 'a.txt', 'content': 'x',
                                                'overwrite': 'yes'}), 'invalid_argument')

    def test_content_over_the_file_limit_is_refused(self):
        payload = 'a' * (files.MAX_FILE_BYTES + 1)
        self.assertEqual(self.code('fs.write', {'path': 'big.txt', 'content': payload}), 'file_too_large')
        self.assertFalse((self.root / 'big.txt').exists())

    def test_no_temporary_file_survives_a_write(self):
        self.store.execute('fs.write', {'path': 'pkg/a.txt', 'content': 'x\n'})
        self.assertEqual(self.listing(), ['pkg', 'pkg/a.txt'])

    def test_parent_directories_are_created_but_bounded(self):
        self.store.execute('fs.write', {'path': 'a/b/c/d.txt', 'content': 'x\n'})
        self.assertTrue((self.root / 'a/b/c/d.txt').exists())
        deep = '/'.join('d%d' % index for index in range(files.MAX_MKDIR_DEPTH + 1)) + '/x.txt'
        self.assertEqual(self.code('fs.write', {'path': deep, 'content': 'x'}), 'path_too_deep')

    def test_file_count_cap_blocks_new_files(self):
        store = FileStore(self.root, max_files=3)
        for index in range(3):
            store.execute('fs.write', {'path': 'f%d.txt' % index, 'content': 'x\n'})
        with self.assertRaises(FileStoreError) as caught:
            store.execute('fs.write', {'path': 'f3.txt', 'content': 'x\n'})
        self.assertEqual(caught.exception.code, 'too_many_files')
        store.execute('fs.write', {'path': 'f0.txt', 'content': 'y\n', 'overwrite': True,
                                   'expected_sha256': sha(b'x\n')})


class Edits(Base):
    def setUp(self):
        super().setUp()
        self.text = 'alpha\nbeta\ngamma\nbeta\n'
        self.put('doc.txt', self.text)

    def test_exact_unique_edit(self):
        result = self.store.execute('code.edit', {'path': 'doc.txt', 'old': 'gamma', 'new': 'delta'})
        self.assertEqual(result['replacements'], 1)
        self.assertEqual(result['sha256_before'], sha(self.text.encode()))
        self.assertEqual((self.root / 'doc.txt').read_text(), 'alpha\nbeta\ndelta\nbeta\n')
        self.assertEqual(result['sha256'], sha(b'alpha\nbeta\ndelta\nbeta\n'))

    def test_multi_line_edit(self):
        self.store.execute('code.edit', {'path': 'doc.txt', 'old': 'alpha\nbeta\n', 'new': 'ALPHA\n'})
        self.assertEqual((self.root / 'doc.txt').read_text(), 'ALPHA\ngamma\nbeta\n')

    def test_ambiguous_edit_is_refused(self):
        self.assertEqual(self.code('code.edit', {'path': 'doc.txt', 'old': 'beta', 'new': 'x'}),
                         'match_not_unique')
        self.assertEqual((self.root / 'doc.txt').read_text(), self.text)

    def test_missing_match_is_refused(self):
        self.assertEqual(self.code('code.edit', {'path': 'doc.txt', 'old': 'omega', 'new': 'x'}),
                         'match_not_found')
        self.assertEqual((self.root / 'doc.txt').read_text(), self.text)

    def test_empty_and_identical_old_are_refused(self):
        self.assertEqual(self.code('code.edit', {'path': 'doc.txt', 'old': '', 'new': 'x'}), 'empty_match')
        self.assertEqual(self.code('code.edit', {'path': 'doc.txt', 'old': 'beta', 'new': 'beta'}),
                         'invalid_argument')

    def test_stale_expected_hash_is_a_conflict(self):
        self.store.execute('code.edit', {'path': 'doc.txt', 'old': 'gamma', 'new': 'delta'})
        self.assertEqual(self.code('code.edit', {'path': 'doc.txt', 'old': 'alpha', 'new': 'x',
                                                 'expected_sha256': sha(self.text.encode())}),
                         'sha256_conflict')
        self.assertEqual((self.root / 'doc.txt').read_text(), 'alpha\nbeta\ndelta\nbeta\n')

    def test_matching_expected_hash_is_accepted(self):
        self.store.execute('code.edit', {'path': 'doc.txt', 'old': 'gamma', 'new': 'delta',
                                         'expected_sha256': sha(self.text.encode())})
        self.assertEqual((self.root / 'doc.txt').read_text(), 'alpha\nbeta\ndelta\nbeta\n')

    def test_non_string_arguments_are_refused(self):
        self.assertEqual(self.code('code.edit', {'path': 'doc.txt', 'old': 1, 'new': 'x'}),
                         'invalid_argument')

    def test_edit_of_binary_file_is_refused(self):
        (self.root / 'blob.bin').write_bytes(b'\xff\x00')
        self.assertEqual(self.code('code.edit', {'path': 'blob.bin', 'old': 'a', 'new': 'b'}),
                         'binary_file')


class Links(Base):
    def test_symlinked_final_component_is_refused(self):
        os.symlink(str(self.outside / 'file.txt'), str(self.root / 'escape.txt'))
        self.assertEqual(self.code('fs.read', {'path': 'escape.txt'}), 'symlink_rejected')

    def test_symlinked_directory_component_is_refused(self):
        os.symlink(str(self.outside), str(self.root / 'away'))
        self.assertEqual(self.code('fs.read', {'path': 'away/file.txt'}), 'symlink_rejected')
        self.assertEqual(self.code('fs.list', {'path': 'away'}), 'symlink_rejected')
        self.assertEqual(self.code('fs.tree', {'path': 'away'}), 'symlink_rejected')

    def test_relative_symlink_loop_is_refused(self):
        os.symlink('loop', str(self.root / 'loop'))
        self.assertEqual(self.code('fs.read', {'path': 'loop'}), 'symlink_rejected')

    def test_write_never_follows_a_symlink_out_of_the_workspace(self):
        target = self.outside / 'file.txt'
        os.symlink(str(target), str(self.root / 'escape.txt'))
        self.assertEqual(self.code('fs.write', {'path': 'escape.txt', 'content': 'pwned\n'}), 'exists')
        self.assertEqual(self.code('fs.write', {'path': 'escape.txt', 'content': 'pwned\n',
                                                'overwrite': True,
                                                'expected_sha256': sha(b'OUTSIDE\n')}), 'symlink_rejected')
        self.assertEqual(target.read_text(), 'OUTSIDE\n')

    def test_write_never_creates_a_file_through_a_dangling_symlink(self):
        os.symlink(str(self.outside / 'new.txt'), str(self.root / 'dangle.txt'))
        self.assertEqual(self.code('fs.write', {'path': 'dangle.txt', 'content': 'pwned\n'}), 'exists')
        self.assertFalse((self.outside / 'new.txt').exists())

    def test_write_through_a_symlinked_parent_is_refused(self):
        os.symlink(str(self.outside), str(self.root / 'away'))
        self.assertEqual(self.code('fs.write', {'path': 'away/new.txt', 'content': 'pwned\n'}),
                         'symlink_rejected')
        self.assertFalse((self.outside / 'new.txt').exists())

    def test_edit_through_a_symlink_is_refused(self):
        os.symlink(str(self.outside / 'file.txt'), str(self.root / 'escape.txt'))
        self.assertEqual(self.code('code.edit', {'path': 'escape.txt', 'old': 'OUTSIDE', 'new': 'x'}),
                         'symlink_rejected')
        self.assertEqual((self.outside / 'file.txt').read_text(), 'OUTSIDE\n')

    def test_hardlinked_files_are_refused(self):
        self.put('original.txt', 'shared\n')
        os.link(str(self.root / 'original.txt'), str(self.root / 'clone.txt'))
        self.assertEqual(self.code('fs.read', {'path': 'original.txt'}), 'hardlinked_file')
        self.assertEqual(self.code('code.edit', {'path': 'clone.txt', 'old': 'shared', 'new': 'x'}),
                         'hardlinked_file')
        reasons = {item['name']: item['reason'] for item in self.store.execute('fs.list', {})['skipped']}
        self.assertEqual(reasons, {'original.txt': 'hardlinked_file', 'clone.txt': 'hardlinked_file'})

    def test_special_files_are_refused(self):
        os.mkfifo(str(self.root / 'pipe'))
        self.assertEqual(self.code('fs.read', {'path': 'pipe'}), 'special_file')
        self.assertEqual(self.code('code.edit', {'path': 'pipe', 'old': 'a', 'new': 'b'}), 'special_file')
        entry = self.store.execute('fs.list', {})['skipped']
        self.assertEqual(entry, [{'name': 'pipe', 'reason': 'not_a_regular_entry'}])

    def test_listing_marks_links_as_skipped(self):
        self.put('real.txt', 'x\n')
        os.symlink(str(self.outside / 'file.txt'), str(self.root / 'escape.txt'))
        result = self.store.execute('fs.list', {})
        self.assertEqual([item['name'] for item in result['entries']], ['real.txt'])
        self.assertEqual(result['skipped'], [{'name': 'escape.txt', 'reason': 'not_a_regular_entry'}])

    def test_glob_and_search_never_cross_a_symlink(self):
        self.put('pkg/app.py', 'value = 1\n')
        os.symlink(str(self.outside), str(self.root / 'away'))
        os.symlink(str(self.outside / 'file.txt'), str(self.root / 'escape.py'))
        self.assertEqual(self.store.execute('fs.glob', {'pattern': '**/*.py'})['paths'], ['pkg/app.py'])
        self.assertEqual(self.store.execute('fs.glob', {'pattern': 'away/*'})['paths'], [])
        found = self.store.execute('code.grep', {'pattern': 'OUTSIDE'})
        self.assertEqual(found['matches'], [])


class Races(Base):
    """Real concurrent mutation of the path being resolved."""

    def test_directory_component_flipped_to_a_symlink_never_leaks(self):
        (self.root / 'real').mkdir()
        (self.root / 'real/file.txt').write_text('INSIDE\n')
        (self.outside / 'dir').mkdir()
        (self.outside / 'dir/file.txt').write_text('OUTSIDE\n')
        os.symlink(str(self.outside / 'dir'), str(self.root / 'spare'))
        stop = threading.Event()
        trouble = []

        def flip():
            try:
                while not stop.is_set():
                    os.rename(str(self.root / 'real'), str(self.root / 'dir'))
                    time.sleep(0.0002)
                    os.rename(str(self.root / 'dir'), str(self.root / 'real'))
                    os.rename(str(self.root / 'spare'), str(self.root / 'dir'))
                    time.sleep(0.0002)
                    os.rename(str(self.root / 'dir'), str(self.root / 'spare'))
            except OSError as exc:  # pragma: no cover - only on an unexpected layout
                trouble.append(exc)

        worker = threading.Thread(target=flip)
        worker.start()
        seen = {'inside': 0, 'rejected': 0}
        try:
            for _ in range(600):
                try:
                    result = self.store.execute('fs.read', {'path': 'dir/file.txt'})
                except FileStoreError as exc:
                    self.assertIn(exc.code, ('symlink_rejected', 'not_found', 'not_a_directory'), exc.code)
                    seen['rejected'] += 1
                    continue
                self.assertEqual(result['content'], 'INSIDE\n', 'read escaped the workspace')
                seen['inside'] += 1
        finally:
            stop.set()
            worker.join(5)
        self.assertEqual(trouble, [])
        self.assertGreater(seen['inside'], 0, 'the in-root state was never observed')
        self.assertGreater(seen['rejected'], 0, 'the symlink state was never observed; '
                                                'the race did not interleave')

    def test_final_component_flipped_to_a_symlink_never_leaks(self):
        (self.root / 'spare.txt').write_text('INSIDE\n')
        os.symlink(str(self.outside / 'file.txt'), str(self.root / 'link.txt'))
        stop = threading.Event()

        def flip():
            while not stop.is_set():
                os.rename(str(self.root / 'spare.txt'), str(self.root / 'doc.txt'))
                os.rename(str(self.root / 'doc.txt'), str(self.root / 'spare.txt'))
                os.rename(str(self.root / 'link.txt'), str(self.root / 'doc.txt'))
                os.rename(str(self.root / 'doc.txt'), str(self.root / 'link.txt'))

        worker = threading.Thread(target=flip)
        worker.start()
        try:
            for _ in range(600):
                try:
                    content = self.store.execute('fs.read', {'path': 'doc.txt'})['content']
                except FileStoreError as exc:
                    self.assertIn(exc.code, ('symlink_rejected', 'not_found'), exc.code)
                    continue
                self.assertEqual(content, 'INSIDE\n')
        finally:
            stop.set()
            worker.join(5)

    def test_overwrite_detects_a_change_made_after_the_hash_check(self):
        """The guard between reading the hash and renaming the replacement in."""
        self.put('doc.txt', 'first\n')
        original = self.store._read_file
        calls = []

        def racing_read(segments, limit=None):
            data, info = original(segments, limit)
            calls.append(segments)
            if len(calls) == 1:
                (self.root / 'doc.txt').write_text('changed by someone else\n')
            return data, info

        self.store._read_file = racing_read
        with self.assertRaises(FileStoreError) as caught:
            self.store.execute('fs.write', {'path': 'doc.txt', 'content': 'mine\n',
                                            'overwrite': True, 'expected_sha256': sha(b'first\n')})
        self.assertEqual(caught.exception.code, 'sha256_conflict')
        self.assertEqual((self.root / 'doc.txt').read_text(), 'changed by someone else\n')

    def test_edit_detects_a_change_made_after_the_match(self):
        self.put('doc.txt', 'alpha\n')
        original = self.store._read_file
        calls = []

        def racing_read(segments, limit=None):
            data, info = original(segments, limit)
            calls.append(segments)
            if len(calls) == 1:
                (self.root / 'doc.txt').write_text('alpha\nextra\n')
            return data, info

        self.store._read_file = racing_read
        with self.assertRaises(FileStoreError) as caught:
            self.store.execute('code.edit', {'path': 'doc.txt', 'old': 'alpha', 'new': 'beta'})
        self.assertEqual(caught.exception.code, 'sha256_conflict')
        self.assertEqual((self.root / 'doc.txt').read_text(), 'alpha\nextra\n')

    def test_create_loses_the_link_race_instead_of_clobbering(self):
        original = self.store._stage

        def racing_stage(dfd, data, rel):
            name = original(dfd, data, rel)
            (self.root / 'doc.txt').write_text('written by someone else\n')
            return name

        self.store._stage = racing_stage
        with self.assertRaises(FileStoreError) as caught:
            self.store.execute('fs.write', {'path': 'doc.txt', 'content': 'mine\n'})
        self.assertEqual(caught.exception.code, 'exists')
        self.assertEqual((self.root / 'doc.txt').read_text(), 'written by someone else\n')
        self.assertEqual(self.listing(), ['doc.txt'], 'the staged temp file must be cleaned up')


class ListTreeGlob(Base):
    def setUp(self):
        super().setUp()
        self.put('README.md', '# readme\n')
        self.put('pkg/app.py', 'value = 1\n')
        self.put('pkg/util/helper.py', 'def helper():\n    return 2\n')
        self.put('pkg/util/notes.txt', 'notes\n')
        self.put('.env', 'TOKEN=shh\n')
        self.put('node_modules/dep/index.js', 'module.exports = 1;\n')

    def test_list_is_sorted_and_hides_credentials(self):
        result = self.store.execute('fs.list', {})
        self.assertEqual([item['name'] for item in result['entries']],
                         ['README.md', 'node_modules', 'pkg'])
        self.assertEqual(result['skipped'], [{'name': '.env', 'reason': 'credential_path'}])
        self.assertEqual([item['kind'] for item in result['entries']], ['file', 'dir', 'dir'])
        self.assertEqual(result['entries'][0]['bytes'], len('# readme\n'))

    def test_list_of_a_subdirectory(self):
        result = self.store.execute('fs.list', {'path': 'pkg/util'})
        self.assertEqual([item['path'] for item in result['entries']],
                         ['pkg/util/helper.py', 'pkg/util/notes.txt'])

    def test_list_of_a_file_is_refused(self):
        self.assertEqual(self.code('fs.list', {'path': 'README.md'}), 'not_a_directory')

    def test_list_truncates_at_the_file_limit(self):
        store = FileStore(self.root, max_files=2)
        result = store.execute('fs.list', {})
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['entries']), 2)

    def test_tree_depth_is_bounded(self):
        shallow = self.store.execute('fs.tree', {'depth': 1})
        self.assertTrue(all('children' not in node for node in shallow['entries']))
        elided = [node for node in shallow['entries'] if node['name'] == 'pkg'][0]
        self.assertTrue(elided['children_elided'])
        deep = self.store.execute('fs.tree', {'depth': 3})
        pkg = [node for node in deep['entries'] if node['name'] == 'pkg'][0]
        util = [node for node in pkg['children'] if node['name'] == 'util'][0]
        self.assertEqual([node['name'] for node in util['children']], ['helper.py', 'notes.txt'])
        self.assertEqual(deep['nodes'], 9)

    def test_tree_rejects_depth_outside_the_contract(self):
        for candidate in (0, -1, files.MAX_TREE_DEPTH + 1, 'three', True):
            self.assertEqual(self.code('fs.tree', {'depth': candidate}), 'invalid_argument', candidate)

    def test_tree_omits_credential_paths_and_caps_nodes(self):
        rendered = repr(self.store.execute('fs.tree', {'depth': 6}))
        self.assertNotIn('.env', rendered)
        self.assertNotIn('TOKEN', rendered)
        store = FileStore(self.root, max_files=3)
        self.assertTrue(store.execute('fs.tree', {'depth': 6})['truncated'])

    def test_glob_is_deterministic_and_relative(self):
        self.assertEqual(self.store.execute('fs.glob', {'pattern': '*.md'})['paths'], ['README.md'])
        self.assertEqual(self.store.execute('fs.glob', {'pattern': 'pkg/*.py'})['paths'], ['pkg/app.py'])
        self.assertEqual(self.store.execute('fs.glob', {'pattern': '**/*.py'})['paths'],
                         ['pkg/app.py', 'pkg/util/helper.py'])
        self.assertEqual(self.store.execute('fs.glob', {'pattern': 'pkg/**'})['paths'],
                         ['pkg/app.py', 'pkg/util/helper.py', 'pkg/util/notes.txt'])
        self.assertEqual(self.store.execute('fs.glob', {'pattern': 'pkg/util/[hn]*'})['paths'],
                         ['pkg/util/helper.py', 'pkg/util/notes.txt'])

    def test_glob_returns_files_only(self):
        self.assertEqual(self.store.execute('fs.glob', {'pattern': 'pkg/*'})['paths'], ['pkg/app.py'])

    def test_glob_is_not_a_regex(self):
        self.put('pkg/a.py', 'x\n')
        self.assertEqual(self.store.execute('fs.glob', {'pattern': 'pkg/.*'})['paths'], [])
        self.assertEqual(self.store.execute('fs.glob', {'pattern': 'pkg/a.py|pkg/app.py'})['paths'], [])
        self.assertEqual(self.code('fs.glob', {'pattern': 'pkg/.*\\.py'}), 'invalid_path')

    def test_glob_refuses_escape_and_credential_patterns(self):
        self.assertEqual(self.code('fs.glob', {'pattern': '../**'}), 'traversal')
        self.assertEqual(self.code('fs.glob', {'pattern': '/etc/*'}), 'absolute_path')
        self.assertEqual(self.code('fs.glob', {'pattern': '.env*'}), 'credential_path')
        self.assertEqual(self.store.execute('fs.glob', {'pattern': '*'})['paths'], ['README.md'])
        self.assertEqual(self.store.execute('fs.glob', {'pattern': '**'})['count'], 5)

    def test_glob_rejects_empty_patterns(self):
        self.assertEqual(self.code('fs.glob', {'pattern': ''}), 'invalid_pattern')
        self.assertEqual(self.code('fs.glob', {'pattern': '.'}), 'invalid_pattern')
        self.assertEqual(self.code('fs.glob', {'pattern': 3}), 'invalid_pattern')

    def test_glob_truncates_at_the_file_limit(self):
        store = FileStore(self.root, max_files=2)
        result = store.execute('fs.glob', {'pattern': '**'})
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['paths']), 2)


class Search(Base):
    def setUp(self):
        super().setUp()
        self.put('pkg/app.py', 'import json\n\nTOTAL = 3\n\n\nclass Widget:\n'
                               '    def render(self, widget):\n        return widget\n')
        self.put('web/app.ts', 'export function render(x: number) { return x; }\n'
                               'const render2 = 1;\n')
        self.put('svc/main.go', 'package main\n\nfunc render() int { return 1 }\n')
        self.put('notes.md', 'render is documented here\n')

    def test_python_results_come_from_the_ast(self):
        result = self.store.execute('code.search', {'query': 'Widget', 'path': 'pkg'})
        self.assertEqual([(hit['line'], hit['kind'], hit['analysis']) for hit in result['matches']],
                         [(6, 'class', 'python_ast')])
        self.assertIn('python_ast', result['analysis'])

    def test_python_kinds_cover_definitions_imports_and_assignments(self):
        kinds = lambda query: sorted({hit['kind'] for hit in
                                      self.store.execute('code.search', {'query': query, 'path': 'pkg'})['matches']})
        self.assertEqual(kinds('json'), ['import'])
        self.assertEqual(kinds('TOTAL'), ['assignment'])
        self.assertEqual(kinds('widget'), ['parameter', 'reference'])

    def test_unknown_symbol_has_no_matches(self):
        self.assertEqual(self.store.execute('code.search', {'query': 'absent_symbol'})['matches'], [])

    def test_best_effort_languages_are_labelled(self):
        result = self.store.execute('code.search', {'query': 'render'})
        analyses = {hit['path']: hit['analysis'] for hit in result['matches']}
        self.assertEqual(analyses['web/app.ts'], 'best_effort_regex')
        self.assertEqual(analyses['svc/main.go'], 'best_effort_regex')
        self.assertEqual(analyses['pkg/app.py'], 'python_ast')
        self.assertEqual(analyses['notes.md'], 'literal')
        self.assertIn('best-effort', result['note'])
        self.assertIn('best_effort_regex', result['analysis'])

    def test_unparsable_python_falls_back_and_says_so(self):
        self.put('broken/bad.py', 'def oops(:\n')
        result = self.store.execute('code.search', {'query': 'oops', 'path': 'broken'})
        self.assertEqual([hit['analysis'] for hit in result['matches']], ['best_effort_text'])
        self.assertTrue(any('parse failed' in note for note in result['warnings']))

    def test_search_skips_credential_files_and_vendor_directories(self):
        self.put('.env', 'TOKEN=supersecretvalue\n')
        self.put('node_modules/dep/index.js', 'const render = 1;\n')
        result = self.store.execute('code.search', {'query': 'render'})
        self.assertEqual([hit for hit in result['matches'] if 'node_modules' in hit['path']], [])
        token = self.store.execute('code.search', {'query': 'supersecretvalue'})
        self.assertEqual(token['matches'], [])
        self.assertTrue(any('credential' in note for note in token['warnings']))

    def test_search_results_are_sorted(self):
        matches = self.store.execute('code.search', {'query': 'render'})['matches']
        keys = [(hit['path'], hit['line'], hit['column']) for hit in matches]
        self.assertEqual(keys, sorted(keys))

    def test_search_match_cap(self):
        body = '\n'.join('target_%d = target' % index for index in range(400))
        self.put('many/big.py', 'target = 1\n' + body + '\n')
        result = self.store.execute('code.search', {'query': 'target', 'path': 'many'})
        self.assertEqual(len(result['matches']), files.SEARCH_MAX_MATCHES)
        self.assertTrue(result['truncated'])

    def test_invalid_queries(self):
        self.assertEqual(self.code('code.search', {'query': ''}), 'invalid_query')
        self.assertEqual(self.code('code.search', {'query': 'x' * 129}), 'invalid_query')
        self.assertEqual(self.code('code.search', {'query': 5}), 'invalid_query')
        self.assertEqual(self.code('code.search', {'query': 'a\nb'}), 'control_character')


class Grep(Base):
    def setUp(self):
        super().setUp()
        self.put('pkg/app.py', 'alpha = 1\nbeta = 2\n')
        self.put('notes.md', 'alpha and omega\n')

    def test_regex_search(self):
        result = self.store.execute('code.grep', {'pattern': r'^(alpha|beta) = \d$'})
        self.assertEqual([(hit['path'], hit['line'], hit['column']) for hit in result['matches']],
                         [('pkg/app.py', 1, 1), ('pkg/app.py', 2, 1)])
        self.assertEqual(result['engine'], 'python_re_isolated_subprocess')

    def test_literal_search_does_not_interpret_metacharacters(self):
        self.put('pkg/re.py', 'value = "a.c"\n')
        literal = self.store.execute('code.grep', {'pattern': 'a.c', 'literal': True, 'path': 'pkg'})
        self.assertEqual([hit['path'] for hit in literal['matches']], ['pkg/re.py'])
        loose = self.store.execute('code.grep', {'pattern': 'a.c', 'path': 'pkg'})
        self.assertEqual(len(loose['matches']), 1)

    def test_scoped_to_a_subdirectory(self):
        result = self.store.execute('code.grep', {'pattern': 'alpha', 'path': 'pkg'})
        self.assertEqual([hit['path'] for hit in result['matches']], ['pkg/app.py'])

    def test_catastrophic_pattern_hits_the_deadline(self):
        self.put('bomb/payload.txt', 'a' * 4000 + '\n')
        started = time.time()
        with self.assertRaises(FileStoreError) as caught:
            self.store.execute('code.grep', {'pattern': r'(a+)+$b', 'path': 'bomb'})
        elapsed = time.time() - started
        self.assertEqual(caught.exception.code, 'grep_timeout')
        self.assertLess(elapsed, 10.0, 'the deadline must bound the match loop')
        self.assertGreaterEqual(elapsed, files.GREP_DEADLINE)

    def test_invalid_regex_is_reported_not_raised_in_process(self):
        self.assertEqual(self.code('code.grep', {'pattern': '('}), 'invalid_pattern')
        self.assertEqual(self.code('code.grep', {'pattern': '[z-a]'}), 'invalid_pattern')

    def test_pattern_validation(self):
        self.assertEqual(self.code('code.grep', {'pattern': ''}), 'invalid_pattern')
        self.assertEqual(self.code('code.grep', {'pattern': 'a' * (files.GREP_MAX_PATTERN + 1)}),
                         'invalid_pattern')
        self.assertEqual(self.code('code.grep', {'pattern': 'a\x00b'}), 'control_character')
        self.assertEqual(self.code('code.grep', {'pattern': 'a', 'literal': 'yes'}), 'invalid_argument')

    def test_match_cap_and_truncation(self):
        self.put('many/hits.txt', 'hit\n' * 300)
        result = self.store.execute('code.grep', {'pattern': 'hit', 'path': 'many'})
        self.assertEqual(len(result['matches']), files.GREP_MAX_MATCHES)
        self.assertTrue(result['truncated'])

    def test_long_lines_are_clipped(self):
        self.put('wide/line.txt', 'x' * 5000 + '\n')
        hit = self.store.execute('code.grep', {'pattern': 'x', 'path': 'wide'})['matches'][0]
        self.assertEqual(len(hit['text']), 400)

    def test_credential_files_are_never_grepped(self):
        self.put('.env', 'TOKEN=supersecretvalue\n')
        self.put('deploy.pem', 'supersecretvalue\n')
        self.put('pkg/other.py', 'noise\n')
        result = self.store.execute('code.grep', {'pattern': 'supersecretvalue'})
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['files_scanned'], 3)
        self.assertTrue(all('credential' in note for note in result['warnings']))

    def test_runs_out_of_process(self):
        """The match loop must not be able to hang or corrupt the caller."""
        import subprocess as sub
        seen = {}
        original = sub.run

        def record(argv, **kwargs):
            seen['argv'] = argv
            seen['cwd'] = kwargs.get('cwd')
            seen['env'] = kwargs.get('env')
            seen['timeout'] = kwargs.get('timeout')
            return original(argv, **kwargs)

        files.subprocess.run = record
        self.addCleanup(setattr, files.subprocess, 'run', original)
        self.store.execute('code.grep', {'pattern': 'alpha'})
        self.assertEqual(seen['argv'][:2], [files.sys.executable, '-I'])
        self.assertLess(seen['timeout'], 2.0)
        self.assertNotIn(str(self.root), str(seen['cwd']))
        self.assertEqual(sorted(seen['env']), ['LC_ALL', 'PATH'])
        self.assertEqual(sorted(os.listdir(seen['cwd'])), [])


class Dispatch(Base):
    def test_every_contract_skill_is_served(self):
        self.assertEqual(self.store.skills(),
                         ['code.edit', 'code.grep', 'code.read', 'code.search', 'code.write',
                          'fs.glob', 'fs.list', 'fs.read', 'fs.tree', 'fs.write'])

    def test_results_carry_the_requested_skill_id(self):
        self.put('doc.txt', 'x\n')
        self.assertEqual(self.store.execute('code.read', {'path': 'doc.txt'})['skill_id'], 'code.read')
        self.assertEqual(self.store.execute('fs.read', {'path': 'doc.txt'})['skill_id'], 'fs.read')
        result = self.store.execute('code.write', {'path': 'new.txt', 'content': 'y\n'})
        self.assertEqual(result['skill_id'], 'code.write')

    def test_unknown_skill_is_refused(self):
        for candidate in ('exec.shell', 'git.commit', 'fs.delete', 'fs.read ', '', None, 7):
            self.assertEqual(self.code(candidate, {}), 'unknown_skill', candidate)

    def test_unknown_arguments_are_refused_not_ignored(self):
        self.put('doc.txt', 'x\n')
        self.assertEqual(self.code('fs.write', {'path': 'a.txt', 'content': 'x',
                                                'overwite': True}), 'unknown_argument')
        self.assertEqual(self.code('fs.read', {'path': 'doc.txt', 'depth': 2}), 'unknown_argument')

    def test_missing_arguments_are_refused(self):
        self.assertEqual(self.code('fs.read', {}), 'missing_argument')
        self.assertEqual(self.code('fs.write', {'path': 'a.txt'}), 'missing_argument')
        self.assertEqual(self.code('code.edit', {'path': 'a.txt', 'old': 'x'}), 'missing_argument')
        self.assertEqual(self.code('fs.glob', {}), 'missing_argument')

    def test_arguments_must_be_a_mapping(self):
        self.assertEqual(self.code('fs.list', []), 'invalid_arguments')
        self.assertEqual(self.code('fs.list', 'path=.'), 'invalid_arguments')
        self.assertEqual(self.store.execute('fs.list', None)['path'], '.')

    def test_error_payload_is_structured(self):
        with self.assertRaises(FileStoreError) as caught:
            self.store.execute('fs.read', {'path': '.env'})
        payload = caught.exception.as_dict()
        self.assertEqual(payload['error'], 'credential_path')
        self.assertEqual(payload['path'], '.env')

    def test_errors_never_echo_file_contents(self):
        self.put('doc.txt', 'TOKEN=supersecretvalue\n')
        with self.assertRaises(FileStoreError) as caught:
            self.store.execute('fs.write', {'path': 'doc.txt', 'content': 'x\n',
                                            'overwrite': True, 'expected_sha256': sha(b'nope' * 16)})
        self.assertNotIn('supersecretvalue', str(caught.exception))
        self.assertNotIn('supersecretvalue', repr(caught.exception.as_dict()))


class Snapshots(Base):
    def setUp(self):
        super().setUp()
        self.put('README.md', '# readme\n')
        self.put('pkg/app.py', 'value = 1\n')
        self.put('.env', 'TOKEN=supersecretvalue\n')
        self.put('.git/config', '[core]\n')
        self.put('.git/credentials', 'https://user:pw@host\n')
        self.put('node_modules/dep/index.js', 'module.exports = 1;\n')
        os.symlink(str(self.outside / 'file.txt'), str(self.root / 'escape.txt'))
        os.mkfifo(str(self.root / 'pipe'))

    def test_snapshot_is_a_base64_mapping_of_safe_relative_paths(self):
        snapshot = self.store.snapshot()
        self.assertEqual(sorted(snapshot), ['.git/config', 'README.md', 'pkg/app.py'])
        self.assertEqual(base64.b64decode(snapshot['pkg/app.py']), b'value = 1\n')
        for key, value in snapshot.items():
            self.assertFalse(key.startswith('/'))
            self.assertIsInstance(value, str)
            self.assertEqual(value, re.sub(r'[^A-Za-z0-9+/=]', '', value))

    def test_snapshot_omits_secrets_links_and_specials(self):
        rendered = repr(self.store.snapshot())
        self.assertNotIn('supersecretvalue', rendered)
        self.assertNotIn('user:pw', rendered)
        self.assertNotIn('escape.txt', rendered)
        self.assertNotIn('pipe', rendered)
        self.assertNotIn('node_modules', rendered)

    def test_snapshot_report_explains_what_it_left_out(self):
        report = self.store.snapshot_report()
        joined = ' '.join(report['warnings'])
        self.assertIn('.env', joined)
        self.assertIn('escape.txt', joined)
        self.assertIn('pipe', joined)
        self.assertIn('node_modules', joined)
        self.assertEqual(report['count'], 3)
        self.assertEqual(report['total_bytes'], len('# readme\n') + len('value = 1\n') + len('[core]\n'))
        self.assertTrue(report['include_git'])

    def test_include_git_false_drops_the_git_tree(self):
        self.assertEqual(sorted(self.store.snapshot(include_git=False)), ['README.md', 'pkg/app.py'])
        with self.assertRaises(FileStoreError) as caught:
            self.store.snapshot(include_git='yes')
        self.assertEqual(caught.exception.code, 'invalid_argument')

    def test_binary_content_round_trips_exactly(self):
        payload = bytes(range(256))
        (self.root / 'blob.bin').write_bytes(payload)
        self.assertEqual(base64.b64decode(self.store.snapshot()['blob.bin']), payload)

    def test_hardlinked_file_is_refused_by_the_transport(self):
        os.link(str(self.root / 'README.md'), str(self.root / 'README.copy'))
        with self.assertRaises(FileStoreError) as caught:
            self.store.snapshot()
        self.assertEqual(caught.exception.code, 'hardlinked_file')

    def test_bounds_are_enforced_rather_than_truncated(self):
        store = FileStore(self.root, max_files=2)
        with self.assertRaises(FileStoreError) as caught:
            store.snapshot()
        self.assertEqual(caught.exception.code, 'too_many_files')
        store = FileStore(self.root, max_bytes=10)
        with self.assertRaises(FileStoreError) as caught:
            store.snapshot()
        self.assertEqual(caught.exception.code, 'too_many_bytes')
        store = FileStore(self.root, max_file_bytes=5)
        with self.assertRaises(FileStoreError) as caught:
            store.snapshot()
        self.assertEqual(caught.exception.code, 'file_too_large')


class ReplaceSnapshot(Base):
    def setUp(self):
        super().setUp()
        self.put('README.md', '# readme\n')
        self.put('pkg/app.py', 'value = 1\n')
        self.put('pkg/old.py', 'remove me\n')
        self.put('.env', 'TOKEN=supersecretvalue\n')
        self.put('node_modules/dep/index.js', 'module.exports = 1;\n')
        os.symlink(str(self.outside / 'file.txt'), str(self.root / 'escape.txt'))

    def encoded(self, mapping):
        return {key: base64.b64encode(value).decode('ascii') for key, value in mapping.items()}

    def test_publishes_the_whole_file_set(self):
        result = self.store.replace_snapshot(self.encoded({
            'README.md': b'# replaced\n',
            'pkg/app.py': b'value = 2\n',
            'pkg/sub/new.py': b'value = 3\n',
        }))
        self.assertEqual(result['status'], 'published')
        self.assertEqual((result['files'], result['bytes']), (3, 31))
        self.assertEqual((self.root / 'README.md').read_text(), '# replaced\n')
        self.assertEqual((self.root / 'pkg/sub/new.py').read_text(), 'value = 3\n')
        self.assertFalse((self.root / 'pkg/old.py').exists())
        self.assertEqual(self.leftovers(), [])

    def test_untransportable_entries_survive_a_round_trip(self):
        snapshot = self.store.snapshot()
        snapshot['pkg/added.py'] = base64.b64encode(b'value = 9\n').decode('ascii')
        result = self.store.replace_snapshot(snapshot)
        self.assertEqual(result['preserved'], ['.env', 'escape.txt', 'node_modules'])
        self.assertEqual((self.root / '.env').read_text(), 'TOKEN=supersecretvalue\n')
        self.assertTrue((self.root / 'escape.txt').is_symlink())
        self.assertEqual((self.root / 'node_modules/dep/index.js').read_text(), 'module.exports = 1;\n')
        self.assertEqual((self.root / 'pkg/added.py').read_text(), 'value = 9\n')
        self.assertEqual(self.leftovers(), [])

    def test_nested_credentials_are_preserved_under_rebuilt_directories(self):
        self.put('pkg/.env', 'INNER=1\n')
        self.store.replace_snapshot(self.encoded({'other/file.txt': b'x\n'}))
        self.assertEqual((self.root / 'pkg/.env').read_text(), 'INNER=1\n')
        self.assertEqual((self.root / 'other/file.txt').read_text(), 'x\n')
        self.assertFalse((self.root / 'README.md').exists())

    def test_preservation_can_be_switched_off_explicitly(self):
        self.store.replace_snapshot(self.encoded({'README.md': b'# only\n'}),
                                    preserve_untransportable=False)
        self.assertEqual(self.listing(), ['README.md'])

    def test_root_identity_survives_and_the_store_keeps_working(self):
        before = os.stat(str(self.root)).st_ino
        self.store.replace_snapshot(self.encoded({'a.txt': b'one\n'}))
        self.assertNotEqual(os.stat(str(self.root)).st_ino, before,
                            'the generation should be swapped, not edited in place')
        self.assertEqual(self.store.root, self.root)
        self.assertEqual(self.store.execute('fs.read', {'path': 'a.txt'})['content'], 'one\n')
        self.store.execute('fs.write', {'path': 'b.txt', 'content': 'two\n'})
        self.assertEqual(sorted(self.store.snapshot()), ['a.txt', 'b.txt'])

    def test_published_files_are_regular_and_not_world_writable(self):
        self.store.replace_snapshot(self.encoded({'pkg/a.txt': b'x\n'}))
        info = os.stat(str(self.root / 'pkg/a.txt'))
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode) & 0o022, 0)
        self.assertEqual(info.st_nlink, 1)

    def test_empty_snapshot_empties_the_transportable_file_set(self):
        self.store.replace_snapshot({}, preserve_untransportable=False)
        self.assertEqual(self.listing(), [])

    def test_git_tree_may_be_published(self):
        self.store.replace_snapshot(self.encoded({'.git/config': b'[core]\n', 'a.txt': b'x\n'}))
        self.assertEqual((self.root / '.git/config').read_text(), '[core]\n')
        self.assertEqual(self.code('fs.read', {'path': '.git/config'}), 'credential_path')

    def rejects(self, snapshot, expected):
        before = self.listing()
        with self.assertRaises(FileStoreError) as caught:
            self.store.replace_snapshot(snapshot)
        self.assertEqual(caught.exception.code, expected)
        self.assertEqual(self.listing(), before, 'the workspace must be untouched')
        self.assertEqual(self.leftovers(), [], 'no staging directory may be left behind')

    def test_validation_happens_before_anything_is_published(self):
        good = base64.b64encode(b'ok\n').decode('ascii')
        self.rejects({'README.md': good, '../escape.txt': good}, 'traversal')
        self.rejects({'README.md': good, '/etc/hosts': good}, 'absolute_path')
        self.rejects({'README.md': good, 'a/../../b': good}, 'traversal')
        self.rejects({'README.md': good, '.ssh/id_rsa': good}, 'credential_path')
        self.rejects({'README.md': good, 'deploy.pem': good}, 'credential_path')
        self.rejects({'README.md': good, 'bad\x00name': good}, 'control_character')
        self.rejects({'README.md': good, 'x' * 250: good}, 'path_too_long')
        self.rejects({'README.md': good, 'broken.txt': 'not base64 !!'}, 'invalid_encoding')
        self.rejects({'README.md': good, 'wrong.txt': 12}, 'invalid_encoding')
        self.rejects({'README.md': good, '': good}, 'invalid_snapshot')
        self.rejects({'a': good, 'a/b': good}, 'path_conflict')
        self.rejects({'a.txt': good, './a.txt': good}, 'duplicate_path')
        self.rejects([('a.txt', good)], 'invalid_snapshot')
        self.rejects({'big.txt': base64.b64encode(b'a' * (files.MAX_FILE_BYTES + 1)).decode()},
                     'file_too_large')

    def test_count_and_byte_bounds_are_validated(self):
        payload = base64.b64encode(b'a' * 1024).decode('ascii')
        store = FileStore(self.root, max_files=4)
        with self.assertRaises(FileStoreError) as caught:
            store.replace_snapshot({'f%d.txt' % index: payload for index in range(5)})
        self.assertEqual(caught.exception.code, 'too_many_files')
        store = FileStore(self.root, max_bytes=2048)
        with self.assertRaises(FileStoreError) as caught:
            store.replace_snapshot({'f%d.txt' % index: payload for index in range(3)})
        self.assertEqual(caught.exception.code, 'too_many_bytes')
        self.assertEqual(self.leftovers(), [])

    def test_bytes_values_are_accepted(self):
        self.store.replace_snapshot({'a.txt': b'raw\n'})
        self.assertEqual((self.root / 'a.txt').read_text(), 'raw\n')

    def test_publish_failure_restores_the_previous_generation(self):
        original = files.os.rename
        calls = []

        def failing_rename(src, dst, **kwargs):
            calls.append((src, dst))
            if len(calls) == 2:
                raise OSError(errno.EIO, 'injected publish failure')
            return original(src, dst, **kwargs)

        files.os.rename = failing_rename
        self.addCleanup(setattr, files.os, 'rename', original)
        with self.assertRaises(FileStoreError) as caught:
            self.store.replace_snapshot({'a.txt': b'new\n'})
        files.os.rename = original
        self.assertEqual(caught.exception.code, 'publish_failed')
        self.assertEqual((self.root / 'README.md').read_text(), '# readme\n')
        self.assertEqual((self.root / '.env').read_text(), 'TOKEN=supersecretvalue\n')
        self.assertFalse((self.root / 'a.txt').exists())
        self.assertEqual(self.leftovers(), [])

    def test_snapshot_paths_are_never_followed_out_of_the_root(self):
        """A snapshot key may not reuse an existing symlink to write outside."""
        target = self.outside / 'file.txt'
        self.store.replace_snapshot({'escape.txt': b'pwned\n'}, preserve_untransportable=False)
        self.assertEqual(target.read_text(), 'OUTSIDE\n')
        self.assertFalse((self.root / 'escape.txt').is_symlink())
        self.assertEqual((self.root / 'escape.txt').read_text(), 'pwned\n')


if __name__ == '__main__':
    unittest.main()
