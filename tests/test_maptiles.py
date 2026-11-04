"""Regression tests for the Kodi-independent radar geometry and catalogue."""
import unittest

from lib.maptiles import CANVAS_SIZE, radar_frames, radar_tile_layout


class RadarGeometryTests(unittest.TestCase):
    def test_native_zoom_uses_nine_tiles_and_no_scaling(self):
        layout = radar_tile_layout(64, 48, 7)
        self.assertEqual(layout["zoom"], 7)
        self.assertEqual(len(layout["tiles"]), 9)
        self.assertEqual(layout["columns"], 3)
        self.assertEqual(layout["rows"], 3)
        self.assertEqual(layout["affine"], (1, 0, 0, 0, 1, 0))

    def test_zoom_ten_never_requests_above_seven(self):
        layout = radar_tile_layout(512, 384, 10)
        self.assertEqual(layout["zoom"], 7)
        self.assertLessEqual(len(layout["tiles"]), 4)
        self.assertEqual(layout["affine"][0], 0.125)
        self.assertEqual(layout["affine"][3], 0)
        self.assertEqual(layout["affine"][4], 0.125)

    def test_antimeridian_wraps_requested_tile_not_canvas_location(self):
        layout = radar_tile_layout(0, 40, 7)
        self.assertIn((127, 39, 0, 0), layout["tiles"])
        self.assertIn((0, 39, 256, 0), layout["tiles"])

    def test_invalid_polar_tile_is_not_requested(self):
        layout = radar_tile_layout(80, 0, 7)
        self.assertEqual(len(layout["tiles"]), 6)
        self.assertEqual(layout["rows"], 3)
        self.assertTrue(all(0 <= y < 128 for _, y, _, _ in layout["tiles"]))

    def test_viewport_is_three_tiles(self):
        self.assertEqual(CANVAS_SIZE, 768)

    def test_invalid_zoom_raises(self):
        for zoom in (-1, 0, 16, 8.5):
            with self.assertRaises(ValueError):
                radar_tile_layout(1, 1, zoom)


class RadarMetadataTests(unittest.TestCase):
    def test_sorted_and_deduplicated(self):
        index = {"radar": {"past": [
            {"time": 300, "path": "/v2/radar/300"},
            {"time": 100, "path": "/v2/radar/100"},
            {"time": 300, "path": "/v2/radar/300"},
            {"time": 200, "path": "/v2/radar/200"},
        ]}}
        self.assertEqual(radar_frames(index), [
            (100, "/v2/radar/100"),
            (200, "/v2/radar/200"),
            (300, "/v2/radar/300"),
        ])
        self.assertEqual(radar_frames(index, limit=2), [
            (200, "/v2/radar/200"), (300, "/v2/radar/300")
        ])

    def test_malformed_or_untrusted_paths_are_ignored(self):
        index = {"radar": {"past": [
            {"time": 123, "path": "//another.host/v2/radar/123"},
            {"time": 456, "path": "/v2/radar/456"},
            {"time": 789, "path": "/v2/satellite/789"},
            {"time": -1, "path": "/v2/radar/-1"},
            {"time": "bad", "path": "/v2/radar/bad"},
        ]}}
        self.assertEqual(radar_frames(index), [(456, "/v2/radar/456")])

    def test_zero_history_limit_is_empty(self):
        self.assertEqual(radar_frames({"radar": {"past": [{"time": 123, "path": "/v2/radar/123"}]}}, limit=0), [])

    def test_absent_metadata_is_empty(self):
        self.assertEqual(radar_frames(None), [])
        self.assertEqual(radar_frames({"satellite": {"infrared": []}}), [])


if __name__ == "__main__":
    unittest.main()
