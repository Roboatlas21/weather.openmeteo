"""Behavioral map tests with real PNGs, WMS metadata and temporary caches."""
import io
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch
from PIL import Image
from lib import maps
from lib.maptiles import mercator_segments, location_tile

NOW = 1804352400
WORDS = dict(loading='Loading', unavailable='Unavailable', stale='Cached', radar='Radar', forecast='Forecast')
HEADINGS = {kind: kind for kind in maps.LAYERS}


def image_bytes(size=(256, 256), color=(40, 100, 150, 255)):
    data = io.BytesIO()
    Image.new('RGBA', size, color).save(data, format='PNG')
    return data.getvalue()


def metadata(kind, run=NOW-3600):
    name, style = maps.LAYERS[kind]
    if kind == 'rvradar':
        times = maps.utc(NOW-3960) + '/' + maps.utc(NOW) + '/PT6M'
        ref = ''
    else:
        times = ','.join(maps.utc(NOW+i*3600) for i in range(-3, 28))
        ref = '<Dimension name="reference_time" default="{}">{}</Dimension>'.format(maps.utc(run), maps.utc(run))
    return ('<WMS_Capabilities xmlns="http://www.opengis.net/wms"><Capability><Layer><Layer><Name>'
            + name + '</Name><Dimension name="time">' + times + '</Dimension>' + ref
            + '<Style><Name>' + style + '</Name></Style></Layer></Layer></Capability></WMS_Capabilities>').encode()


class Provider:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.run = NOW-3600

    def __call__(self, url):
        self.calls.append(url)
        if self.fail and self.fail(url):
            return None
        if 'tile.openstreetmap' in url or 'tilecache.rainviewer' in url:
            return image_bytes()
        if 'weather-maps.json' in url:
            return json.dumps({'radar': {'past': [dict(time=NOW-i*600, path='/v2/radar/'+str(NOW-i*600)) for i in range(12)]}}).encode()
        q = parse_qs(urlparse(url).query)
        if q['REQUEST'][0] == 'GetLegendGraphic':
            return image_bytes((76, 275), 'white')
        kind = next(k for k, v in maps.LAYERS.items() if v[0] == q['LAYERS'][0])
        if q['REQUEST'][0] == 'GetCapabilities':
            return metadata(kind, self.run)
        return image_bytes((int(q['WIDTH'][0]), int(q['HEIGHT'][0])))


class MapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        maps._METADATA.clear()
        self.provider = Provider()
        self.now = NOW
        # Production writes and cache expiry share wall time. Keep PNG mtimes
        # on the simulated clock too, including unpublished replacement frames.
        save_image = maps.save_image
        def save_at_test_time(path, image):
            save_image(path, image)
            os.utime(path, (self.now, self.now))
        writer = patch.object(maps, 'save_image', save_at_test_time)
        writer.start()
        self.addCleanup(writer.stop)

    def manager(self, **kwargs):
        args = dict(root=self.tmp.name, lat=43.36, lon=-80.31, zoom=8, fetch=self.provider, clock=lambda: self.now)
        args.update(kwargs)
        return maps.Maps(**args)

    def props(self, m, enabled):
        return m.properties(enabled, HEADINGS, str, WORDS)

    def test_missing_tile_preserves_previous_complete_background(self):
        m = self.manager()
        m.basemap()
        old = m.state['base'].copy()
        tile = next(u for u in self.provider.calls if 'tile.openstreetmap' in u)
        self.now += 604801
        self.provider.fail = lambda url: url == tile
        m.refresh({'gctemp': True})
        self.assertEqual(m.state['base'], old)
        self.assertTrue(m.path(old['file']))

    def test_first_partial_background_never_publishes(self):
        m = self.manager()
        self.provider.fail = lambda url: 'tile.openstreetmap' in url and '/69/' not in url
        m.refresh({'gctemp': True})
        self.assertNotIn('base', m.state)
        self.assertEqual(self.props(m, {'gctemp': True})['Map.2.Time.0'], 'Unavailable')

    def test_zoom_and_coordinates_isolate_cache_and_texture_paths(self):
        old = self.manager()
        old.refresh({'rvradar': True})
        new = self.manager(zoom=10)
        self.assertNotEqual(old.directory, new.directory)
        self.assertNotIn('base', new.state)
        start = len(self.provider.calls)
        new.refresh({'rvradar': True})
        self.assertTrue(any('tilecache.rainviewer' in u for u in self.provider.calls[start:]))
        self.assertNotEqual(self.props(old, {'rvradar': True})['Map.1.Layer.0'], self.props(new, {'rvradar': True})['Map.1.Layer.0'])
        self.assertNotEqual(new.directory, self.manager(zoom=10, lat=43.45).directory)

    def test_radar_disabled_or_missing_preserves_af3_shared_basemap(self):
        m = self.manager()
        m.refresh({'gctemp': True})
        props = self.props(m, {'rvradar': False, 'gctemp': True})
        self.assertEqual(props['Map.1.Area'], props['Map.2.Area'])
        self.assertTrue(Path(props['Map.1.Area']).is_file())
        self.assertEqual(props['Map.1.Heading'], '')
        props = self.props(m, {'rvradar': True, 'gctemp': True})
        self.assertEqual(props['Map.1.Area'], props['Map.2.Area'])

    def test_disabled_layer_does_not_force_enabled_layers_to_refresh(self):
        m = self.manager()
        m.refresh({'gctemp': True, 'gcwind': False})
        # A partial animation should resume in the next service cycle.
        self.assertFalse(m.state['gctemp']['complete'])
        self.now += 301
        start = len(self.provider.calls)
        self.manager().refresh({'gctemp': True, 'gcwind': False})
        self.assertTrue(self.provider.calls[start:])
        self.assertFalse(any('GDPS_15km_Winds_10m' in u for u in self.provider.calls[start:]))
        self.assertNotIn('gcwind', m.state)

    def test_history_reduction_clears_every_legacy_slot(self):
        m = self.manager()
        m.refresh({'rvradar': True, 'gctemp': True})
        window = self.props(m, {'rvradar': True, 'gctemp': True})
        window['Map.8.Layer.47'] = 'legacy-satellite.png'
        window.update(self.props(self.manager(history=4), {'rvradar': True, 'gctemp': True}))
        for kind in (1, 2):
            for i in range(4, 48):
                self.assertEqual(window['Map.{}.Layer.{}'.format(kind, i)], '')
        self.assertEqual(window['Map.8.Layer.47'], '')

    def test_forecast_requests_pin_valid_time_run_and_projection(self):
        m = self.manager()
        m.refresh({'gctemp': True})
        requests = [parse_qs(urlparse(u).query) for u in self.provider.calls if 'REQUEST=GetMap' in u]
        self.assertEqual(len(requests), 2)
        self.assertEqual({q['CRS'][0] for q in requests}, {'EPSG:3857'})
        self.assertEqual({q['DIM_REFERENCE_TIME'][0] for q in requests}, {maps.utc(NOW-3600)})
        self.assertEqual([q['TIME'][0] for q in requests], [maps.utc(NOW), maps.utc(NOW+10800)])
        self.assertFalse(m.state['gctemp']['complete'])
        for _ in range(4):
            self.now += 301
            m.refresh({'gctemp': True})
        self.assertTrue(m.state['gctemp']['complete'])
        props = self.props(m, {'gctemp': True})
        self.assertIn(str(NOW+86400), props['Map.2.Time.0'])
        self.assertIn(str(NOW), props['Map.2.Time.8'])
        self.assertIn(str(NOW), props['Map.2.Layer'])

    def test_run_rollover_does_not_mix_models_on_partial_failure(self):
        m = self.manager()
        for _ in range(5):
            m.refresh({'gctemp': True})
            self.now += 301
        self.assertTrue(m.state['gctemp']['complete'])
        old = m.state['gctemp'].copy()
        self.now += 10801
        self.provider.run += 10800
        self.provider.fail = lambda u: 'REQUEST=GetMap' in u and maps.utc(NOW+21600).replace(':', '%3A') in u
        m.refresh({'gctemp': True})
        # Never replace an existing complete animation with one fresh frame.
        self.assertEqual(old['run'], m.state['gctemp']['run'])
        self.assertEqual(old['frames'], m.state['gctemp']['frames'])
        self.provider.fail = None
        for _ in range(6):
            self.now += 301
            m.refresh({'gctemp': True})
        current = m.state['gctemp']
        self.assertEqual(current['run'], self.provider.run)
        self.assertTrue(current['complete'])
        self.assertTrue(all('_{}_'.format(current['run']) in f['file'] for f in current['frames']))

    def test_canadian_radar_fallback_does_not_mix_providers(self):
        m = self.manager(country='CA')
        self.provider.fail = lambda u: 'Radar_1km_SfcPrecipType' in u
        m.refresh({'rvradar': True})
        self.assertEqual(m.state['rvradar']['source'], 'RainViewer')
        self.assertFalse(m.state['rvradar']['complete'])
        self.now += 301
        self.provider.fail = None
        m.refresh({'rvradar': True})
        self.assertEqual(m.state['rvradar']['source'], 'ECCC')
        self.assertTrue(all('_ECCC_' in f['file'] for f in m.state['rvradar']['frames']))

    def test_rate_budget_keeps_newest_radar_and_retries_incomplete_history(self):
        m = self.manager()
        calls = []
        def fail(url):
            if 'tilecache.rainviewer' in url:
                calls.append(url)
                return '/v2/radar/{}/'.format(NOW) not in url
            return False
        self.provider.fail = fail
        m.refresh({'rvradar': True})
        self.assertEqual(max(f['valid'] for f in m.state['rvradar']['frames']), NOW)
        self.assertFalse(m.state['rvradar']['complete'])
        self.assertIn('/v2/radar/{}/'.format(NOW), calls[0])
        self.now += 301
        self.provider.fail = None
        for _ in range(6):
            m.refresh({'rvradar': True})
            if m.state['rvradar']['complete']:
                break
            self.now += 301
        self.assertTrue(m.state['rvradar']['complete'])

    def test_stale_radar_hidden_forecast_marked_then_expired(self):
        m = self.manager()
        for _ in range(5):
            m.refresh({'rvradar': True, 'gctemp': True})
            self.now += 301
        self.now += 21601
        props = self.props(m, {'rvradar': True, 'gctemp': True})
        self.assertEqual(props['Map.1.Time.0'], 'Unavailable')
        self.assertTrue(props['Map.2.Time.0'].startswith('Cached'))
        self.now += 86400
        self.assertEqual(self.props(m, {'gctemp': True})['Map.2.Time.0'], 'Unavailable')

    def test_missing_legend_does_not_hide_weather_and_recovers(self):
        m = self.manager()
        self.provider.fail = lambda url: 'GetLegendGraphic' in url
        m.refresh({'gctemp': True})
        first = self.props(m, {'gctemp': True})['Map.2.Layer.0']
        self.assertTrue(m.state['gctemp']['frames'])
        self.assertFalse(m.state['gctemp']['complete'])
        self.now += 301
        self.provider.fail = None
        for _ in range(5):
            m.refresh({'gctemp': True})
            if m.state['gctemp']['complete']:
                break
            self.now += 301
        self.assertTrue(m.state['gctemp']['complete'])
        self.assertNotEqual(first, self.props(m, {'gctemp': True})['Map.2.Layer.0'])

    def test_disabled_and_cancelled_updates_avoid_network(self):
        self.manager().refresh({})
        self.manager(aborted=lambda: True).refresh({'gctemp': True})
        self.assertEqual(self.provider.calls, [])

    def test_invalid_image_and_corrupt_manifest(self):
        for content in (b'<ServiceException/>', image_bytes((123, 256)), b'\x89PNG\r\n\x1a\nbroken'):
            with self.assertRaises((ValueError, OSError)):
                maps.png(content, (256, 256))
        m = self.manager()
        m.manifest.write_text('broken')
        self.assertEqual(self.manager().state, {})
        self.assertIsNone(m.path('../other.png'))

    def test_atomic_failure_preserves_previous_file(self):
        path = Path(self.tmp.name) / 'map.png'
        path.write_bytes(b'previous')
        def fail(stream):
            stream.write(b'partial')
            raise OSError('full disk')
        with self.assertRaises(OSError):
            maps.atomic(path, fail)
        self.assertEqual(path.read_bytes(), b'previous')
        self.assertEqual(list(path.parent.glob('*.tmp')), [])


    def test_fresh_fallback_replaces_expiring_complete_radar(self):
        m = self.manager(country='CA', history=12)
        for _ in range(3):
            m.refresh({'rvradar': True})
            self.now += 61
        self.assertTrue(m.state['rvradar']['complete'])
        # Only the newest ECCC frame remains inside the three-hour window.
        self.now = NOW + 10500
        provider = self.provider
        def fresh_fallback(url):
            if 'weather-maps.json' in url:
                return json.dumps({'radar': {'past': [
                    dict(time=self.now-i*600, path='/v2/radar/'+str(self.now-i*600))
                    for i in range(12)
                ]}}).encode()
            return provider(url)
        m.fetch = fresh_fallback
        self.provider.fail = lambda url: 'Radar_1km_SfcPrecipType' in url
        m.refresh({'rvradar': True})
        state = m.state['rvradar']
        self.assertEqual(state['source'], 'RainViewer')
        self.assertEqual(len(state['frames']), 3)
        self.assertEqual(max(f['valid'] for f in state['frames']), self.now)
        self.assertNotIn('Cached', self.props(m, {'rvradar': True})['Map.1.Time.0'])

    def test_failed_new_frame_keeps_compatible_cached_history(self):
        m = self.manager(country='CA')
        m.refresh({'rvradar': True})
        previous = {f['file'] for f in m.state['rvradar']['frames']}
        self.assertEqual(len(previous), 4)
        self.now += 720
        m.metadata = lambda kind: {
            'run': 0, 'style': maps.LAYERS['rvradar'][1],
            'times': [NOW+i*360 for i in range(-9, 3)],
        }
        broken = maps.utc(NOW+360).replace(':', '%3A')
        # Also fail fallback, so publication must retain the usable ECCC history.
        self.provider.fail = lambda url: (
            ('REQUEST=GetMap' in url and broken in url) or 'weather-maps.json' in url
        )
        start = len(self.provider.calls)
        m.refresh({'rvradar': True})
        state = m.state['rvradar']
        self.assertEqual(state['source'], 'ECCC')
        self.assertEqual(len(state['frames']), 5)
        self.assertTrue(previous.issubset({f['file'] for f in state['frames']}))
        self.assertEqual(max(f['valid'] for f in state['frames']), self.now)
        # One successful image and one failed image; no further network burst.
        self.assertEqual(sum('REQUEST=GetMap' in u for u in self.provider.calls[start:]), 2)
        self.assertFalse(state['continuing'])

    def test_equal_fresh_fallback_does_not_shorten_canadian_history(self):
        m = self.manager(country='CA')
        for _ in range(3):
            m.refresh({'rvradar': True})
            self.now += 61
        before = m.state['rvradar']['frames'][:]
        m.sequence('rvradar', False)
        self.assertEqual(m.state['rvradar']['source'], 'ECCC')
        self.assertEqual(m.state['rvradar']['frames'], before)

    def test_healthy_forecast_backfills_in_one_minute_batches(self):
        enabled = {'gctemp': True}
        m = self.manager()
        self.assertEqual(m.refresh(enabled), 60)
        start = len(self.provider.calls)
        self.now += 59
        self.assertEqual(m.refresh(enabled), 1)
        self.assertEqual(len(self.provider.calls), start)
        self.now += 1
        # Reconstruct managers between cycles just like the Kodi adapter does.
        for remaining in (60, 60, 60, 300):
            m = self.manager()
            start = len(self.provider.calls)
            self.assertEqual(m.refresh(enabled), remaining)
            self.assertLessEqual(sum('REQUEST=GetMap' in u for u in self.provider.calls[start:]), 2)
            if remaining == 60:
                self.now += 60
        self.assertEqual(self.now-NOW, 240)
        self.assertTrue(m.state['gctemp']['complete'])
        self.assertEqual(len(m.state['gctemp']['frames']), 9)
        self.assertEqual(m.continuation_delay({'gctemp': False}), 300)

    def test_failed_downloads_and_legends_keep_normal_retry_backoff(self):
        m = self.manager()
        self.provider.fail = lambda url: 'REQUEST=GetMap' in url
        self.assertEqual(m.refresh({'gctemp': True}), 300)
        start = len(self.provider.calls)
        self.now += 60
        self.assertEqual(m.refresh({'gctemp': True}), 300)
        self.assertEqual(len(self.provider.calls), start)
        self.now += 241
        self.provider.fail = lambda url: 'GetLegendGraphic' in url
        self.assertEqual(m.refresh({'gcwind': True}), 300)
        self.assertTrue(m.state['gcwind']['frames'])
        self.assertFalse(m.state['gcwind']['continuing'])

    def test_missing_canadian_legend_does_not_trigger_radar_fallback(self):
        m = self.manager(country='CA')
        self.provider.fail = lambda url: 'GetLegendGraphic' in url
        self.assertEqual(m.refresh({'rvradar': True}), 300)
        self.assertEqual(m.state['rvradar']['source'], 'ECCC')
        self.assertFalse(m.state['rvradar']['legend_ready'])
        self.assertFalse(any('rainviewer' in url for url in self.provider.calls))

    def test_cleanup_keeps_pending_images_until_their_retention_expires(self):
        m = self.manager()
        recent = m.directory / 'pending.png'
        expired = m.directory / 'expired.png'
        maps.save_image(recent, Image.new('RGBA', (10, 10)))
        maps.save_image(expired, Image.new('RGBA', (10, 10)))
        os.utime(expired, (self.now-86401, self.now-86401))
        m.cleanup()
        self.assertTrue(recent.is_file())
        self.assertFalse(expired.exists())

    def test_universal_blue_legend_matches_rainviewer_reference(self):
        # 15/25/35/45/55 dBZ, exact RainViewer colour scheme 2 RGB values.
        self.assertEqual(maps.UNIVERSAL_BLUE_STOPS, (
            (15, '#88ddee', '#9fdfff'),
            (25, '#0077aa', '#5f9fff'),
            (35, '#ffee00', '#3f7fff'),
            (45, '#ff4400', '#1f5fff'),
            (55, '#ffaaff', '#003fff'),
        ))
        legend = self.manager().rv_legend()
        self.assertEqual(legend.getpixel((8, 28)), (136, 221, 238, 255))
        self.assertEqual(legend.getpixel((8, 83)), (159, 223, 255, 255))

    def test_bounded_healthy_canadian_radar_does_not_trigger_fallback(self):
        m = self.manager(country='CA', history=12)
        m.refresh({'rvradar': True})
        self.assertIn('rvradar', m.state)
        self.assertEqual(m.state['rvradar']['source'], 'ECCC')
        self.assertFalse(m.state['rvradar']['complete'])
        self.assertFalse(any('weather-maps.json' in u or 'tilecache.rainviewer' in u
                             for u in self.provider.calls))

    def test_partial_canadian_radar_triggers_fallback(self):
        m = self.manager(country='CA', history=4)
        broken = maps.utc(NOW-360).replace(':', '%3A')
        self.provider.fail = lambda url: (
            'REQUEST=GetMap' in url and 'Radar_1km_SfcPrecipType' in url
            and broken in url
        )
        m.refresh({'rvradar': True})
        self.assertTrue(any('weather-maps.json' in u for u in self.provider.calls))
        self.assertIn('rvradar', m.state)
        self.assertEqual(m.state['rvradar']['source'], 'RainViewer')

    def test_complete_fallback_retries_preferred_canadian_radar(self):
        m = self.manager(country='CA', history=4)
        self.provider.fail = lambda url: (
            'REQUEST=GetMap' in url and 'Radar_1km_SfcPrecipType' in url
        )
        m.refresh({'rvradar': True})
        self.now += 301
        m.refresh({'rvradar': True})
        self.assertEqual(m.state['rvradar']['source'], 'RainViewer')
        self.assertTrue(m.state['rvradar']['complete'])
        self.provider.fail = None
        self.now += 301
        start = len(self.provider.calls)
        m.refresh({'rvradar': True})
        self.assertTrue(any('Radar_1km_SfcPrecipType' in u for u in self.provider.calls[start:]))
        self.assertEqual(m.state['rvradar']['source'], 'ECCC')
        self.assertTrue(m.state['rvradar']['complete'])

    def test_embedded_legend_and_marker_fit_af3_visible_area(self):
        m = self.manager()
        image = m.annotate(Image.new('RGBA', (768, 768)), Image.new('RGBA', (76, 275), 'red'))
        self.assertEqual(image.getpixel((630, 200)), (255, 0, 0, 255))
        x, y = map(round, m.marker)
        self.assertNotEqual(image.getpixel((x, y))[3], 0)
        self.assertEqual(image.getpixel((30, 550))[3], 255)


