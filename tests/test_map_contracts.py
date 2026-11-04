"""Offline integration checks for map settings and the Kodi entry points."""
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


class MapProviderContractTests(unittest.TestCase):
    def test_all_python_modules_compile(self):
        for path in (ROOT / 'lib').glob('*.py'):
            with self.subTest(module=path.name):
                compile(path.read_bytes(), str(path), 'exec')

    def test_settings_and_addon_xml_parse(self):
        settings = ET.parse(ROOT / 'resources/settings.xml')
        ET.parse(ROOT / 'addon.xml')
        ids = [s.get('id') for s in settings.findall('.//setting')]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn('mapradarsource', ids)

    def test_new_settings_have_unique_localized_labels(self):
        text = (ROOT / 'resources/language/resource.language.en_gb/strings.po').read_text()
        ids = re.findall(r'msgctxt "#(\d+)"', text)
        self.assertEqual(len(ids), len(set(ids)))
        settings = ET.parse(ROOT / 'resources/settings.xml')
        for name in ('mapradarsource', 'mapmuted', 'maplegends'):
            setting = settings.find('.//setting[@id="'+name+'"]')
            self.assertIn(setting.get('label'), ids)
