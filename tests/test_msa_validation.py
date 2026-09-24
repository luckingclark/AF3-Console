"""MSA completeness checks use synthetic data; no research inputs are included."""
import copy
import gzip
import lzma
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import af3_runtime as R


class MsaValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='af3-validation-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'P12345_data.json'
        self.value = {'name': 'P12345', 'dialect': 'alphafold3', 'version': 4,
                      'modelSeeds': [1], 'sequences': [{'protein': {
                          'id': 'A', 'sequence': 'MACDE',
                          'unpairedMsa': '>query\nMACDE\n>hit\nMAca-D-\n',
                          'pairedMsa': '', 'templates': []}}]}
        self.entity = {'type': 'protein', 'sequence': 'MACDE'}

    def check(self, value=None):
        R.atomic_json(self.path, self.value if value is None else value)
        return R.msa_validation_errors(self.path, self.entity)

    def test_complete_inline_and_explicit_empty_inputs(self):
        self.assertEqual(self.check(), [])
        self.assertTrue(R.validate_msa(self.path, self.entity))
        self.value['sequences'][0]['protein'].update(unpairedMsa='', pairedMsa='', templates=[])
        self.assertEqual(self.check(), [])

    def test_required_fields_reject_missing_null_and_wrong_types(self):
        for field in ('unpairedMsa', 'pairedMsa', 'templates'):
            for bad in (None, 123, {}, 'missing'):
                with self.subTest(field=field, bad=bad):
                    value = copy.deepcopy(self.value)
                    if bad == 'missing':
                        value['sequences'][0]['protein'].pop(field)
                    else:
                        value['sequences'][0]['protein'][field] = bad
                    self.assertTrue(any(field in e for e in self.check(value)))

    def test_external_relative_files_are_checked_after_edit_delete_and_restore(self):
        body = self.value['sequences'][0]['protein']
        alignment = self.root / 'synthetic alignment.a3m'
        alignment.write_text(body.pop('unpairedMsa'), encoding='utf-8')
        body['unpairedMsaPath'] = alignment.name
        self.assertEqual(self.check(), [])
        alignment.write_text('>query\nMACD\n', encoding='utf-8')
        self.assertTrue(any('aligned length' in e for e in R.msa_validation_errors(self.path)))
        alignment.unlink()
        self.assertTrue(any('resource' in e for e in R.msa_validation_errors(self.path)))
        alignment.write_text('>query\nMACDE\n', encoding='utf-8')
        self.assertEqual(R.msa_validation_errors(self.path), [])

    def test_reference_rejects_null_empty_directory_and_ambiguous_inline(self):
        for ref in (None, '', '.', 7):
            with self.subTest(ref=ref):
                value = copy.deepcopy(self.value)
                body = value['sequences'][0]['protein']
                body.pop('unpairedMsa')
                body['unpairedMsaPath'] = ref
                self.assertTrue(self.check(value))
        self.value['sequences'][0]['protein']['unpairedMsaPath'] = 'synthetic.a3m'
        self.assertTrue(any('only one' in e for e in self.check()))

    def test_container_asset_namespace_is_part_of_validation_cache(self):
        body = self.value['sequences'][0]['protein']
        body.pop('unpairedMsa')
        body['unpairedMsaPath'] = '/root/af_assets/synthetic.a3m'
        (self.root / 'synthetic.a3m').write_text('>query\nMACDE\n', encoding='utf-8')
        with mock.patch.object(R, 'ASSET_ROOT', str(self.root)):
            self.assertEqual(self.check(), [])
        with mock.patch.object(R, 'ASSET_ROOT', str(self.root / 'absent')):
            self.assertTrue(R.msa_validation_errors(self.path))

    def test_templates_require_mmcif_and_equal_valid_indices(self):
        good = {'mmcif': 'data_synthetic\n#\n', 'queryIndices': [0, 4], 'templateIndices': [2, 8]}
        self.value['sequences'][0]['protein']['templates'] = [good]
        self.assertEqual(self.check(), [])
        for bad in (None, {}, {'mmcif': ''}, {'mmcif': 'not mmcif'},
                    {'queryIndices': [5]}, {'queryIndices': [-1, 2]},
                    {'queryIndices': [True, 2]}, {'queryIndices': [0, '2']},
                    {'templateIndices': [2]}, {'templateIndices': [0, -1]},
                    {'templateIndices': [False, 2]}, {'queryIndices': None}):
            with self.subTest(bad=bad):
                template = dict(good)
                if isinstance(bad, dict) and bad:
                    template.update(bad)
                else:
                    template = bad
                self.value['sequences'][0]['protein']['templates'] = [template]
                self.assertTrue(any('templates' in e for e in self.check()))

    def test_template_reference_change_and_competing_sources(self):
        asset = self.root / 'synthetic.cif'
        asset.write_text('data_synthetic\n', encoding='utf-8')
        template = {'mmcifPath': asset.name, 'queryIndices': [], 'templateIndices': []}
        self.value['sequences'][0]['protein']['templates'] = [template]
        self.assertEqual(self.check(), [])
        asset.write_text('', encoding='utf-8')
        self.assertTrue(any('mmcif' in e for e in R.msa_validation_errors(self.path)))
        asset.write_text('data_synthetic\n', encoding='utf-8')
        self.assertEqual(R.msa_validation_errors(self.path), [])
        template['mmcif'] = 'data_synthetic\n'
        self.assertTrue(any('only one' in e for e in self.check()))

    def test_a3m_rejects_truncation_bad_characters_and_wrong_query(self):
        for alignment in ('MACDE', '>query\nMACD\n', '>query\nM-CDD\n',
                          '>query\nMACDE\n>hit\nMACD\n', '>query\nMACD!\n',
                          '>query\nMACDE\n>unfinished\n'):
            with self.subTest(alignment=alignment):
                self.value['sequences'][0]['protein']['unpairedMsa'] = alignment
                self.assertTrue(any('A3M' in e for e in self.check()))

    def test_a3m_wrapped_sequences_insertions_gaps_and_feature_equivalence(self):
        body = self.value['sequences'][0]['protein']
        body['sequence'] = 'MUDBZOJ'
        body['unpairedMsa'] = '>query\nMC\nDDEXxX\n>hit\nMcdCD-E-X\n'
        self.entity['sequence'] = body['sequence']
        self.assertEqual(self.check(), [])

    def test_rna_dna_ligand_fields_and_all_chain_validation(self):
        self.value['sequences'].extend([
            {'rna': {'id': 'B', 'sequence': 'ACGU', 'unpairedMsa': '>query\nACGU\n'}},
            {'dna': {'id': ['C', 'D'], 'sequence': 'ACGT'}},
            {'ligand': {'id': 'E', 'ccdCodes': ['ATP']}}])
        self.assertEqual(self.check(), [])
        self.value['sequences'][1]['rna'].pop('unpairedMsa')
        self.assertTrue(any('rna' in e for e in self.check()))

    def test_invalid_entry_shapes_sequence_types_and_chain_ids(self):
        for entry in ({}, {'unknown': {}}, {'protein': None},
                      {'protein': {}, 'rna': {}}, {'protein': {'id': 'A', 'sequence': 123}}):
            with self.subTest(entry=entry):
                self.value['sequences'] = [entry]
                self.assertTrue(self.check())
        for value in ({}, [], {'sequences': 'protein'}, {'sequences': []}):
            with self.subTest(value=value):
                self.assertTrue(self.check(value))

    def test_entity_selection_rejects_other_sequence_type_or_multiple_entries(self):
        self.assertEqual(self.check(), [])
        self.assertTrue(R.msa_validation_errors(self.path, {'type': 'protein', 'sequence': 'MADCE'}))
        self.assertTrue(R.msa_validation_errors(self.path, {'type': 'rna', 'sequence': 'MACDE'}))
        other = copy.deepcopy(self.value['sequences'][0])
        other['protein']['id'] = 'B'
        self.value['sequences'].append(other)
        self.assertTrue(any('exactly one' in e for e in self.check()))
        self.assertEqual(R.msa_validation_errors(self.path), [])

    def test_json_parse_errors_and_read_only_behavior(self):
        self.path.write_text('{"sequences": [', encoding='utf-8')
        self.assertTrue(R.msa_validation_errors(self.path))
        self.assertEqual(self.check(), [])
        before = self.path.read_bytes()
        self.assertEqual(R.msa_validation_errors(self.path), [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual({p.name for p in self.root.iterdir()}, {self.path.name})

    def test_cached_summary_avoids_reparsing_unchanged_alignment(self):
        self.assertEqual(self.check(), [])
        with mock.patch.object(R, '_msa_a3m_error', side_effect=AssertionError('reparsed')):
            self.assertEqual(R.msa_validation_errors(self.path), [])
            self.assertEqual(R.msa_validation_errors(self.path, self.entity), [])
        # Changing the JSON invalidates the compact cache.
        self.value['sequences'][0]['protein'].pop('templates')
        self.assertTrue(self.check())

    def test_compressed_external_alignment_detection_uses_magic_bytes(self):
        body = self.value['sequences'][0]['protein']
        text = body.pop('unpairedMsa').encode('utf-8')
        body['unpairedMsaPath'] = 'synthetic.dat'
        for compress in (gzip.compress, lzma.compress):
            with self.subTest(compress=compress.__module__):
                (self.root / 'synthetic.dat').write_bytes(compress(text))
                self.assertEqual(self.check(), [])

    def test_valid_empty_fields_are_warnings_with_chain_ids(self):
        self.assertEqual(self.check(), [])
        self.assertEqual(R.msa_empty_fields(self.path), [
            'sequences[0].protein[A].pairedMsa', 'sequences[0].protein[A].templates'])
        body = self.value['sequences'][0]['protein']
        body['id'] = ['A', 'B']
        body['unpairedMsa'] = ''
        self.assertEqual(self.check(), [])
        warnings = R.msa_empty_fields(self.path, self.entity)
        self.assertEqual(len(warnings), 3)
        self.assertTrue(all('protein[A,B]' in field for field in warnings))
        body.pop('templates')
        self.assertTrue(self.check())
        self.assertEqual(R.msa_empty_fields(self.path), [])

    def test_empty_external_compressed_msa_is_warned_and_refreshes_on_change(self):
        body = self.value['sequences'][0]['protein']
        body.pop('unpairedMsa')
        body['unpairedMsaPath'] = 'synthetic.a3m.gz'
        asset = self.root / 'synthetic.a3m.gz'
        asset.write_bytes(gzip.compress(b''))
        self.assertEqual(self.check(), [])
        self.assertTrue(any('unpairedMsa' in field for field in R.msa_empty_fields(self.path)))
        asset.write_bytes(gzip.compress(b'>query\nMACDE\n'))
        self.assertFalse(any('unpairedMsa' in field for field in R.msa_empty_fields(self.path)))

    def test_rna_empty_warning_only_requires_unpaired_msa(self):
        self.value['sequences'] = [{'rna': {'id': 'A', 'sequence': 'ACGU', 'unpairedMsa': ''}}]
        self.entity = {'type': 'rna', 'sequence': 'ACGU'}
        self.assertEqual(self.check(), [])
        self.assertEqual(R.msa_empty_fields(self.path), ['sequences[0].rna[A].unpairedMsa'])

    def test_invalid_compressed_resource_returns_validation_error(self):
        body = self.value['sequences'][0]['protein']
        body.pop('unpairedMsa')
        body['unpairedMsaPath'] = 'synthetic.dat'
        for content in (b'\x1f\x8bgarbage', b'\xfd7zXZ\x00garbage'):
            (self.root / 'synthetic.dat').write_bytes(content)
            self.assertTrue(self.check())


if __name__ == '__main__':
    unittest.main()
