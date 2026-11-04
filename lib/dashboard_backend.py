"""Background data work for the full-screen window; never writes skin properties."""
import json
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
import pytz

from . import maps, maprate
from .dashboard_data import forecast, number

CURRENT = 'temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,wind_speed_10m'
HOURLY = 'temperature_2m,apparent_temperature,precipitation_probability,weather_code,wind_speed_10m,is_day'
DAILY = ('weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,'
         'uv_index_max,precipitation_probability_max,wind_speed_10m_max')


class Backend(threading.Thread):
    """One cancellable worker; results are consumed by the GUI's own event loop."""
    def __init__(self, root, settings, locations, temperature, speed, condition, icon,
                 fonts=(), log=lambda message: None):
        super().__init__(name='OpenMeteoDashboard', daemon=True)
        self.root, self.settings, self.locations = Path(root), settings, locations
        self.temperature, self.speed, self.condition, self.icon = temperature, speed, condition, icon
        self.fonts, self.log = fonts, log
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.results = queue.Queue(maxsize=2)
        self.selection = None
        self.session = requests.Session()
        self.session.headers['User-Agent'] = settings['user_agent']

    def select(self, location, layer, zoom):
        self.selection = (location, layer, zoom)
        self.wake.set()

    def cancelled(self, selection):
        return self.stop_event.is_set() or selection != self.selection

    def fetch(self, url, selection):
        if self.cancelled(selection):
            return None
        if url.startswith('https://tilecache.rainviewer.com/') and not maprate.reserve(self.root.parent / 'cache'):
            return None
        try:
            response = self.session.get(url, timeout=(6, 8))
            response.raise_for_status()
            # Bound responses before decoding; providers only return small JSON/PNGs.
            return response.content if len(response.content) <= 8 * 1024 * 1024 else None
        except requests.RequestException as exc:
            self.log('Dashboard download failed: {}'.format(exc))
            return None

    def publish(self, selection, value):
        if self.cancelled(selection):
            return
        while True:
            try:
                self.results.put_nowait((selection, value))
                return
            except queue.Full:
                try:
                    self.results.get_nowait()
                except queue.Empty:
                    pass

    def model(self, data):
        try:
            zone = pytz.timezone(data.get('timezone', 'UTC'))
        except (pytz.UnknownTimeZoneError, AttributeError):
            zone = timezone.utc
        localtime = lambda stamp: datetime.fromtimestamp(stamp, timezone.utc).astimezone(zone)
        result = forecast(data, time.time(), localtime, self.temperature, self.speed,
                          self.condition, self.icon, self.settings.get('time_format', '%H:%M'))
        result['time_format'] = self.settings.get('time_format', '%H:%M')
        result['clock'] = localtime(time.time()).strftime(result['time_format'])
        result['localtime'] = localtime
        return result

    def read_forecast(self, path):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def snapshot(self, manager, layer):
        state = manager.state.get(layer, {})
        now = time.time()
        radar = layer == 'rvradar'
        frames = [dict(valid=f['valid'], path=str(manager.path(f['file'])))
                  for f in state.get('frames', [])
                  if manager.path(f.get('file')) and number(f.get('valid'))
                  and ((-900 <= now-f['valid'] <= 10800) if radar else
                       (0 <= now-state.get('updated', 0) <= 86400
                        and now-10800 <= f['valid'] <= now+86400))]
        base = manager.path(manager.state.get('base', {}).get('file'))
        legend = ''
        if frames:
            if state.get('source') == 'RainViewer':
                path = manager.directory / 'legend_rainviewer.png'
                if not path.is_file():
                    maps.save_image(path, manager.rv_legend())
                legend = str(path)
            else:
                import hashlib
                name = 'legend_{}_{}.png'.format(layer, hashlib.sha256(state.get('style', '').encode()).hexdigest()[:10])
                legend = str(manager.path(name) or '')
        return dict(base=str(base or ''), frames=frames if base else [], legend=legend,
                    source=state.get('source', ''), loading=not state.get('attempt'),
                    stale=bool(frames) and (now-max(f['valid'] for f in frames) > 1800
                                          if radar else now-state.get('updated', 0) > 21600))

    def update(self, selection):
        location, layer, zoom = selection
        loc = self.locations[location]
        root = self.root / location
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'forecast.json'
        data = self.read_forecast(path)
        if not data:
            # Reuse current/hourly conditions immediately after an upgrade. The
            # legacy provider's daily buckets are UTC, so wait for local-day data.
            data = self.read_forecast(self.root.parent / 'cache' / location / 'weather.json')
            if data:
                data = dict(data, daily={}, timezone=loc.get('timezone') or data.get('timezone', 'UTC'))
        enabled = self.settings['maps'].get(location, False) and self.settings['layers'].get(layer, False)
        manager = maps.Maps(root, loc['lat'], loc['lon'], zoom, history=12,
                            radar=self.settings['radar'], country=loc['country'],
                            muted=True, legends=True, decorated=False,
                            fetch=lambda url: self.fetch(url, selection),
                            aborted=lambda: self.cancelled(selection), font_paths=self.fonts, log=self.log)
        result = dict(forecast=self.model(data), map=self.snapshot(manager, layer), enabled=enabled)
        self.publish(selection, result)
        now = time.time()
        try:
            fresh = path.is_file() and 0 <= now-path.stat().st_mtime < 3600
        except OSError:
            fresh = False
        if not fresh:
            query = urlencode(dict(latitude=loc['lat'], longitude=loc['lon'], current=CURRENT,
                                   hourly=HOURLY, daily=DAILY, timezone='auto',
                                   timeformat='unixtime', forecast_days=10))
            raw = self.fetch('https://api.open-meteo.com/v1/forecast?' + query, selection)
            if raw:
                try:
                    candidate = json.loads(raw)
                    current = candidate.get('current') if isinstance(candidate, dict) else None
                    if not isinstance(current, dict) or not number(current.get('time')):
                        raise ValueError('Missing forecast data')
                    maps.atomic(path, lambda stream: stream.write(raw))
                    data = candidate
                except (ValueError, TypeError, OSError) as exc:
                    self.log('Dashboard forecast unavailable: {}'.format(exc))
            result['forecast'] = self.model(data)
            self.publish(selection, dict(result))
        if enabled and not self.cancelled(selection):
            manager.refresh({layer: True})
            result['map'] = self.snapshot(manager, layer)
            self.publish(selection, dict(result))

    def run(self):
        try:
            while not self.stop_event.is_set():
                self.wake.clear()
                selection = self.selection
                if selection:
                    try:
                        self.update(selection)
                    except Exception as exc:
                        self.log('Dashboard update failed: {}'.format(exc))
                self.wake.wait(60)
        finally:
            self.session.close()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
