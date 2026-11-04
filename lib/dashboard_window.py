"""Remote-first full-screen window. All GUI updates stay in the foreground loop."""
import queue
import time

import xbmc
import xbmcgui

from .dashboard_data import Playback

VIEWS = ('overview', 'hourly', 'daily', 'maps')
LAYERS = ('rvradar', 'gctemp', 'gcwind', 'gccloud', 'gcprecip')


class Dashboard(xbmcgui.WindowXML):
    def configure(self, backend, locations, location, translate, initial='overview', zoom=8):
        self.backend, self.locations, self.location = backend, locations, location
        self.t = translate
        self.view = initial if initial in VIEWS else 'overview'
        self.layer, self.regional_zoom, self.zoom = 'rvradar', zoom, zoom
        self.playback = Playback()
        self.closed = False
        self.ready = False
        self.legend_expanded = False
        self.result = {}
        self.last_frame = time.monotonic()
        self.last_detail = None
        self.forecast_signature = None

    def onInit(self):
        self.set_view(self.view)
        self.request()
        self.setFocusId(100 if self.view == 'overview' else 200 if self.view == 'maps' else 300)
        self.ready = True

    def prop(self, key, value):
        self.setProperty('Weather.'+key, str(value if value is not None else ''))

    def request(self):
        self.result = {}
        self.forecast_signature = None
        self.playback.replace([])
        for key in ('Map.Base', 'Map.Image', 'Map.Legend', 'Map.Time', 'Map.Source', 'Map.Units'):
            self.prop(key, '')
        self.show_frame()
        self.prop('Map.Status', self.t(32720))
        self.prop('Map.Heading', self.t(32730+LAYERS.index(self.layer)))
        self.prop('Location', self.locations[self.location]['name'])
        self.prop('Status', self.t(32720))
        self.backend.select(self.location, self.layer, self.zoom)
        for i, name in enumerate(LAYERS):
            self.getControl(200+i).setLabel(('• ' if self.layer == name else '') + self.t(32730+i))
        self.getControl(210).setLabel(('• ' if self.zoom != self.regional_zoom else '') + self.t(32735))
        self.getControl(211).setLabel(('• ' if self.zoom == self.regional_zoom else '') + self.t(32736))

    def set_view(self, view):
        self.view = view
        self.legend_expanded = False
        self.prop('LegendExpanded', 'false')
        self.prop('View', view)
        self.prop('Heading', self.t(32702 if view == 'hourly' else 32703))
        self.prop('Hint', self.t(32713 if view == 'maps' else 32712))
        for i, name in enumerate(VIEWS):
            self.getControl(11+i).setLabel(('• ' if view == name else '') + self.t(32701+i))
        self.populate_list()
        self.last_frame = time.monotonic()

    def populate_list(self):
        control = self.getControl(300)
        position = control.getSelectedPosition()
        control.reset()
        data = self.result.get('forecast', {})
        rows = data.get('days' if self.view == 'daily' else 'hours', [])
        for row in rows:
            item = xbmcgui.ListItem(label=row.get('date', ''))
            for key, value in row.items():
                item.setProperty(key, str(value))
            item.setProperty('when', row.get('shortdate') if self.view == 'daily' else row.get('date', '')+'  '+row.get('time', ''))
            item.setProperty('rowdate', row.get('shortdate') if self.view == 'daily' else row.get('date', ''))
            item.setProperty('rowtime', '' if self.view == 'daily' else row.get('time', ''))
            item.setProperty('temperature', row.get('high', '')+' / '+row.get('low', '') if self.view == 'daily' else row.get('temp', ''))
            item.setProperty('detail', (self.t(32716)+' '+row.get('sunrise', '—')+'   '+self.t(32717)+' '+row.get('sunset', '—'))
                             if self.view == 'daily' else self.t(32715)+' '+row.get('feels', '—'))
            item.setArt({'icon': row.get('icon', '')})
            control.addItem(item)
        if rows:
            control.selectItem(max(0, min(position, len(rows)-1)))
        self.prop('Empty', '' if rows else self.t(32721))

    def render(self, result):
        self.result = result
        data = result['forecast']
        # Layer/zoom changes leave forecast data in place until the same-location
        # snapshot arrives. Location changes explicitly clear the previous city.
        for key in ('temp', 'feels', 'condition', 'icon', 'wind', 'humidity', 'clock'):
            self.prop(key.title(), data.get(key, ''))
        days, hours = data.get('days', []), data.get('hours', [])
        today = days[0] if days else {}
        self.getControl(50).setLabel(data.get('temp', '—'), font='font_musicvis_bigger' if 'arctic.fuse.3' in xbmc.getSkinDir() else 'font60')
        self.prop('Range', self.t(32718)+' '+today.get('high', '—')+'   '+self.t(32719)+' '+today.get('low', '—'))
        self.prop('Sunset', today.get('sunset', '—'))
        self.prop('Later', (self.t(32714)+' '+hours[min(6, len(hours)-1)]['condition']) if hours else '')
        localtime = data['localtime']
        self.prop('Status', self.t(32721) if not data['available'] else
                  (self.t(32722)+' · ' if data['stale'] else '') + 'Open-Meteo · '+self.t(32723)+' '+localtime(data['stamp']).strftime('%a '+data.get('time_format', '%H:%M')))
        for group, rows, fields in (('Hour', hours, ('time', 'temp', 'rain', 'icon')),
                                    ('Day', days, ('day', 'high', 'low', 'icon'))):
            for i in range(7):
                for key in fields:
                    self.prop('{}.{}.{}'.format(group, i, key), rows[i].get(key, '') if i < len(rows) else '')
        signature = (data.get('stamp'), str(hours), str(days))
        if signature != self.forecast_signature:
            self.populate_list()
            self.forecast_signature = signature
        value = result['map']
        self.prop('Map.Base', value['base'])
        self.prop('Map.Legend', value['legend'])
        self.prop('Map.Source', (self.t(32740) if value['source'] == 'ECCC' else value['source']) + '  |  © OpenStreetMap contributors' if value['base'] else '')
        self.prop('Map.Units', '°C' if self.layer == 'gctemp' else 'km/h' if self.layer == 'gcwind' else '')
        self.prop('Map.Status', self.t(32724) if not result['enabled'] else
                  ('' if value['frames'] else self.t(32720 if value['loading'] else 32725)))
        self.playback.replace(value['frames'])
        self.show_frame()

    def show_frame(self):
        frame = self.playback.current
        self.prop('Map.Image', frame.get('path', ''))
        if frame and self.result:
            fmt = self.result['forecast']['localtime']
            time_format = '%a '+self.result['forecast'].get('time_format', '%H:%M')
            stale = self.result['map'].get('stale')
            self.prop('Map.Time', (self.t(32722)+' · ' if stale else '')+fmt(frame['valid']).strftime(time_format))
            first, last = self.playback.frames[0]['valid'], self.playback.frames[-1]['valid']
            self.prop('Map.Range', self.t(32737 if self.layer == 'rvradar' else 32738)+'  '+fmt(first).strftime(time_format)+' — '+fmt(last).strftime(time_format))
            self.prop('Map.Count', '{}/{}'.format(self.playback.index+1, len(self.playback.frames)))
        else:
            for key in ('Map.Time', 'Map.Range', 'Map.Count'):
                self.prop(key, '')
        self.getControl(225).setPercent(self.playback.progress)
        self.getControl(220).setLabel(self.t(32726 if self.playback.playing else 32727))

    def tick(self):
        if not self.ready or self.closed:
            return
        while True:
            try:
                selection, result = self.backend.results.get_nowait()
            except queue.Empty:
                break
            if selection == (self.location, self.layer, self.zoom):
                self.render(result)
        now = time.monotonic()
        interval = 2 if self.playback.index == len(self.playback.frames)-1 else 0.9
        if self.view in ('overview', 'maps') and not self.legend_expanded and self.playback.playing and len(self.playback.frames) > 1 and now-self.last_frame >= interval:
            self.playback.step(1)
            self.show_frame()
            self.last_frame = now

    def onClick(self, control):
        if control == 10:
            ids = list(self.locations)
            choice = xbmcgui.Dialog().select(self.t(32706), [self.locations[i]['name'] for i in ids])
            if choice >= 0:
                self.location = ids[choice]
                self.getControl(50).setLabel('—')
                for key in ('Temp', 'Feels', 'Condition', 'Icon', 'Wind', 'Humidity', 'Sunset', 'Range', 'Later'):
                    self.prop(key, '')
                for group, fields in (('Hour', ('time', 'temp', 'rain', 'icon')), ('Day', ('day', 'high', 'low', 'icon'))):
                    for i in range(7):
                        for field in fields:
                            self.prop('{}.{}.{}'.format(group, i, field), '')
                self.getControl(300).reset()
                self.request()
        elif control in range(11, 15):
            self.set_view(VIEWS[control-11])
            self.setFocusId(100 if self.view == 'overview' else 200 if self.view == 'maps' else 300)
        elif control == 15:
            xbmcgui.Dialog().ok(self.t(32700), self.t(32728))
        elif control in (100, 101, 102):
            self.set_view({100: 'maps', 101: 'hourly', 102: 'daily'}[control])
            self.setFocusId(200 if control == 100 else 300)
        elif control in range(200, 205):
            self.layer = LAYERS[control-200]
            self.request()
        elif control in (210, 211):
            self.zoom = min(10, self.regional_zoom+2) if control == 210 else self.regional_zoom
            self.request()
        elif control in (220, 221):
            self.playback.playing = not self.playback.playing
            self.show_frame()
        elif control in (222, 223):
            self.playback.step(-1 if control == 222 else 1, manual=True)
            self.show_frame()
        elif control == 224:
            if self.result.get('map', {}).get('legend'):
                self.legend_expanded = True
                self.prop('LegendExpanded', 'true')
                self.setFocusId(240)
            else:
                xbmcgui.Dialog().ok(self.t(32747), self.t(32739))
        elif control == 240:
            self.legend_expanded = False
            self.prop('LegendExpanded', 'false')
            self.setFocusId(224)
        elif control == 300:
            item = self.getControl(300).getSelectedItem()
            if item:
                xbmcgui.Dialog().ok(item.getProperty('when'), item.getProperty('condition')+'\n'+item.getProperty('detail')+'\n'+self.t(32709)+' '+item.getProperty('wind'))

    def onAction(self, action):
        key = action.getId()
        if key in (10, 92):
            if self.legend_expanded:
                self.onClick(240)
                return
            if self.view == 'overview':
                self.shutdown()
            else:
                self.set_view('overview')
                self.setFocusId(100)
        elif self.view == 'maps' and key in (1, 2) and self.getFocusId() == 221:
            self.playback.step(-1 if key == 1 else 1, manual=True)
            self.show_frame()
        elif self.view == 'maps' and key in (12, 79, 229):
            self.playback.playing = not self.playback.playing
            self.show_frame()

    def shutdown(self):
        self.closed = True
        self.backend.stop()
        self.close()
