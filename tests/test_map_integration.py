"""Exercise the real Kodi adapter with a stub Kodi environment, without network."""
import importlib.util
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from lib import maps

ROOT = Path(__file__).resolve().parents[1]


class KodiAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.props = {}
        self.settings = dict(loc1='Cambridge, Ontario, CA', loc1maps=True,
                             mapradarsource='auto', mapmuted=True, maplegends=True)
        self.settings.update({'map'+k: True for k in maps.LAYERS})
        self.config = types.SimpleNamespace(
            addon_cache=self.tmp.name,
            loc=types.SimpleNamespace(id='1',lat=43.36,lon=-80.31,name='Cambridge'),
            addon=types.SimpleNamespace(mapzoom=8,maphistory=12,api=False,alerts=False),
            localization=types.SimpleNamespace(layers={k:k for k in maps.LAYERS}),
            kodi=types.SimpleNamespace(date='%Y-%m-%d',time='%H:%M'),
            addon_path='addon',
            xbmcvfs=types.SimpleNamespace(translatePath=lambda p:'/nonexistent/font.ttf'),
        )
        self.utils = types.SimpleNamespace(
            setting=lambda k,*args:self.settings.get(k,''),
            monitor=types.SimpleNamespace(abortRequested=lambda:False),
            log=lambda *args:None,locaddon=lambda key:str(key),
            dt=lambda kind,stamp:datetime.fromtimestamp(stamp,timezone.utc),
            addprop=lambda key,value:self.props.__setitem__(key,value),
        )
        dependencies={'xbmc':types.ModuleType('xbmc'),'lib.config':self.config,
                      'lib.utils':self.utils,'lib.conv':types.ModuleType('lib.conv')}
        spec=importlib.util.spec_from_file_location('lib._weather_integration',ROOT/'lib/weather.py')
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,dependencies):spec.loader.exec_module(module)
        module.api=types.SimpleNamespace(geturl=lambda url:None)
        self.main=object.__new__(module.Main)
        self.main.mode='update'
        self.main.setlocs=lambda:None

    def test_stock_af3_properties_are_written_by_actual_weather_adapter(self):
        self.main.setother()
        self.assertTrue(self.props['Map.1.Layer.0'].endswith('.png'))
        self.assertEqual(self.props['Map.2.Heading'],'gctemp')
        self.assertEqual(self.props['Map.8.Layer.47'],'')
        self.assertEqual(self.props['current.location'],'Cambridge')
        self.assertEqual(self.props['map.isfetched'],'true')

    def test_adapter_preserves_alternative_zero_based_skin_contract(self):
        self.config.addon.api=True
        self.config.addon.icons='icons'
        self.config.addon_icons='iconsdir'
        self.main.setother()
        self.assertEqual(self.props['Map.0.Heading'],'rvradar')
        self.assertEqual(self.props['Map.1.Heading'],'gctemp')
        self.assertEqual(self.props['Map.9.Heading'],'')

    def test_per_location_toggle_disables_every_map(self):
        self.settings['loc1maps']=False
        self.main.setother()
        self.assertTrue(all(not self.props['Map.{}.Heading'.format(i)] for i in range(10)))

    def test_adapter_captures_location_and_chooses_canadian_radar(self):
        self.main.mode='download'
        manager=self.main.mapmanager()
        self.assertTrue(manager.prefer_geomet)
        self.config.loc.lat=49.2
        self.config.loc.id='2'
        self.assertEqual(manager.lat,43.36)
        self.assertIn('/1/maps/',str(manager.directory))


class ServiceSchedulingTests(unittest.TestCase):
    def run_service(self, delays, enabled=True):
        calls, waits = [], []
        stopped = [False]
        def wait(seconds):
            waits.append(seconds)
            stopped[0] = True
            return True
        def weather_main(locid, mode):
            calls.append((locid, mode))
            return types.SimpleNamespace(map_retry_delay=delays.get(locid))
        settings = {'loc1': 'Other', 'loc2': 'Selected', 'alert_interval': 15}
        utils = types.SimpleNamespace(
            monitor=types.SimpleNamespace(abortRequested=lambda: stopped[0], waitForAbort=wait),
            setting=lambda key, *args: settings.get(key, False),
            settingrpc=lambda key: (
                ('weather.openmeteo' if enabled else 'another.provider')
                if key == 'weather.addon' else '2'
            ),
            setsetting=lambda *args: None, log=lambda *args: None, geoip=lambda: True,
            time=types.SimpleNamespace(time=lambda: 1000),
            lastupdate=lambda key: 0, setupdate=lambda key: None,
        )
        config = types.SimpleNamespace(
            addon=types.SimpleNamespace(maxlocs=3), addon_info='test',
            init=lambda **kwargs: None,
        )
        api = types.SimpleNamespace(migrate_map_cache=lambda: None,
                                    s=types.SimpleNamespace(close=lambda: None))
        dependencies = {
            'lib.weather': types.SimpleNamespace(Main=weather_main),
            'lib.config': config, 'lib.utils': utils, 'lib.api': api,
        }
        spec = importlib.util.spec_from_file_location('lib._service_integration', ROOT/'lib/service.py')
        module = importlib.util.module_from_spec(spec)
        # Also replace package attributes if a previous test imported a module.
        import lib
        attrs = {name.split('.')[1]: value for name, value in dependencies.items()}
        with patch.dict(sys.modules, dependencies), patch.multiple(lib, create=True, **attrs):
            spec.loader.exec_module(module)
            module.Main()
        return calls, waits

    def test_backfill_shortens_sleep_and_publishes_selected_location_first(self):
        calls, waits = self.run_service({'1': 300, '2': 60})
        self.assertEqual(waits, [60])
        self.assertEqual(calls[:3], [('2', 'download'), ('2', 'update'), ('1', 'download')])

    def test_completed_or_disabled_maps_keep_normal_service_sleep(self):
        for delays in ({'1': 300, '2': 300}, {'1': None, '2': None}):
            with self.subTest(delays=delays):
                self.assertEqual(self.run_service(delays)[1], [300])

    def test_inactive_weather_provider_never_downloads(self):
        calls, waits = self.run_service({'2': 60}, enabled=False)
        self.assertEqual(calls, [])
        self.assertEqual(waits, [300])


if __name__=='__main__':unittest.main()
