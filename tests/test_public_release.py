import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import build_public_candidate as release


class PublicReleaseTests(unittest.TestCase):
    def setUp(self):
        self.policy = release.load_policy()

    def test_new_documents_and_historical_revisions_are_not_implicitly_final(self):
        for name in ('docs/new-final.pdf', 'docs/unreviewed.md', 'presentations/old.json',
                     'scripts/internal-notes.md', 'validation/oci-20261027-w01-comparison/raw.zip'):
            self.assertFalse(release.included(name, self.policy), name)
        for name in ('LICENSE', 'docs/research-brief.md', 'benchlib/cli.py',
                     'validation/oci-20261027-w01-comparison/summary.json'):
            self.assertTrue(release.included(name, self.policy), name)

    def test_inventory_rejects_path_escape_and_duplicate_documents(self):
        for name in ('../secret', '/private/file', 'C:/private/file', 'docs\\..\\secret'):
            with self.assertRaises(ValueError):
                release.included(name, self.policy)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'policy.json'
            policy = dict(self.policy)
            policy['documents'] = [*policy['documents'], policy['documents'][0]]
            path.write_text(json.dumps(policy), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                release.load_policy(path)

    def test_sensitive_content_is_rejected_without_echoing_the_value(self):
        samples = [
            ' '.join(('Oracle', 'Restricted')),
            '/'.join(('C:', 'Users', 'private-operator', 'key.txt')),
            'ocid1.compartment.oc1..' + 'a' * 40,
            'ocir.us-phoenix-1.oci.oraclecloud.com/' + 'private-tenant/image',
        ]
        for value in samples:
            with self.subTest(value_type=value.split(':')[0][:8]):
                with self.assertRaises(ValueError) as caught:
                    release.check_content('docs/sample.md', value.encode(), self.policy)
                self.assertNotIn(value, str(caught.exception))
        release.check_content('configs/example.json', b'{"id":"ocid1.compartment.oc1..example"}', self.policy)

    def test_unreviewed_binary_and_changed_final_asset_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Binary asset'):
            release.check_content('docs/attachment.pdf', b'\xff\xfe', self.policy)
        name = 'docs/assets/fluxnova-research-brief-20261028.pdf'
        original = (release.ROOT / name).read_bytes()
        release.check_content(name, original, self.policy)
        with self.assertRaisesRegex(ValueError, 'Reviewed asset changed'):
            release.check_content(name, original + b'changed', self.policy)
        with self.assertRaisesRegex(ValueError, 'Private file'):
            release.check_content('configs/credentials.local.json', b'{}', self.policy)

    def test_git_really_ignores_drafts_and_keeps_the_exact_final_inventory(self):
        # Use Git's matching semantics in an isolated repository, not a mirror of
        # the string-generation implementation. Ignore tracked-file exceptions.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(['git', 'init', '--quiet', str(root)], check=True, capture_output=True)
            ignore = root / '.gitignore'
            ignore.write_text('runs/\n', encoding='utf-8')
            release.sync_ignore(self.policy, ignore)
            first = ignore.read_bytes()
            release.sync_ignore(self.policy, ignore)
            self.assertEqual(first, ignore.read_bytes())
            names = [*self.policy['documents'], 'docs/draft.md', 'docs/assets/old-brief.pdf',
                     'docs/history/notes.md', 'presentations/unreviewed-final.pptx', 'notes/research.docx']
            result = subprocess.run(['git', '-c', 'safe.directory=' + root.as_posix(),
                'check-ignore', '--no-index', '--stdin', '-z'], input=('\0'.join(names) + '\0').encode(),
                cwd=root, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(result.stdout.decode().strip('\0').split('\0')), set(names[len(self.policy['documents']):]))

    def test_private_registry_is_replaced_in_guides_only(self):
        value = ('docker pull ocir.us-phoenix-1.oci.oraclecloud.com/' + 'private-tenant/image:tag').encode()
        cleaned = release.sanitize_markdown(value)
        self.assertIn(b'REGISTRY_HOST/REGISTRY_NAMESPACE/image:tag', cleaned)
        release.check_content('docs/sample.md', cleaned, self.policy)


if __name__ == '__main__':
    unittest.main()
