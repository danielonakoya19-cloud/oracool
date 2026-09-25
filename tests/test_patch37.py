"""patch37 — the web-app icon is the new OraCool orb (PWA manifest, favicon, apple touch,
maskable set, notification icon) plus a wordmark logo for link previews."""
import io, json, unittest
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRV = open(ROOT / 'server.py', encoding='utf-8').read()
APP = open(ROOT / 'index.html', encoding='utf-8').read()
LAND = open(ROOT / 'landing.html', encoding='utf-8').read()
FILES = {"icon.png": (512, 512), "icon-192.png": (192, 192), "icon-512.png": (512, 512), "icon-maskable-192.png": (192, 192),
         "icon-maskable-512.png": (512, 512), "apple-touch-icon.png": (180, 180), "logo.png": (1024, 1024)}


class Assets(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch45-inline-feed"'), 2)

    def test_files_exist_with_right_sizes(self):
        for f, size in FILES.items():
            im = Image.open(ROOT / f); self.assertEqual(im.size, size, f)
        ico = Image.open(ROOT / 'favicon.ico'); self.assertEqual(ico.format, 'ICO')

    def test_icon_is_the_new_blue_orb_not_the_old_gold_triangle(self):
        im = Image.open(ROOT / 'icon-512.png').convert('RGB')
        px = im.load(); w, h = im.size
        centre = px[w // 2, h // 2]
        self.assertGreater(centre[2], 150, "centre must be the blue orb, got %r" % (centre,))
        gold = sum(1 for x in range(0, w, 8) for y in range(0, h, 8) if px[x, y][0] > 200 and 150 < px[x, y][1] < 210 and px[x, y][2] < 90)
        self.assertLess(gold, 20, "old gold triangle pixels still present")

    def test_maskable_keeps_art_inside_safe_zone(self):
        im = Image.open(ROOT / 'icon-maskable-512.png').convert('RGB'); px = im.load()
        for x, y in ((3, 3), (508, 3), (3, 508), (508, 508), (256, 4), (4, 256)):  # corners + edge midpoints are flat base
            r, g, b = px[x, y]; self.assertLess(max(r, g, b), 40, (x, y, px[x, y]))
        self.assertGreater(px[256, 256][2], 150)

    def test_manifest(self):
        m = json.load(open(ROOT / 'manifest.json'))
        srcs = [i['src'] for i in m['icons']]
        self.assertIn('/icon-512.png?v=2', srcs); self.assertIn('/icon-maskable-512.png?v=2', srcs)
        self.assertTrue(any(i['purpose'] == 'maskable' for i in m['icons']))
        self.assertTrue(all(i['purpose'] in ('any', 'maskable') for i in m['icons']))  # never 'any maskable' on the same image
        self.assertEqual(m['id'], '/app')  # same app identity → existing installs update instead of duplicating

    def test_heads_and_routes(self):
        for k in ('href="/icon-512.png?v=2"', 'href="/favicon.ico?v=2"', 'href="/apple-touch-icon.png?v=2"',
                  'property="og:image" content="https://oracoolai.com/logo.png"'):
            self.assertIn(k, APP, k); self.assertIn(k, LAND, k)
        for route in ('"/icon.png"', '"/icon-512.png"', '"/icon-maskable-512.png"', '"/favicon.ico": "image/x-icon"', '"/logo.png"'):
            self.assertIn(route, SRV, route)
        self.assertIn('elif path in _BRAND_ASSETS:', SRV)
        self.assertIn("icon: '/icon-192.png?v=2'", open(ROOT / 'sw.js').read())


if __name__ == '__main__':
    unittest.main()
