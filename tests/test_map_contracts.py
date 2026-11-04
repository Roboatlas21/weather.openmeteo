"""Offline regression checks for CoreELEC weather-map integration."""
import ast
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def map_api_urls():
    tree = ast.parse((ROOT / "lib" / "config.py").read_text(encoding="utf-8"))
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "map_api" for t in node.targets)
    )
    return ast.literal_eval(assignment.value)


class MapProviderContractTests(unittest.TestCase):
    def test_map_modules_compile_without_importing_kodi(self):
        modules = ("lib/api.py", "lib/weather.py", "lib/config.py",
                   "lib/monitor.py", "lib/service.py", "lib/maptiles.py")
        for name in modules:
            with self.subTest(module=name):
                compile((ROOT / name).read_bytes(), name, "exec")

    def test_settings_are_well_formed(self):
        ET.parse(ROOT / "resources" / "settings.xml")
        ET.parse(ROOT / "addon.xml")

    def test_rainviewer_uses_universal_blue(self):
        self.assertTrue(map_api_urls()["rvradar"].endswith("/2/1_1.png"))

    def test_eccc_uses_current_layer_names_and_transparency(self):
        urls = map_api_urls()
        required = {
            "gctemp": "GDPS_15km_AirTemp_2m",
            "gcwind": "GDPS_15km_Winds_10m",
            "gccloud": "GDPS_15km_TotalCloudCover",
            "gcprecip": "GDPS_15km_PrecipType-Significant1h",
        }
        for name, layer in required.items():
            with self.subTest(map=name):
                self.assertIn("LAYERS=" + layer, urls[name])
                self.assertIn("TRANSPARENT=TRUE", urls[name])
                self.assertNotIn("GDPS.ETA_", urls[name])

    def test_old_satellite_feed_is_not_requested(self):
        source = (ROOT / "lib" / "config.py").read_text(encoding="utf-8")
        self.assertIn("map_rv          = { 'rvradar': map_rvradar }", source)
        self.assertNotIn("'rvsatellite': map_rvsatellite", source)


if __name__ == "__main__":
    unittest.main()
