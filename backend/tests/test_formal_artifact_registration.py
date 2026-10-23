"""Offline regression for the publication -> accepted registration -> publication sequence."""
import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.file_services.workspace_fs import WorkspaceFS
from ai_system.sandbox.artifacts import capture_run_files
from ai_system.sandbox.evidence import seal_run


class FormalArtifactRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        environment = patch.dict(os.environ, {'PA_DATA_PATH': str(base / 'state')})
        environment.start()
        self.addCleanup(environment.stop)
        self.source = WorkspaceFS(base / 'project/experiments/attempt/workspace')
        self.target = WorkspaceFS(base / 'project')
        self.source.init_structure()
        self.target.init_structure()
        self.run = self.source.create_run()
        self.source.save_run_input(self.run, 'print(1)')
        self.source.save_run_stdout(self.run, '1\n')
        self.source.save_run_stderr(self.run, '')
        self.artifact = self.source.register_run_artifact(self.run, 'outputs/chart.png')
        self.source.write_text(self.artifact, 'frozen chart bytes')
        self.source.finish_run(self.run, 'success')
        seal_run(self.source, self.run)
        self.path = f'outputs/research/{self.run}/outputs/chart.png'
        self.digest = hashlib.sha256(b'frozen chart bytes').hexdigest()

    def publish(self):
        return capture_run_files(self.source, self.target, run_ids=[self.run])

    def test_accepted_registration_and_republication_keep_archive_binding(self):
        self.publish()
        # Same call made by research.control.execute after a published acceptance.
        self.target.register_file(self.path, kind='image', source_run=self.run, visibility='user')
        self.assertIn(self.path, self.publish())
        reloaded = WorkspaceFS(self.target.root)
        binding = reloaded.get_file_meta(self.path)
        self.assertEqual(binding['archive_path'], self.artifact)
        self.assertEqual(binding['sha256'], self.digest)
        # Preview and compiler input selection use this canonical resolver.
        reloaded.abs(self.path).write_text('edited presentation copy')
        self.assertEqual(reloaded.canonical_artifact(self.path).read_bytes(), b'frozen chart bytes')

    def test_metadata_only_registration_retains_source(self):
        self.publish()
        self.target.register_file(self.path, kind='image', created_by='agent', visibility='user')
        self.assertEqual(self.target.get_file_meta(self.path)['source_run'], self.run)
        self.assertEqual(self.target.canonical_artifact(self.path).read_bytes(), b'frozen chart bytes')

    def test_rebinding_is_rejected_without_modifying_manifest(self):
        self.publish()
        before = copy.deepcopy(self.target._manifest)
        for change in ({'source_run': 'run_other'}, {'sha256': '0' * 64},
                       {'archive_path': f'runs/{self.run}/artifacts/outputs/other.png'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.target.register_file(self.path, **change)
            self.assertEqual(self.target._manifest, before)
            self.assertEqual(WorkspaceFS(self.target.root)._manifest, before)

    def test_first_publication_persists_complete_binding_atomically(self):
        original = self.target._save_manifest
        observed = []
        def checked_save():
            row = self.target.get_file_meta(self.path)
            if row:
                self.assertEqual(row['archive_path'], self.artifact)
                self.assertEqual(row['sha256'], self.digest)
                observed.append(True)
            original()
        with patch.object(self.target, '_save_manifest', checked_save):
            self.publish()
        self.assertTrue(observed)

    def test_unbound_formal_registration_is_rejected(self):
        with self.assertRaises(ValueError):
            self.target.register_file(self.path, source_run=self.run)
        self.assertIsNone(self.target.get_file_meta(self.path))

    def test_partial_existing_binding_is_not_silently_replaced(self):
        self.publish()
        del self.target._manifest['files'][self.path]['sha256']
        before = copy.deepcopy(self.target._manifest)
        with self.assertRaises(ValueError):
            self.target.register_file(self.path, source_run=self.run)
        self.assertEqual(self.target._manifest, before)

    def test_mutable_working_files_can_change_producer(self):
        name = 'outputs/intermediate/predictions.csv'
        self.target.register_file(name, source_run='run_first')
        self.target._manifest['files'][name].update(archive_path='old', sha256='old')
        self.target.register_file(name, source_run='run_second')
        row = self.target.get_file_meta(name)
        self.assertEqual(row['source_run'], 'run_second')
        self.assertNotIn('archive_path', row)
        self.assertNotIn('sha256', row)


if __name__ == '__main__':
    unittest.main()
