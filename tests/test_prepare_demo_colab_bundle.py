"""Tests for the reviewed-source Colab bundle builder."""

import ast
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "prepare_demo_colab_bundle.py"
EXPECTED_MEMBERS = [
    "aba_demo/__init__.py",
    "aba_demo/engine.py",
    "aba_demo/export.py",
    "aba_demo/vision.py",
    "aba_demo/botsort_reid.yaml",
    "aba_demo/context_schema.py",
    "aba_demo/openrouter_context.py",
    "requirements-demo-vision.txt",
    "tests/test_demo_vision.py",
    "aba_demo/colab_workflow.py",
    "tests/test_colab_workflow.py",
    "tests/test_context_schema.py",
    "tests/test_openrouter_context.py",
]


def load_bundle_module():
    spec = importlib.util.spec_from_file_location("prepare_demo_colab_bundle", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PrepareDemoColabBundleTests(unittest.TestCase):
    def notebook(self):
        return json.loads((PROJECT_ROOT / 'notebooks/ABA_Colab_Ready.ipynb').read_text(encoding='utf-8'))

    def cells(self):
        return {c['id']: ''.join(c['source']) for c in self.notebook()['cells']}

    def test_allowlist_matches_the_notebook_loader(self):
        tree = ast.parse(self.cells()['bundle-code'])
        allowed = next(ast.literal_eval(n.value) for n in ast.walk(tree)
                       if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'allowed' for t in n.targets))
        self.assertEqual(set(EXPECTED_MEMBERS), allowed)
        self.assertEqual(tuple(EXPECTED_MEMBERS), load_bundle_module().BUNDLE_FILES)

    def test_notebook_clean_compilable_and_no_transfer_widgets(self):
        for cell in self.notebook()['cells']:
            if cell['cell_type'] == 'code':
                source = ''.join(cell['source'])
                compile(source, cell['id'], 'exec')
                self.assertEqual(cell['outputs'], [])
                self.assertIsNone(cell['execution_count'])
                for forbidden in ('files.upload(', 'files.download(', 'input(', 'APPROVED FOR COLAB'):
                    self.assertNotIn(forbidden, source)
        source = '\n'.join(self.cells().values())
        self.assertIn('authorization', source)
        self.assertIn('does not upload', source)
        self.assertIn('Download...', source)
        self.assertIn('/content/demo_colab_bundle.zip', source)

    def test_notebook_uses_explicit_human_defaults_and_same_session(self):
        cells = self.cells()
        self.assertIn('EXPECTED_INITIAL_ID = None', cells['initial-code'])
        self.assertIn('INITIAL_TARGET_CONFIRMED = False', cells['initial-code'])
        self.assertIn('EXPECTED_RESELECTED_ID = None', cells['reselect-code'])
        self.assertIn('RESELECTION_CONFIRMED = False', cells['reselect-code'])
        self.assertIn('session.advance(', cells['probe-code'])
        self.assertIn('session.finish(', cells['export-code'])
        self.assertIn('session.publish(', cells['publish-code'])
        self.assertIn("REVIEWED_CANDIDATE_SHA256 = ''", cells['publish-code'])
        self.assertIn('HUMAN_REVIEW_CONFIRMED = False', cells['publish-code'])
        self.assertNotIn('VideoAnalyzer(', '\n'.join(cells.values()))
        self.assertIn("CANDIDATE_OVERLAP_POLICY = 'human_review_required'", cells['preview-code'])
        self.assertIn("'identity_recovery_max_gap_s': 0.5", cells['preview-code'])
        self.assertIn("'tracker_profile': 'botsort_reid'", cells['preview-code'])
        self.assertIn("'weights': 'yolo11s-pose.pt'", cells['preview-code'])
        self.assertIn("VIDEO_PATH = '/content/session.mp4'", cells['preview-code'])
        notebook_source = '\n'.join(cells.values())
        self.assertNotIn('initial ID2', notebook_source)
        self.assertNotIn('ID6', notebook_source)

    def test_context_key_supports_colab_ui_and_vscode_masked_fallback(self):
        source = self.cells()['context-code']
        self.assertIn("userdata.get('OPENROUTER_API_KEY')", source)
        self.assertIn('getpass(', source)
        self.assertIn('OPENROUTER_API_KEY', source)
        self.assertNotIn("api_key='", source)
        self.assertNotIn('os.environ', source)

    def test_install_command_surfaces_child_stderr(self):
        source = self.cells()['setup-code']
        self.assertIn('capture_output=True', source)
        self.assertIn('result.stderr', source)
        self.assertIn('result.returncode', source)

    def test_tracker_runtime_dependency_is_declared(self):
        requirements = (PROJECT_ROOT / 'requirements-demo-vision.txt').read_text(encoding='utf-8')
        self.assertIn('lap>=0.5.12,<0.6', requirements.splitlines())

    def test_loader_digest_clean_extraction_and_import_purge_are_exercised(self):
        import hashlib
        import types
        module = load_bundle_module()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            archive = workspace / 'bundle.zip'
            module.create_bundle(PROJECT_ROOT, archive)
            target = workspace / 'clean'
            target.mkdir()
            (target / 'sitecustomize.py').write_text('raise RuntimeError("stale")')
            source = self.cells()['bundle-code']
            tree = ast.parse(source)
            pin = next(ast.literal_eval(n.value) for n in ast.walk(tree)
                       if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'EXPECTED_BUNDLE_SHA256' for t in n.targets))
            self.assertTrue(pin == 'PIN_FINAL_BUNDLE_SHA256_BEFORE_DELIVERY' or (len(pin) == 64 and all(c in '0123456789abcdef' for c in pin)))
            source = source.replace("Path('/content/demo_colab_bundle.zip')", repr(archive))
            source = source.replace("Path('/content/aba_demo_project')", repr(target))
            source = source.replace(repr(pin), repr(hashlib.sha256(archive.read_bytes()).hexdigest()))
            import os
            before_cwd, before_path = Path.cwd(), sys.path[:]
            cached = {k: v for k,v in sys.modules.items() if k == 'aba_demo' or k.startswith('aba_demo.')}
            sys.modules['aba_demo.stale'] = types.ModuleType('aba_demo.stale')
            try:
                exec(source, {'WindowsPath': type(workspace), 'PosixPath': type(workspace)})
                self.assertFalse((target / 'sitecustomize.py').exists())
                self.assertNotIn('aba_demo.stale', sys.modules)
                self.assertEqual(sys.path[0], str(target))
                self.assertEqual({p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()}, set(EXPECTED_MEMBERS))
                archive.write_bytes(archive.read_bytes() + b'tamper')
                with self.assertRaisesRegex(ValueError, 'Bundle SHA-256 mismatch'):
                    exec(source, {'WindowsPath': type(workspace), 'PosixPath': type(workspace)})
            finally:
                os.chdir(before_cwd)
                sys.path[:] = before_path
                for key in list(sys.modules):
                    if key == 'aba_demo' or key.startswith('aba_demo.'):
                        del sys.modules[key]
                sys.modules.update(cached)

    def test_cli_writes_only_reviewed_files_with_original_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "demo-colab.zip"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--output", str(output)],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), EXPECTED_MEMBERS)
                for member in EXPECTED_MEMBERS:
                    self.assertEqual(archive.read(member), (PROJECT_ROOT / member).read_bytes())

    def test_generated_bundle_runs_reviewed_tests_in_clean_directory(self):
        module = load_bundle_module()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            output = workspace / "bundle.zip"
            module.create_bundle(PROJECT_ROOT, output)
            extracted = workspace / "extracted"
            with zipfile.ZipFile(output) as archive:
                archive.extractall(extracted)

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    "test_*.py",
                    "-v",
                ],
                cwd=extracted,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_archives_are_byte_deterministic_with_fixed_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.zip"
            second = Path(directory) / "second.zip"
            for output in (first, second):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "--output", str(output)],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                for info in archive.infolist():
                    self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                    self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                    self.assertEqual(info.external_attr, 0o100644 << 16)

    def test_missing_allowlisted_file_is_rejected_before_output_is_written(self):
        module = load_bundle_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = "aba_demo/vision.py"
            for relative in EXPECTED_MEMBERS:
                if relative != missing:
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(relative, encoding="utf-8")
            output = root / "bundle.zip"

            with self.assertRaisesRegex(FileNotFoundError, "aba_demo/vision.py"):
                module.create_bundle(root, output)

            self.assertFalse(output.exists())

    def test_cli_requires_force_to_replace_an_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing.zip"
            output.write_bytes(b"keep this file")

            refused = subprocess.run(
                [sys.executable, str(SCRIPT), "--output", str(output)],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("already exists", refused.stderr)
            self.assertEqual(output.read_bytes(), b"keep this file")

            replaced = subprocess.run(
                [sys.executable, str(SCRIPT), "--output", str(output), "--force"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), EXPECTED_MEMBERS)

    def test_failed_write_preserves_destination_and_removes_temporary_file(self):
        module = load_bundle_module()

        class FailingArchive:
            def __init__(self, path, *args, **kwargs):
                self.path = Path(path)
                self.path.write_bytes(b"partial archive")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def writestr(self, *args, **kwargs):
                raise OSError("simulated write failure")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in EXPECTED_MEMBERS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative, encoding="utf-8")
            output = root / "bundle.zip"
            output.write_bytes(b"original archive")

            with patch.object(module.zipfile, "ZipFile", FailingArchive):
                with self.assertRaisesRegex(OSError, "simulated write failure"):
                    module.create_bundle(root, output, force=True)

            self.assertEqual(output.read_bytes(), b"original archive")
            self.assertEqual(list(root.glob(".bundle.zip.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
