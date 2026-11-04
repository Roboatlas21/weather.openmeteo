"""Behavioral coverage for independent full-screen navigation and data loading."""
import importlib.util
import json
import queue
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from lib.dashboard_data import forecast, Playback
from lib.dashboard_backend import Backend
from lib import maprate
import test_maps as map_fixtures

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 4, 19, 30, tzinfo=timezone.utc).timestamp()
LOCAL = timezone(timedelta(hours=-5))


def data_fixture():
    midnight = datetime(2026, 11, 4, tzinfo=LOCAL).timestamp()
    times = [(NOW//3600)*3600-7200+i*3600 for i in range(52)]
    days = [midnight-86400+i*86400 for i in range(12)]
    return {'timezone': 'America/Toronto',
            'current': dict(time=NOW, temperature_2m=8, apparent_temperature=5,
                            weather_code=61, is_day=1, relative_humidity_2m=82, wind_speed_10m=18),
            'hourly': dict(time=times, temperature_2m=[8]*52, apparent_temperature=[5]*52,
                           weather_code=[61]*52, is_day=[1]*52, wind_speed_10m=[18]*52,
                           precipitation_probability=[70]*52),
            'daily': dict(time=days, temperature_2m_max=[10]*12, temperature_2m_min=[4]*12,
                          weather_code=[61]*12, sunrise=[t+7*3600 for t in days],
                          sunset=[t+17*3600 for t in days], precipitation_probability_max=[80]*12,
                          uv_index_max=[2]*12, wind_speed_10m_max=[25]*12)}


def model(data=None, now=NOW):
    return forecast(data if data is not None else data_fixture(), now,
                    lambda stamp: datetime.fromtimestamp(stamp, LOCAL),
                    lambda n: str(n)+'°C' if isinstance(n, (int,float)) else '—',
                    lambda n: str(n)+' km/h' if isinstance(n, (int,float)) else '—',
                    lambda code, day: 'Rain' if code == 61 else 'Unknown',
                    lambda code, day: str(ROOT/'resources/icons/wmo/61d.png') if code else '',
                    '%I:%M %p')


class ForecastTests(unittest.TestCase):
    def test_past_days_and_location_timezone_do_not_shift_today(self):
        result = model()
        self.assertEqual(result['days'][0]['date'], 'Wednesday 04 November')
        self.assertEqual(result['days'][0]['sunset'], '05:00 PM')
        self.assertEqual(len(result['days']), 10)
        self.assertEqual(result['hours'][0]['time'], '02:00 PM')
        self.assertTrue(result['available'])

    def test_short_arrays_and_missing_values_are_not_zero_weather(self):
        data = data_fixture()
        data['hourly']['temperature_2m'] = []
        data['current']['temperature_2m'] = None
        data['daily']['uv_index_max'] = [None]*12
        result = model(data)
        self.assertEqual(result['temp'], '—')
        self.assertEqual(result['hours'][0]['temp'], '—')
        self.assertEqual(result['days'][0]['uv'], '—')
        self.assertEqual(model({})['hours'], [])
        self.assertEqual(model(dict(hourly=None, daily=[], current=None))['hours'], [])

    def test_expired_current_conditions_are_hidden(self):
        result = model(now=NOW+86401)
        self.assertFalse(result['available'])
        self.assertEqual(result['temp'], '—')
        self.assertEqual(result['icon'], '')

    def test_playback_keeps_selected_time_and_manual_scrub_pauses(self):
        p = Playback()
        p.replace([dict(valid=t, path=str(t)) for t in (10,20,30)])
        self.assertEqual(p.current['valid'], 30)
        p.step(-1, manual=True)
        self.assertFalse(p.playing)
        p.replace([dict(valid=t, path=str(t)) for t in (20,30,40)])
        self.assertEqual(p.current['valid'], 20)
        p.step(-1)
        self.assertEqual(p.current['valid'], 40)
        p.replace([])
        self.assertEqual(p.current, {})
        self.assertEqual(p.progress, 0)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.backend = Backend(Path(self.tmp.name)/'dashboard',
            dict(user_agent='test', radar='auto', maps={'1':False}, layers={'rvradar':True}, time_format='%I:%M %p'),
            {'1':dict(lat=43.36,lon=-80.31,country='CA')},
            lambda n:str(n),lambda n:str(n),lambda *args:'Rain',lambda *args:'')
        self.addCleanup(self.backend.session.close)
        self.backend.select('1','rvradar',8)
        clock = patch('lib.dashboard_backend.time.time', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def test_old_selection_is_cancelled_and_never_published(self):
        old = self.backend.selection
        self.backend.select('1','gctemp',8)
        self.assertTrue(self.backend.cancelled(old))
        self.backend.publish(old, {'wrong_city':True})
        self.assertTrue(self.backend.results.empty())
        self.assertIsNone(self.backend.fetch('https://example.invalid', old))

    def test_cached_forecast_load_is_network_free_and_returns_real_rows(self):
        path=Path(self.tmp.name)/'dashboard/1/forecast.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data_fixture()))
        import os
        os.utime(path, (NOW,NOW))
        with patch.object(self.backend, 'fetch', side_effect=AssertionError('Unexpected network')):
            self.backend.update(self.backend.selection)
        result=self.backend.results.get_nowait()[1]
        self.assertFalse(result['enabled'])
        self.assertEqual(result['forecast']['days'][0]['sunset'], '05:00 PM')
        self.assertTrue(result['forecast']['days'])

    def test_fetch_requests_ten_days_and_location_timezone_and_keeps_cache_on_failure(self):
        path=Path(self.tmp.name)/'dashboard/1/forecast.json'
        with patch.object(self.backend, 'fetch', return_value=json.dumps(data_fixture()).encode()) as fetch:
            self.backend.update(self.backend.selection)
        self.assertIn('forecast_days=10', fetch.call_args[0][0])
        self.assertIn('timezone=auto', fetch.call_args[0][0])
        self.assertTrue(path.is_file())
        original=path.read_bytes()
        import os
        os.utime(path, (1,1))
        with patch.object(self.backend, 'fetch', return_value=b'{broken'):
            self.backend.update(self.backend.selection)
        self.assertEqual(path.read_bytes(), original)


class SharedBudgetTests(unittest.TestCase):
    def test_allowance_is_shared_across_callers_and_expires(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertTrue(maprate.reserve(root, clock=lambda:100, limit=2))
            self.assertTrue(maprate.reserve(Path(root), clock=lambda:100, limit=2))
            self.assertFalse(maprate.reserve(root, clock=lambda:100, limit=2))
            self.assertTrue(maprate.reserve(root, clock=lambda:160, limit=2))

    def test_parallel_workers_do_not_lose_reservations(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as root, ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda i:maprate.reserve(root,limit=10), range(18)))
            self.assertEqual(sum(results),10)


class NativeLayoutTests(unittest.TestCase):
    def test_control_ids_navigation_targets_and_textures_exist(self):
        root=ET.parse(ROOT/'resources/skins/Default/1080i/weather-fullscreen.xml')
        controls=root.findall('.//control')
        ids=[int(c.get('id')) for c in controls if c.get('id')]
        self.assertEqual(len(ids),len(set(ids)))
        for c in controls:
            for direction in ('onleft','onright','onup','ondown'):
                for target in c.findall(direction):
                    value=target.text
                    if value.isdigit():self.assertIn(int(value),ids)
            for tag in ('texture','texturefocus','texturenofocus'):
                value=c.findtext(tag)
                if value and not value.startswith('$'):
                    self.assertTrue((ROOT/'resources/skins/Default/media'/value).is_file(),value)
        for id in range(10,15):
            self.assertEqual(len(root.find('.//control[@id="{}"]'.format(id)).findall('ondown')),4)

    def test_all_packaged_ui_textures_decode(self):
        from PIL import Image
        for path in (ROOT/'resources/skins/Default/media').glob('*.png'):
            with self.subTest(texture=path.name), Image.open(path) as image:
                image.verify()

    def test_dispatch_preserves_existing_weather_and_location_actions(self):
        addon=ET.parse(ROOT/'addon.xml')
        for point in ('xbmc.python.script', 'xbmc.python.weather'):
            self.assertEqual(addon.find('.//extension[@point="{}"]'.format(point)).get('library'),'default.py')
        spec=importlib.util.spec_from_file_location('weather_entry',ROOT/'default.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        weather=types.SimpleNamespace(Main=Mock())
        config=types.SimpleNamespace(init=Mock())
        dashboard=types.SimpleNamespace(main=Mock())
        import lib
        with patch.dict(sys.modules,{'lib.weather':weather,'lib.config':config,'dashboard':dashboard}), patch.multiple(lib,weather=weather,config=config,create=True):
            for arg in ('1','loc1','loc5'):module.main([arg])
            self.assertEqual([call.args[0] for call in weather.Main.call_args_list],['1','loc1','loc5'])
            module.main([]);module.main(['view=maps','location=2'])
            self.assertEqual(dashboard.main.call_count,2)

    def test_fullscreen_maps_have_no_skin_legend_panel_and_isolate_cache(self):
        case=map_fixtures.MapTests();case.setUp()
        try:
            regular=case.manager()
            full=case.manager(decorated=False)
            self.assertNotEqual(regular.directory,full.directory)
            full.refresh({'gctemp':True})
            from PIL import Image
            base=Image.open(full.path(full.state['base']['file']))
            self.assertNotEqual(base.getpixel((600,200)),(255,255,255,255))
            frame=Image.open(full.path(full.state['gctemp']['frames'][0]['file']))
            self.assertEqual(frame.getpixel((600,200)),(40,100,150,255))
        finally:case.doCleanups()
