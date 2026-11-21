from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from workrunner import native_cuda, stream_digest
from workrunner.training_artifacts import training_output


class NativeBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.library = object()
        (self.folder / 'compute_manifest.json').write_text(json.dumps({'cuda_architectures': ['75']}))
        (self.folder / 'libnative_cuda.so').write_bytes(b'test library')
        patches = [
            patch.object(native_cuda, '_LIBRARIES', {}),
            patch.object(native_cuda, 'library_path', return_value=self.folder / 'libword_codec.so'),
            patch.object(native_cuda.torch.cuda, 'is_available', return_value=True),
            patch.object(native_cuda.torch.cuda, 'get_device_capability', return_value=(7, 5)),
            patch.object(native_cuda.ctypes, 'CDLL', side_effect=self.load_library),
            patch.object(native_cuda, '_configure', return_value=self.library),
        ]
        for mock_patch in patches:
            mock_patch.start()
            self.addCleanup(mock_patch.stop)

    def load_library(self, path):
        if not Path(path).is_file():
            raise OSError('missing shared library')
        return self.library

    def test_quiet_and_normal_runs_load_bundled_library_without_compiling(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('production must not compile')):
            with training_output(False):
                self.assertIs(native_cuda.load_native(), self.library)
                self.assertIs(native_cuda.load_native(), self.library)
            self.assertIs(native_cuda.load_native(), self.library)
        self.assertEqual(native_cuda.ctypes.CDLL.call_count, 1)
        self.assertEqual(set(path.name for path in self.folder.iterdir()),
                         {'compute_manifest.json', 'libnative_cuda.so'})

    def test_unsupported_gpu_has_explicit_error(self):
        with patch.object(native_cuda.torch.cuda, 'get_device_capability', return_value=(6, 1)):
            with self.assertRaisesRegex(native_cuda.NativeUnavailable, 'no bundled CUDA kernels for sm_61'):
                native_cuda.load_native()
        self.assertEqual(native_cuda._LIBRARIES, {})

    def test_missing_and_invalid_manifest_have_explicit_errors(self):
        manifest = self.folder / 'compute_manifest.json'
        for content in (None, 'invalid JSON', '{}'):
            with self.subTest(content=content):
                if content is None:
                    manifest.unlink()
                else:
                    manifest.write_text(content)
                with self.assertRaisesRegex(native_cuda.NativeUnavailable, 'cannot load bundled'):
                    native_cuda.load_native()
        self.assertEqual(native_cuda._LIBRARIES, {})

    def test_missing_binary_has_explicit_error(self):
        (self.folder / 'libnative_cuda.so').unlink()
        with self.assertRaisesRegex(native_cuda.NativeUnavailable, 'missing shared library'):
            native_cuda.load_native()
        self.assertEqual(native_cuda._LIBRARIES, {})

    def test_missing_digest_binary_uses_python_fallback(self):
        stream_digest._load_native.cache_clear()
        self.addCleanup(stream_digest._load_native.cache_clear)
        with patch.object(stream_digest, 'library_path', return_value=self.folder / 'libword_codec.so'):
            self.assertIsNone(stream_digest._load_native(False))
            self.assertIsNone(stream_digest._load_native(True))

    def test_production_has_no_cpp_or_cuda_source_files(self):
        root = Path(__file__).resolve().parents[1]
        suffixes = {'.c', '.cc', '.cpp', '.cxx', '.h', '.hpp', '.cu', '.cuh'}
        sources = [path.relative_to(root) for folder in ('src', 'tools', 'tests')
                   for path in (root / folder).rglob('*') if path.suffix in suffixes]
        self.assertEqual(sources, [])


if __name__ == '__main__':
    unittest.main()
