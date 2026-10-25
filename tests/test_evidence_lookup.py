import base64
import json
import tempfile
import unittest
from pathlib import Path

from fpdb.core import digest, save_json
from fpdb.evidence import extract_joint, merge_evidence
from fpdb.index import build
from fpdb.lookup import lookup


class JointEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.a = [base64.b64encode(bytes([i]) * 20).decode() for i in (1, 2, 3)]
        self.b = [base64.b64encode(bytes([i]) * 20).decode() for i in (1, 4, 5)]

    def row(self, values):
        return dict(zip(('tlsClientCiphersSha1', 'tlsClientExtensionsSha1',
                         'tlsClientExtensionsSha1Le'), values))

    def test_joint_counts_and_metadata(self):
        one = self.row(self.a)
        payload = {'http': {'tls': {**one, 'tlsClientHelloLength': '1559', 'tlsCipher': 'AES'}}}
        joint = extract_joint(payload)
        self.assertEqual(joint['tlsClientHelloLength'], 1559)
        merged, truncated = merge_evidence([{**joint, 'count': 2}], [{**joint, 'count': 3}])
        self.assertFalse(truncated)
        self.assertEqual(merged[0]['count'], 5)

    def test_lookup_never_joins_independent_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity = {'target': 'test', 'language': 'python', 'client': 'requests',
                'runtime_version': '3.13', 'client_version': 'system',
                'platform': 'linux-x86_64', 'runner': 'ubuntu-24.04',
                'revision': '1', 'environment': {'os': 'linux', 'arch': 'x86_64',
                    'tls_backend': {'name': 'OpenSSL', 'version': '3.5'}}}
            record = {'schema_version': 1, 'identity': identity, 'record_id': digest(identity),
                'observations': [{}], 'evidence': [
                    {**self.row(self.a), 'count': 3}, {**self.row(self.b), 'count': 2}]}
            save_json(root / 'data/observations/python/requests/test.json', record)
            build(root)
            keys = list(self.row(self.a))
            matched = lookup(root, {keys[0]: self.a[0], keys[1]: self.a[1], keys[2]: self.a[2]})
            self.assertEqual(len(matched), 1)
            self.assertEqual(matched[0]['match'], 'three-field exact tuple')
            self.assertEqual(matched[0]['sample_count'], 3)
            # Two independent captures must never be presented as an exact
            # three-field match.  Partial matches may be ranked instead.
            partial = lookup(root, {keys[0]: self.a[0], keys[1]: self.a[1],
                                    keys[2]: self.b[2]})
            self.assertEqual(len(partial), 1)
            self.assertEqual(partial[0]['matched_fields'], 2)
            self.assertEqual(partial[0]['supplied_fields'], 3)
            self.assertEqual(partial[0]['sample_count'], 5)
            self.assertEqual(len(partial[0]['details']), 2)
            self.assertEqual({tuple(d['matched_field_names']) for d in partial[0]['details']},
                             {(keys[0], keys[1]), (keys[0], keys[2])})
            self.assertEqual(lookup(root, {keys[0]: self.a[0], keys[1]: self.a[1],
                                       keys[2]: self.b[2]}, require_all=True), [])
            # If only one of the supplied values exists, report an explicitly
            # weak candidate; never manufacture a higher-strength match.
            unknown = base64.b64encode(b'Z' * 20).decode()
            one = lookup(root, {keys[0]: self.a[0], keys[1]: unknown,
                                keys[2]: unknown})
            self.assertEqual(one[0]['match'], 'single-field candidate')
            self.assertEqual(one[0]['matched_fields'], 1)

    def test_legacy_summary_preserved_after_new_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity = {'target': 'legacy', 'language': 'python', 'client': 'requests',
                'runtime_version': '3.13', 'client_version': 'system',
                'platform': 'linux-x86_64', 'runner': 'ubuntu-24.04',
                'revision': '1', 'environment': {'os': 'linux', 'arch': 'x86_64',
                    'tls_backend': {'name': 'OpenSSL', 'version': '3.5'}}}
            # The legacy tuple existed before the new evidence was introduced.
            record = {'schema_version': 1, 'identity': identity, 'record_id': digest(identity),
                      'observations': [self.row(self.b)],
                      'evidence': [{**self.row(self.a), 'count': 2}]}
            save_json(root / 'data/observations/python/requests/legacy.json', record)
            build(root)
            fields = list(self.row(self.b))
            query = {fields[0]: self.b[0], fields[1]: self.b[1], fields[2]: self.b[2]}
            self.assertEqual(len(lookup(root, query)), 1)



if __name__ == '__main__':
    unittest.main()
