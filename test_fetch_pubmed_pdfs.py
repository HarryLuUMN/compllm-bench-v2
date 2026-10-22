import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fetch_pubmed_pdfs import fetch_database, resolve_pdf


class PDFTests(unittest.TestCase):
    def test_resolves_available_version_without_assuming_version_one(self):
        class FakeClient:
            def get(self, url):
                if 'idconv' in url:
                    return json.dumps({'records': [{'doi': '10/test', 'pmcid': 'PMC42', 'pmid': '7'}]}).encode()
                if 'list-type' in url:
                    return b'<ListBucketResult><CommonPrefixes><Prefix>PMC42.2/</Prefix></CommonPrefixes></ListBucketResult>'
                return json.dumps({'doi': '10/test', 'version': 2, 'is_manuscript': 'no',
                                   'pdf_url': 's3://pmc-oa-opendata/PMC42.2/article.pdf?md5=abc'}).encode()
        source = resolve_pdf('10/test', FakeClient())
        self.assertEqual(source['version'], 2)
        self.assertEqual(source['url'], 'https://pmc-oa-opendata.s3.amazonaws.com/PMC42.2/article.pdf')
        self.assertEqual(source['md5'], 'abc')

    def test_download_validation_resume_and_statuses(self):
        for content, checksum, status in [(b'%PDF-fixture', None, 'downloaded'),
                                          (b'<html>blocked</html>', None, 'failed'),
                                          (b'%PDF-fixture', 'wrong', 'failed')]:
            with self.subTest(status=status, checksum=checksum), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                folder = root / 'paper_0001'
                folder.mkdir()
                metadata = {'doi': '10/test', 'paper_pdf_status': 'missing'}
                (folder / 'metadata.json').write_text(json.dumps(metadata))
                (root / 'manifest.json').write_text(json.dumps({'entries': [{'folder': folder.name, **metadata}]}))
                source = {'url': 'https://example.org/paper.pdf', 'md5': checksum}
                class FakeClient:
                    def get(self, url):
                        return content
                with patch('fetch_pubmed_pdfs.resolve_pdf', return_value=source):
                    counts = fetch_database(root, client=FakeClient())
                self.assertEqual(counts[status], 1)
                self.assertEqual((folder / 'paper.pdf').exists(), status == 'downloaded')
                manifest = json.loads((root / 'manifest.json').read_text())
                self.assertEqual(manifest['entries'][0]['paper_pdf_status'], status)
                if status == 'downloaded':
                    with patch('fetch_pubmed_pdfs.resolve_pdf', side_effect=AssertionError('Must skip')):
                        self.assertEqual(fetch_database(root)['existing'], 1)
                else:
                    with patch('fetch_pubmed_pdfs.resolve_pdf', return_value=None):
                        self.assertEqual(fetch_database(root)['unavailable'], 1)

    def test_no_match_is_unavailable(self):
        class FakeClient:
            def get(self, url):
                return b'{"records": []}'
        self.assertIsNone(resolve_pdf('10/missing', FakeClient()))


if __name__ == '__main__':
    unittest.main()
