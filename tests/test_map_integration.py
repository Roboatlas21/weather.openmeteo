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


if __name__=='__main__':unittest.main()