class TimeAndGeometryTests(unittest.TestCase):
    def test_time_ranges_and_bad_intervals(self):
        value = maps.utc(NOW)+'/'+maps.utc(NOW+7200)+'/PT1H'
        self.assertEqual(maps.time_values(value), [NOW, NOW+3600, NOW+7200])
        self.assertEqual(maps.time_values(value.replace('PT1H', 'PT0S')), [])
        self.assertEqual(maps.time_values('invalid'), [])

    def test_capabilities_select_run_and_style(self):
        name, style = maps.LAYERS['gctemp']
        info = maps.capabilities(metadata('gctemp'), name, style)
        self.assertEqual(info['run'], NOW-3600)
        self.assertEqual(info['style'], style)
        self.assertEqual(len(info['times']), 31)

    def test_wms_extents_wrap_dateline_and_clip_poles(self):
        radius = math.pi*6378137
        segments = mercator_segments(0, 1, 2)
        self.assertEqual(sum(s[1] for s in segments), 768)
        self.assertEqual([s[3] for s in segments], [0, 256])
        for bbox, w, h, x, y in segments:
            self.assertGreaterEqual(bbox[0], -radius)
            self.assertLessEqual(bbox[2], radius)
        self.assertEqual(mercator_segments(2, 0, 2)[0][2], 512)
        self.assertEqual(mercator_segments(2, 0, 2)[0][4], 256)

    def test_marker_is_exact_location_within_central_tile(self):
        x, y, point = location_tile(43.36, -80.31, 8)
        self.assertTrue(all(256 <= p < 512 for p in point))
        self.assertEqual(location_tile(0, 180, 8), location_tile(0, -180, 8))


if __name__ == '__main__':
    unittest.main()
