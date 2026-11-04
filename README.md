# Open-Meteo Weather
(weather.openmeteo)

### Features

- No account or API key required
- Geolocation (only on first run)
- Current, hourly, daily and timeofday weather
- Animated maps for stock Arctic Fuse 3: radar, temperature, wind, clouds and precipitation type.
- Automatic radar source: Environment Canada for Canadian locations, RainViewer elsewhere; Canadian radar falls back to RainViewer during outages.
- Forecast maps animate actual valid times over the next 24 hours, from one model run per layer.
- Embedded legends, an exact location marker, a muted background and source attribution work without any skin modification.
- Each location/zoom has an independent cache. Only complete images are published, and cached/unavailable data is labelled.
- RainViewer provides past radar only, with native detail limited to zoom 7; its infrared satellite service is retired.
- No account or API key is needed for weather maps.
- Airquality: AQI, PM25, PM10, ... (Requires skin support)
- Pollen: Alder, Birch, Grass, ... (Requires skin support, Europe only)
- Sun and moon data (Requires skin support)
- Weather alert notifications
- Dedicated full-screen weather window with current conditions, hourly forecast, ten days and animated maps.
- Executable widget entries open the full-screen window from stock AF3, with remote navigation and no skin patches.
- Weather alert properties for skins (Requires skin support)
- Graphs (Requires skin support)
Special window properties that can be used for graphs
- WMO weather codes support (Requires skin support)
WMO icons are more precise than KODIs builtin codes


### Stock Arctic Fuse 3

Select Open-Meteo as Kodi's weather provider and enable maps for the selected location.
The default map options are **Automatic** radar, **12 animation frames**, muted background,
and embedded legends. Existing saved settings are respected. Temperature legends use °C
and the wind-arrow legend uses km/h, independently of the text forecast's unit settings.

AF3 retains its existing stacked map cards, automatic playback speed and fixed overlay
opacity. The provider reserves a matching legend panel in the background so AF3's opacity
does not let roads bleed through the legend. There are no skin patches or extra controls.

Changing zoom or map appearance creates a new cache. A loading message appears until
that view has a complete background and imagery. The selected location is downloaded and
published before other saved locations. The service normally checks every five minutes;
healthy incomplete animations request another bounded pass after about one minute.
Initial downloads remain limited to two new frames per forecast layer and three or four
new radar frames per pass, with the shared RainViewer tile rate limit still enforced.
Near-term weather appears first; a nine-frame forecast normally fills in about four
minutes plus download time. Complete radar refreshes after ten minutes and forecasts
after three hours. Failed downloads and missing legends retain the normal retry backoff.

Usable cached frames survive download failures. Radar selection considers the newest
observation and the number of remaining usable frames, so fresh fallback imagery can
replace stale history immediately. Forecast model runs remain separate, with a useful
previous animation retained while its replacement fills. RainViewer's Universal Blue
legend uses the provider's published rain/snow dBZ colour table.

### Full-screen weather (1.1.0)

Open **Add-ons → Program add-ons → Open-Meteo** and select **Overview** if Kodi
shows the four launch entries. The window has Overview, Hourly, 10 days and Maps pages. It uses your saved locations and forecast unit settings.
Maps must be enabled for the location and layer in Open-Meteo settings.

For a stock AF3 widget, select the **Open-Meteo** program plugin as the widget
source. Its Overview, Hourly, 10 days and Maps entries open the corresponding
full-screen page when selected. The plugin directory is `plugin://weather.openmeteo/`.
You can also add the program add-on to Kodi Favourites, or use these custom actions
where your shortcut editor supports Kodi built-ins:

```
RunScript(weather.openmeteo)
RunScript(weather.openmeteo,view=maps)
```

The plugin source accepts `?location=2` to pin its entries to a saved location slot;
otherwise the window starts with Kodi's selected weather location. Changing location
inside this window does not change Kodi's weather location.

- **Arrows / OK:** navigate tabs, forecast rows, layers and the location selector.
- **Maps:** Local and Regional change the coverage. Focus the timeline and press
  Left / Right to step through frames; manual stepping pauses playback.
- **Play / Pause:** use the on-screen button, OK on the timeline, or the remote's
  Play / Pause key. Expand legend makes the provider's colour key easier to read.
- **Back:** closes the expanded legend first, then returns to Overview, then closes
  the window and returns to the screen that opened it.

The full-screen map has its own dark background, separate legend and frame timestamp.
Temperature and wind legends retain the provider's °C and km/h units. Radar shows
past observations; the other layers show forecast times. Loading, disabled and cached
states are labelled. Forecast dates and times use the selected location's time zone.
The hourly page covers approximately the next 48 hours; the overview shows the current
hour and the next six hours. Map animations fill gradually under the existing bounded
download policy.

The window downloads data on one background worker and uses an independent map cache.
The weather service and window share the RainViewer tile allowance. The existing AF3
weather page continues to receive its original decorated map images. Close and reopen
the window after changing add-on settings.

### Validation

With Python 3, Pillow, Requests and pytz installed, run `python -m unittest discover -s tests -v`.
Tests use generated PNG fixtures and fake provider responses; they do not require Kodi
or internet access. Device rendering still needs validation on the target Kodi installation.
The full-screen tests cover forecast dates, missing values, playback, location changes,
widget launch arguments, Back navigation, map isolation and the shared tile allowance.
The native XML and bundled textures can be regenerated with `python tools/build_dashboard_skin.py`
(the development machine needs DejaVu Sans; Kodi uses the active skin's fonts).
