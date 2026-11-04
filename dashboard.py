"""Explicit entry point; the normal weather-provider invocation remains unchanged."""
import sys
from pathlib import Path
from urllib.parse import parse_qs

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from lib import config, conv, utils
from lib.dashboard_backend import Backend
from lib.dashboard_data import number
from lib.dashboard_window import Dashboard


def main(args=None):
    addon = xbmcaddon.Addon('weather.openmeteo')
    t = addon.getLocalizedString
    home = xbmcgui.Window(10000)
    previous = home.getProperty('OpenMeteo.DashboardWindow')
    if previous.isdigit() and xbmc.getCondVisibility('Window.IsVisible({})'.format(previous)):
        xbmc.executebuiltin('ActivateWindow({})'.format(previous))
        return
    config.init(cache=True)
    options = parse_qs('&'.join(args if args is not None else sys.argv[1:]))
    locations = {}
    for i in range(1, 6):
        id = str(i)
        name = addon.getSetting('loc'+id)
        if not name:
            continue
        try:
            lat, lon = float(addon.getSetting('loc'+id+'lat')), float(addon.getSetting('loc'+id+'lon'))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
        except ValueError:
            continue
        locations[id] = dict(name=addon.getSetting('loc'+id+'user') or name,
                             lat=lat, lon=lon, country=name.split(',')[-1].strip(),
                             timezone=addon.getSetting('loc'+id+'tz'))
    if not locations:
        xbmcgui.Dialog().ok(t(32700), t(32729))
        addon.openSettings()
        return
    location = options.get('location', [str(utils.settingrpc('weather.currentlocation'))])[0]
    if location not in locations:
        location = next(iter(locations))
    root = Path(xbmcvfs.translatePath(addon.getAddonInfo('profile')))
    settings = dict(time_format='%I:%M %p' if '%I' in config.kodi.time else '%H:%M',
                    user_agent='weather.openmeteo/{} (Kodi fullscreen weather)'.format(addon.getAddonInfo('version')),
                    radar=addon.getSetting('mapradarsource'),
                    maps={id: addon.getSetting('loc'+id+'maps') == 'true' for id in locations},
                    layers={key: addon.getSetting('map'+key) == 'true' for key in config.localization.layers})
    def temperature(value):
        return str(conv.temp(value)) + conv.temp() if number(value) else '—'
    def speed(value):
        return str(conv.speed(value)) + ' ' + conv.speed() if number(value) else '—'
    def code(value, day):
        return str(int(value))+('d' if day else 'n') if number(value) else ''
    def condition(value, day):
        return config.localization.wmo.get(code(value, day), t(32725))
    def icon(value, day):
        name = code(value, day)
        return str(Path(addon.getAddonInfo('path')) / 'resources' / 'icons' / config.addon.icons / (name+'.png')) if name else ''
    backend = Backend(root/'dashboard', settings, locations, temperature, speed, condition, icon,
                      fonts=[xbmcvfs.translatePath('special://xbmc/media/Fonts/arial.ttf')],
                      log=lambda msg: xbmc.log('[weather.openmeteo] '+msg, xbmc.LOGWARNING))
    window = Dashboard('weather-fullscreen.xml', addon.getAddonInfo('path'), 'Default', '1080i')
    window.configure(backend, locations, location, t, options.get('view', ['overview'])[0],
                     zoom=max(2, min(8, config.addon.mapzoom)))
    monitor = xbmc.Monitor()
    try:
        window.show()
        home.setProperty('OpenMeteo.DashboardWindow', str(xbmcgui.getCurrentWindowId()))
        backend.start()
        while not window.closed and not monitor.waitForAbort(0.1):
            window.tick()
    finally:
        window.shutdown()
        if backend.is_alive():
            backend.join()
        home.clearProperty('OpenMeteo.DashboardWindow')
        del window


if __name__ == '__main__':
    main()
