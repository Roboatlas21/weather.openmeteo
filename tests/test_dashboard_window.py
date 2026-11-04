"""Remote actions, stale worker replies and safe widget launch dispatch."""
import importlib.util
import queue
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from test_dashboard import model

ROOT=Path(__file__).resolve().parents[1]


class Item:
    def __init__(self,label=''):
        self.props={};self.art={};self.label=label
    def setProperty(self,k,v):self.props[k]=v
    def getProperty(self,k):return self.props.get(k,'')
    def setArt(self,v):self.art.update(v)


class Control:
    def __init__(self):self.items=[];self.position=-1;self.label=''
    def reset(self):self.items=[];self.position=-1
    def getSelectedPosition(self):return self.position
    def selectItem(self,value):self.position=value
    def addItem(self,item):self.items.append(item)
    def getSelectedItem(self):return self.items[self.position] if self.items else None
    def setLabel(self,label,**kwargs):self.label=label
    def setPercent(self,value):self.percent=value


class Window:
    def __init__(self,*args):self.controls={};self.props={};self.focus=100;self.close=Mock()
    def getControl(self,id):return self.controls.setdefault(id,Control())
    def setProperty(self,key,value):self.props[key]=value
    def setFocusId(self,value):self.focus=value
    def getFocusId(self):return self.focus


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.dialog=types.SimpleNamespace(select=Mock(return_value=1),ok=Mock())
        self.gui=types.SimpleNamespace(WindowXML=Window,ListItem=Item,Dialog=lambda:self.dialog)
        self.xbmc=types.SimpleNamespace(getSkinDir=lambda:'skin.arctic.fuse.3',executebuiltin=Mock())
        with patch.dict(sys.modules,{'xbmcgui':self.gui,'xbmc':self.xbmc}):
            spec=importlib.util.spec_from_file_location('lib._dashboard_test',ROOT/'lib/dashboard_window.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.backend=types.SimpleNamespace(results=queue.Queue(),select=Mock(),stop=Mock())
        self.window=module.Dashboard()
        self.window.configure(self.backend,{'1':{'name':'Cambridge'},'2':{'name':'Oakville'}},'1',lambda id:str(id))
        self.window.onInit()

    def result(self):
        data=model();data['localtime']=lambda t:datetime.fromtimestamp(t,timezone.utc)
        return dict(forecast=data,enabled=True,map=dict(base='base.png',frames=[dict(valid=1,path='one.png'),dict(valid=2,path='two.png')],legend='legend.png',source='ECCC',loading=False,stale=False))

    def test_back_returns_to_overview_then_closes_and_stops_worker(self):
        w=self.window;w.onClick(14)
        w.onAction(types.SimpleNamespace(getId=lambda:92))
        self.assertEqual(w.view,'overview');self.assertFalse(w.closed)
        w.onAction(types.SimpleNamespace(getId=lambda:92))
        self.assertTrue(w.closed);self.backend.stop.assert_called_once();w.close.assert_called_once()

    def test_new_location_clears_previous_city_and_ignores_queued_results(self):
        w=self.window;w.render(self.result())
        self.assertEqual(w.props['Weather.Temp'],'8°C')
        self.backend.results.put((('1','rvradar',8),self.result()))
        w.onClick(10)
        self.assertEqual(w.location,'2')
        self.assertEqual(w.props['Weather.Temp'],'')
        self.assertEqual(w.getControl(50).label,'—')
        self.assertEqual(w.props['Weather.Map.Range'],'')
        w.tick()
        self.assertEqual(w.props['Weather.Map.Image'],'')
        self.assertEqual(w.props['Weather.Location'],'Oakville')
        self.backend.select.assert_called_with('2','rvradar',8)

    def test_scrubbing_pauses_and_layer_switch_clears_old_images(self):
        w=self.window;w.render(self.result());w.onClick(14);w.setFocusId(221)
        w.onAction(types.SimpleNamespace(getId=lambda:1))
        self.assertFalse(w.playback.playing)
        self.assertEqual(w.props['Weather.Map.Image'],'one.png')
        w.onClick(201)
        self.assertEqual(w.props['Weather.Map.Image'],'')
        self.backend.select.assert_called_with('1','gctemp',8)

    def test_hourly_and_daily_lists_show_real_rows_and_restore_focus(self):
        w=self.window;w.render(self.result());w.onClick(13)
        self.assertEqual(w.getFocusId(),300)
        self.assertEqual(len(w.getControl(300).items),10)
        w.onClick(12)
        self.assertGreater(len(w.getControl(300).items),24)

    def test_legend_expansion_keeps_back_inside_the_map_first(self):
        w=self.window;w.render(self.result());w.onClick(14);w.onClick(224)
        self.assertTrue(w.legend_expanded)
        self.assertEqual(w.getFocusId(),240)
        w.onAction(types.SimpleNamespace(getId=lambda:92))
        self.assertEqual(w.view,'maps')
        self.assertFalse(w.legend_expanded)
        self.assertEqual(w.getFocusId(),224)

    def test_widget_opens_only_validated_dashboard_routes(self):
        plugin=types.SimpleNamespace(endOfDirectory=Mock())
        addon=types.SimpleNamespace(getAddonInfo=lambda key:'/addons/weather.openmeteo')
        with patch.dict(sys.modules,{'xbmc':self.xbmc,'xbmcgui':self.gui,'xbmcplugin':plugin,'xbmcaddon':types.SimpleNamespace(Addon=lambda id:addon)}):
            spec=importlib.util.spec_from_file_location('widget_test',ROOT/'widget.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            module.main(['plugin://weather.openmeteo','4','?action=open&view=maps&location=2'])
            self.xbmc.executebuiltin.assert_called_with('RunScript(weather.openmeteo,view=maps,location=2)')
            module.main(['plugin://weather.openmeteo','4','?action=open&view=Injected)&location=999'])
            self.xbmc.executebuiltin.assert_called_with('RunScript(weather.openmeteo,view=overview,location=)')
