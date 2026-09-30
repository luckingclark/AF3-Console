"""Synthetic offline sequences: no UniProt traffic and no real protein examples."""
import contextlib
import gzip
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))
import af3
import af3_runtime as R
import af3_ui_backend as B
import build_uniprot_library as builder


class OfflineSequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='af3 offline # ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.personal = self.root / 'personal'
        self.shared = self.root / 'shared library'
        self.shared.mkdir()
        self.network = mock.patch.object(af3.urllib.request, 'urlopen', side_effect=AssertionError('Unexpected network'))
        self.network.start()
        self.addCleanup(self.network.stop)
        for key, value in (('UNIPROT_CACHE', str(self.personal)), ('HOST_UNIPROT_SHARED', str(self.shared))):
            patch = mock.patch.object(af3, key, value)
            patch.start()
            self.addCleanup(patch.stop)

    def library(self, directory=None, extra_fasta='', extra_tsv=''):
        directory = directory or self.shared
        fasta = self.root / 'source.fasta.gz'
        tsv = self.root / 'source.tsv.gz'
        with gzip.open(fasta, 'wt', encoding='utf-8') as handle:
            handle.write('>sp|P12345|EXAMPLE\nACDEFGHIKLMNPQRSTVWY\n'
                         '>sp|P12345-2|EXAMPLE\nACDEFGHIKL\n' + extra_fasta)
        with gzip.open(tsv, 'wt', encoding='utf-8') as handle:
            handle.write('Entry\tSecondary accessions\tLength\nP12345\tQ12345;\t20\n' + extra_tsv)
        index = directory / R.UNIPROT_INDEX_NAME
        report = builder.build_index([dict(proteome='synthetic', expected_count=1 + bool(extra_tsv),
                    fasta_path=fasta, tsv_path=tsv)], index, {'uniprot_release': 'synthetic'})
        return index, report

    def test_shared_readonly_index_primary_secondary_isoform_no_small_files(self):
        index, report = self.library()
        before = index.read_bytes()
        index.chmod(0o444)
        self.addCleanup(index.chmod, 0o644)
        self.assertEqual(report['sequence_count'], 2)
        self.assertEqual(report['alias_count'], 1)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            for accession in ('P12345', 'Q12345'):
                self.assertEqual(af3.fetch_uniprot(accession), 'ACDEFGHIKLMNPQRSTVWY')
            self.assertEqual(af3.fetch_uniprot('P12345-2'), 'ACDEFGHIKL')
        self.assertEqual(out.getvalue(), '')
        self.assertFalse(self.personal.exists())
        self.assertEqual(list(self.shared.iterdir()), [index])
        self.assertEqual(index.read_bytes(), before)

    def test_personal_seq_then_index_then_shared_priority(self):
        self.library()
        self.personal.mkdir()
        personal_index, _ = self.library(self.personal)
        seq = self.personal / 'P12345.seq'
        seq.write_text('AAAA\n')
        (self.shared / 'P12345.seq').write_text('CCCC\n')
        self.assertEqual(af3.fetch_uniprot('P12345'), 'AAAA')
        seq.unlink()
        self.assertEqual(af3.fetch_uniprot('P12345'), 'ACDEFGHIKLMNPQRSTVWY')
        personal_index.unlink()
        self.assertEqual(af3.fetch_uniprot('P12345'), 'CCCC')

    def test_corrupt_personal_index_can_fall_back_to_shared_without_modification(self):
        self.library()
        self.personal.mkdir()
        bad = self.personal / R.UNIPROT_INDEX_NAME
        bad.write_bytes(b'not a SQLite database')
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(af3.fetch_uniprot('P12345'), 'ACDEFGHIKLMNPQRSTVWY')
        self.assertIn('WARNING', err.getvalue())
        self.assertEqual(bad.read_bytes(), b'not a SQLite database')

    def test_missing_id_network_error_explains_offline_location(self):
        import urllib.error
        self.library()
        with mock.patch.object(af3.urllib.request, 'urlopen', side_effect=urllib.error.URLError('DNS unavailable')):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(R.BusinessError, 'uniprot.sqlite3'):
                af3.fetch_uniprot('Q12345-2')
        self.assertFalse(self.personal.exists())

    def test_online_fallback_writes_only_personal_cache(self):
        from urllib.response import addinfourl
        payload = io.BytesIO(b'>sp|P12345|EXAMPLE\nACDEFGHIKL\n')
        response = addinfourl(payload, {}, 'https://example.invalid')
        with mock.patch.object(af3.urllib.request, 'urlopen', return_value=response) as network:
            self.assertEqual(af3.fetch_uniprot('P12345'), 'ACDEFGHIKL')
            network.assert_called_once()
        self.assertEqual((self.personal / 'P12345.seq').read_text(), 'ACDEFGHIKL\n')
        self.assertEqual(list(self.shared.iterdir()), [])

    def test_gui_backend_and_isoform_parser_use_offline_sequence(self):
        self.library()
        entities, error = B.capture_af3_parse('P12345-2x2:trunc=2-5', fetch=True)
        self.assertIsNone(error)
        self.assertEqual(entities[0]['type'], 'protein')
        self.assertEqual(entities[0]['uniprot'], 'P12345-2')
        self.assertEqual(entities[0]['copies'], 2)
        self.assertEqual(entities[0]['sequence'], 'CDEF')
        self.assertEqual(af3.parse_entity('l:CC-O')['type'], 'ligand')

    def test_builder_rejects_truncated_export_without_publishing(self):
        index, _ = self.library()
        before = index.read_bytes()
        with self.assertRaises(FileExistsError):
            builder.build_index([], index, {})
        bad_target = self.root / 'bad.sqlite3'
        with self.assertRaisesRegex(ValueError, 'Incomplete canonical'):
            builder.build_index([dict(proteome='synthetic', expected_count=2,
                fasta_path=self.root/'source.fasta.gz', tsv_path=self.root/'source.tsv.gz')], bad_target, {})
        self.assertFalse(bad_target.exists())
        self.assertEqual(index.read_bytes(), before)
        self.assertFalse(list(self.root.glob('*.partial')))

    def test_invalid_index_sequence_and_path_traversal_rejected(self):
        index, _ = self.library()
        with contextlib.closing(sqlite3.connect(index)) as db:
            db.execute("UPDATE sequences SET sequence='' WHERE accession='P12345'")
            db.commit()
        with self.assertRaisesRegex(ValueError, 'Invalid sequence'):
            R.lookup_uniprot_index(index, 'P12345')
        with self.assertRaisesRegex(ValueError, 'Invalid UniProt'):
            R.cached_uniprot_sequence('../P12345', self.personal, self.shared)

    def test_builder_detects_missing_isoform_records(self):
        self.library()
        target = self.root / 'incomplete.sqlite3'
        with self.assertRaisesRegex(ValueError, 'FASTA export count'):
            builder.build_index([dict(proteome='synthetic', expected_count=1, expected_fasta=3,
                fasta_path=self.root/'source.fasta.gz', tsv_path=self.root/'source.tsv.gz')], target, {})
        self.assertFalse(target.exists())

    def test_shared_setting_import_normalization_and_no_write_requirement(self):
        imported = B.imported_config_values({'HOST_UNIPROT_SHARED': str(self.shared)})
        self.assertEqual(imported['HOST_UNIPROT_SHARED'], str(self.shared))
        self.assertEqual(R.normalize_config({'HOST_UNIPROT_SHARED': ''})['HOST_UNIPROT_SHARED'], '')
        checks = af3.deployment_checks({'HOST_UNIPROT_SHARED': str(self.shared)}, require_scheduler=False)
        self.assertTrue(next(c for c in checks if c['key'] == 'HOST_UNIPROT_SHARED')['ok'])


if __name__ == '__main__':
    unittest.main()
