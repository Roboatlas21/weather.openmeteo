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

### Validation

With Python 3 and Pillow installed, run `python -m unittest discover -s tests -v`.
Tests use generated PNG fixtures and fake provider responses; they do not require Kodi
or internet access. Device rendering still needs validation on the target Kodi installation.
