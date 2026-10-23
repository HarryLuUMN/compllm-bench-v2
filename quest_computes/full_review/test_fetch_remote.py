import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('remote_fetch', Path(__file__).with_name('fetch_remote.py'))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class RecoveryTests(unittest.TestCase):
    def test_checksums_and_non_pdf(self):
        content = b'%PDF-original'
        self.assertTrue(module.valid(content, {'md5': hashlib.md5(content).hexdigest()}))
        self.assertFalse(module.valid(content, {'sha256': 'wrong'}))
        self.assertFalse(module.valid(b'<html>challenge</html>', {}))

    def test_skips_existing_and_retries_download_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = []
            content = b'%PDF-original'
            for index in (1, 2):
                name = f'paper_{index:04d}'
                (root / name).mkdir()
                entries.append({'folder': name, 'paper_pdf_status': 'downloaded',
                                'paper_pdf_source': {'url': 'https://example.org/paper.pdf',
                                                     'sha256': hashlib.sha256(content).hexdigest()}})
            (root / 'paper_0001/paper.pdf').write_bytes(content)
            (root / 'manifest.json').write_text(json.dumps({'entries': entries}))
            with patch.object(module, 'urlopen', side_effect=[OSError('network'), io.BytesIO(content)]) as fetch:
                with patch.object(module.time, 'sleep'):
                    self.assertEqual(module.restore(root, 1), {'existing': 1, 'restored': 1, 'failed': 0})
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual((root / 'paper_0002/paper.pdf').read_bytes(), content)
            self.assertFalse((root / 'paper_0002/paper.pdf.remote.tmp').exists())


if __name__ == '__main__':
    unittest.main()
