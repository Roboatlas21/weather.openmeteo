"""Immutable forecast rows and playback state, independent of Kodi."""
import math


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def at(section, key, index, default=None):
    values = section.get(key, [])
    return values[index] if isinstance(values, list) and index < len(values) else default


def percent(value):
    return '{}%'.format(round(value)) if number(value) else '—'


def forecast(data, now, localtime, temperature, speed, condition, icon, time_format='%H:%M'):
    """Use timestamps, not array positions: past_days and missing fields are normal."""
    if not isinstance(data, dict):
        data = {}
    hourly, daily, current = (data.get(key) if isinstance(data.get(key), dict) else {}
                              for key in ('hourly', 'daily', 'current'))
    today = localtime(now).date()
    hours, days = [], []
    for i, stamp in enumerate(hourly.get('time') or []):
        if not number(stamp) or stamp <= now - 3600 or stamp > now + 48 * 3600:
            continue
        code, day = at(hourly, 'weather_code', i), at(hourly, 'is_day', i, 1)
        hours.append(dict(stamp=stamp, time=localtime(stamp).strftime(time_format),
                          date=localtime(stamp).strftime('%a %d %b'),
                          temp=temperature(at(hourly, 'temperature_2m', i)),
                          feels=temperature(at(hourly, 'apparent_temperature', i)),
                          rain=percent(at(hourly, 'precipitation_probability', i)),
                          wind=speed(at(hourly, 'wind_speed_10m', i)),
                          condition=condition(code, day), icon=icon(code, day)))
    for i, stamp in enumerate(daily.get('time') or []):
        if not number(stamp) or localtime(stamp).date() < today:
            continue
        code = at(daily, 'weather_code', i)
        def sun(key):
            value = at(daily, key, i)
            return localtime(value).strftime(time_format) if number(value) else '—'
        days.append(dict(stamp=stamp, day=localtime(stamp).strftime('%a'),
                         date=localtime(stamp).strftime('%A %d %B'),
                         shortdate=localtime(stamp).strftime('%a %d %b'),
                         high=temperature(at(daily, 'temperature_2m_max', i)),
                         low=temperature(at(daily, 'temperature_2m_min', i)),
                         rain=percent(at(daily, 'precipitation_probability_max', i)),
                         wind=speed(at(daily, 'wind_speed_10m_max', i)),
                         sunrise=sun('sunrise'), sunset=sun('sunset'),
                         uv=str(at(daily, 'uv_index_max', i)) if number(at(daily, 'uv_index_max', i)) else '—',
                         condition=condition(code, 1), icon=icon(code, 1)))
    code, day = current.get('weather_code'), current.get('is_day', 1)
    result = dict(temp=temperature(current.get('temperature_2m')),
                  feels=temperature(current.get('apparent_temperature')),
                  condition=condition(code, day), icon=icon(code, day),
                  wind=speed(current.get('wind_speed_10m')),
                  humidity=percent(current.get('relative_humidity_2m')),
                  stamp=current.get('time'), hours=hours[:49], days=days[:10])
    result['available'] = number(result['stamp']) and abs(now-result['stamp']) <= 86400
    if not result['available']:
        for field in ('temp', 'feels', 'wind', 'humidity'):
            result[field] = '—'
        result['condition'], result['icon'] = '', ''
    result['stale'] = not result['available'] or now-result['stamp'] > 7200
    return result


class Playback:
    """Keep the selected observation when a background refresh adds frames."""
    def __init__(self):
        self.frames = []
        self.index = 0
        self.playing = True

    def replace(self, frames):
        stamp = self.current.get('valid')
        self.frames = sorted(frames, key=lambda frame: frame['valid'])
        self.index = next((i for i, frame in enumerate(self.frames) if frame['valid'] == stamp),
                          max(0, len(self.frames)-1))

    @property
    def current(self):
        return self.frames[self.index] if self.frames else {}

    def step(self, direction, manual=False):
        if manual:
            self.playing = False
        if self.frames:
            self.index = (self.index + direction) % len(self.frames)
        return self.current

    @property
    def progress(self):
        return 100 * self.index / max(1, len(self.frames)-1)
