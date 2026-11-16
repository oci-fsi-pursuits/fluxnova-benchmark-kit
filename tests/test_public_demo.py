from html.parser import HTMLParser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Surface(HTMLParser):
    def __init__(self):
        super().__init__(); self.tags = []
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class PublicDemoTests(unittest.TestCase):
    def test_public_surface_has_no_submission_or_executable_input(self):
        surface = Surface(); surface.feed((ROOT / 'site/index.html').read_text())
        for tag, attrs in surface.tags:
            self.assertNotIn(tag, ('form', 'iframe', 'object', 'embed', 'input', 'textarea'))
            self.assertFalse(any(key.startswith('on') for key in attrs))
            if tag == 'a': self.assertTrue(attrs['href'].startswith('https://'))
        policies = [a['content'] for t, a in surface.tags if t == 'meta' and a.get('http-equiv') == 'Content-Security-Policy']
        self.assertEqual(len(policies), 1)
        for directive in ("connect-src 'none'", "object-src 'none'", "form-action 'none'", "base-uri 'none'"):
            self.assertIn(directive, policies[0])

    def test_export_allowlist_excludes_lab_and_credentials(self):
        from scripts.build_demo_site import ASSETS
        self.assertEqual(set(ASSETS), {'.nojekyll', 'index.html', 'app.js', 'style.css'})
        self.assertEqual({p.name for p in (ROOT / 'site').iterdir()}, set(ASSETS))

    def test_preview_is_unambiguously_labeled(self):
        text = (ROOT / 'site/index.html').read_text()
        self.assertIn('Illustration, not a live engine.', text)
        self.assertIn('not benchmark measurements', text)
