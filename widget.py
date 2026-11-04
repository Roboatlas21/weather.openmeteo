"""AF3-compatible executable widget items that launch the dedicated window."""
import sys
from urllib.parse import parse_qs, urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin


def main(argv=None):
    argv = argv or sys.argv
    handle = int(argv[1])
    options = parse_qs(argv[2].lstrip('?')) if len(argv) > 2 else {}
    addon = xbmcaddon.Addon('weather.openmeteo')
    path = addon.getAddonInfo('path').rstrip('/\\')
    view = options.get('view', ['overview'])[0]
    if view not in ('overview', 'hourly', 'daily', 'maps'):
        view = 'overview'
    location = options.get('location', [''])[0]
    if location not in ('1', '2', '3', '4', '5'):
        location = ''
    if options.get('action') == ['open']:
        xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
        # Use the add-on ID so Kodi supplies its dependency paths and context.
        # Both script/weather extensions dispatch through default.py.
        xbmc.executebuiltin('RunScript(weather.openmeteo,view={},location={})'.format(view, location))
        return
    xbmcplugin.setContent(handle, 'files')
    for view, label in (('overview', 32701), ('hourly', 32702), ('daily', 32703), ('maps', 32704)):
        item = xbmcgui.ListItem(label=addon.getLocalizedString(label))
        item.setArt({'thumb': path+'/resources/skins/Default/media/widget-'+('maps' if view == 'maps' else 'weather')+'.png',
                     'icon': path+'/resources/icon.png', 'fanart': path+'/resources/skins/Default/media/background.png'})
        item.setProperty('IsPlayable', 'false')
        url = 'plugin://weather.openmeteo/?'+urlencode(dict(action='open', view=view, location=location))
        xbmcplugin.addDirectoryItem(handle, url, item, isFolder=False)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


if __name__ == '__main__':
    main()
