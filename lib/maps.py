"""Complete, time-addressed maps for Kodi's existing Map.* skin contract.

No Kodi imports: network, cancellation, translations and date formatting are
injected. Files and manifests are committed atomically; display never depends
on mutable global location settings or on partially downloaded tile grids.
"""
import hashlib
import io
import json
import math
import os
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps
from . import maptiles

SIZE = maptiles.CANVAS_SIZE
SCHEMA = 4
HISTORY_SLOTS = 48  # Clear slots left by older versions/settings; stock AF3 reads 24.
CONTINUE_INTERVAL = 60  # Small, healthy batches can resume before the normal service cycle.
RADAR_STALE_AFTER = 1800  # A responding provider may still have stopped publishing radar.
GEOMET = 'https://geo.weather.gc.ca/geomet'
LAYERS = {
    'rvradar': ('Radar_1km_SfcPrecipType', 'SfcPrecipType_Dis'),
    'gctemp': ('GDPS_15km_AirTemp_2m', 'TEMPERATURE-LINEAR'),
    'gcwind': ('GDPS_15km_Winds_10m', 'WINDARROWKMH'),
    'gccloud': ('GDPS_15km_TotalCloudCover', 'CLOUD'),
    'gcprecip': ('GDPS_15km_PrecipType-Significant1h', 'SIGPRECIPITATIONTYPE'),
}
_METADATA = {}

# RainViewer color scheme 2 (Universal Blue), sampled at exact dBZ values.
# Both the rain and snow columns match RainViewer's official color-table CSV.
UNIVERSAL_BLUE_STOPS = (
    (15, '#88ddee', '#9fdfff'),
    (25, '#0077aa', '#5f9fff'),
    (35, '#ffee00', '#3f7fff'),
    (45, '#ff4400', '#1f5fff'),
    (55, '#ffaaff', '#003fff'),
)



def utc(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def timestamp(value):
    return int(datetime.fromisoformat(value.strip().replace('Z', '+00:00')).timestamp())


def time_values(value):
    """Decode WMS lists/ranges, with bounded expansion for untrusted metadata."""
    values = set()
    for part in value.split(','):
        try:
            if '/' not in part:
                values.add(timestamp(part))
                continue
            start, end, duration = part.split('/')
            match = re.fullmatch(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?', duration)
            if not match:
                continue
            days, hours, minutes, seconds = [int(v or 0) for v in match.groups()]
            step = days * 86400 + hours * 3600 + minutes * 60 + seconds
            first, last = timestamp(start), timestamp(end)
            if step <= 0 or last < first or (last - first) // step > 4096:
                continue
            values.update(range(first, last + 1, step))
        except (ValueError, OverflowError):
            continue
    return sorted(values)


def capabilities(data, name, preferred_style):
    """Read the layer's advertised valid times, model run, and matching style."""
    root = ET.fromstring(data)
    ns = {'w': 'http://www.opengis.net/wms'}
    for layer in root.findall('.//w:Layer', ns):
        if layer.findtext('w:Name', namespaces=ns) != name:
            continue
        dims = {d.get('name'): d for d in layer.findall('w:Dimension', ns)}
        times = time_values(dims['time'].text or '')
        reference = dims.get('reference_time')
        run = timestamp(reference.get('default')) if reference is not None else 0
        styles = [s.findtext('w:Name', namespaces=ns) for s in layer.findall('w:Style', ns)]
        style = preferred_style if preferred_style in styles else next((s for s in styles if s), '')
        if times and style:
            return {'times': times, 'run': run, 'style': style}
    raise ValueError('No usable time/style metadata for ' + name)


def forecast_times(times, now, count):
    """Sample the next 24 hours, including the nearest current forecast step."""
    candidates = [t for t in times if now - 10800 <= t <= now + 86400]
    if not candidates:
        return []
    count = min(max(1, count), 9)
    targets = [now + i * 86400 / max(1, count - 1) for i in range(count)]
    return sorted({min(candidates, key=lambda t: abs(t - target)) for target in targets})


def png(data, size=None):
    if not data or not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Map service did not return PNG')
    with Image.open(io.BytesIO(data)) as image:
        if image.format != 'PNG' or max(image.size) > 2048 or (size and image.size != size):
            raise ValueError('Unexpected map dimensions')
        image.load()
        return image.convert('RGBA')


def atomic(path, writer):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            writer(stream)
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_image(path, image):
    atomic(path, lambda stream: image.save(stream, format='PNG'))


class Maps:
    def __init__(self, root, lat, lon, zoom, history=12, radar='auto', country='',
                 muted=True, legends=True, fetch=None, aborted=lambda: False,
                 log=lambda message: None, font_paths=(), clock=time.time, decorated=True):
        self.lat, self.lon, self.zoom = float(lat), float(lon), max(2, min(10, int(zoom)))
        self.history = max(4, min(24, int(history)))
        self.radar = radar if radar in ('auto', 'rainviewer', 'geomet') else 'auto'
        self.prefer_geomet = self.radar == 'geomet' or (self.radar == 'auto' and country == 'CA')
        self.muted, self.legends = muted, legends
        self.decorated = decorated
        self.fetch, self.aborted, self.log, self.clock = fetch, aborted, log, clock
        self.x, self.y, self.marker = maptiles.location_tile(self.lat, self.lon, self.zoom)
        key = json.dumps([SCHEMA, self.lat, self.lon, self.zoom, self.prefer_geomet, muted, legends])
        if not decorated:
            key += ':fullscreen-v1'
        self.directory = Path(root) / 'maps' / hashlib.sha256(key.encode()).hexdigest()[:16]
        self.directory.mkdir(parents=True, exist_ok=True)
        self.manifest = self.directory / 'state.json'
        self.state = self.read_state()
        self.font = ImageFont.load_default()
        for path in font_paths:
            try:
                self.font = ImageFont.truetype(str(path), 18)
                break
            except OSError:
                continue

    def read_state(self):
        try:
            state = json.loads(self.manifest.read_text(encoding='utf-8'))
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def commit(self):
        data = json.dumps(self.state, sort_keys=True).encode('utf-8')
        atomic(self.manifest, lambda stream: stream.write(data))

    def path(self, name):
        # A damaged/edited cache manifest must never reference outside its grid.
        if not isinstance(name, str) or Path(name).name != name:
            return None
        path = self.directory / name
        return path if path.is_file() else None

    def image(self, url, size=None):
        if self.aborted():
            raise ValueError('Map update cancelled')
        return png(self.fetch(url), size)

    def metadata(self, kind):
        name, style = LAYERS[kind]
        now = self.clock()
        cached = _METADATA.get(name)
        if cached and 0 <= now - cached[0] < 300:
            return cached[1]
        query = urlencode({'SERVICE': 'WMS', 'VERSION': '1.3.0', 'REQUEST': 'GetCapabilities', 'LAYERS': name})
        result = capabilities(self.fetch(GEOMET + '?' + query), name, style)
        _METADATA[name] = (now, result)
        return result

    def basemap(self):
        """Keep the last complete background until every replacement tile arrives."""
        old = self.state.get('base', {})
        if self.path(old.get('file')) and 0 <= self.clock() - old.get('updated', 0) < 604800:
            return True
        tiles = [(x, y) for y in range(self.y - 1, self.y + 2)
                 for x in range(self.x - 1, self.x + 2) if 0 <= y < (1 << self.zoom)]
        def download(tile):
            x, y = tile
            url = 'https://tile.openstreetmap.org/{}/{}/{}.png'.format(self.zoom, x % (1 << self.zoom), y)
            return tile, self.image(url, (256, 256))
        with ThreadPoolExecutor(max_workers=3) as pool:
            images = list(pool.map(download, tiles))  # Any missing tile aborts publication.
        base = Image.new('RGBA', (SIZE, SIZE), '#242b34')
        for (x, y), image in images:
            base.paste(image, ((x - self.x + 1) * 256, (y - self.y + 1) * 256))
        if not self.decorated:
            # Invert only luminance: roads/text stay legible on a dark TV map.
            base = ImageOps.colorize(ImageOps.invert(ImageOps.grayscale(base)),
                                    '#0d1b27', '#98b9cd').convert('RGBA')
        elif self.muted:
            base = ImageEnhance.Color(base).enhance(0.35)
            base = ImageEnhance.Brightness(base).enhance(0.78)
        if self.legends and self.decorated:
            ImageDraw.Draw(base).rectangle((522, 143, 751, 497), fill='white')
        if self.decorated:
            ImageDraw.Draw(base).rectangle((22, 532, 590, 560), fill='#101820')
        self.draw_marker(base)
        # Unique paths also invalidate Kodi's texture cache after weekly renewal.
        name = 'osm_{}.png'.format(int(self.clock()))
        save_image(self.directory / name, base)
        self.state['base'] = {'file': name, 'updated': int(self.clock())}
        self.commit()
        return True

    def draw_marker(self, image):
        x, y = self.marker
        draw = ImageDraw.Draw(image)
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill='white', outline='#102030', width=2)
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill='#087ee8')

    def legend(self, kind, style):
        name = 'legend_{}_{}.png'.format(kind, hashlib.sha256(style.encode()).hexdigest()[:10])
        path = self.path(name)
        if path:
            with Image.open(path) as image:
                return image.convert('RGBA')
        query = urlencode({'SERVICE': 'WMS', 'VERSION': '1.3.0', 'REQUEST': 'GetLegendGraphic',
                           'SLD_VERSION': '1.1.0', 'LAYER': LAYERS[kind][0], 'STYLE': style, 'FORMAT': 'image/png'})
        image = self.image(GEOMET + '?' + query)
        save_image(self.directory / name, image)
        return image

    def annotate(self, image, legend=None, source='ECCC'):
        """Draw inside stock AF3's crop/header/footer safe area (y=130..560)."""
        image = image.copy()
        if not self.decorated:
            self.draw_marker(image)
            return image
        draw = ImageDraw.Draw(image)
        if self.legends and legend is not None:
            legend = legend.copy()
            resample = Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS
            legend.thumbnail((215, 340), resample)
            x, y = 529 + (215 - legend.width) // 2, 150
            draw.rectangle((522, 143, 751, 497), fill='white')
            image.alpha_composite(legend, (x, y))
        elif self.legends:
            draw.text((536, 170), 'Legend unavailable', font=self.font, fill='black')
        self.draw_marker(image)
        credit = source + ' | © OpenStreetMap contributors'
        draw.rectangle((22, 532, 590, 560), fill='#101820')
        draw.text((29, 536), credit, font=self.font, fill='white')
        return image

    def rv_legend(self):
        # Universal Blue official palette: rain and snow, values in dBZ.
        # https://www.rainviewer.com/files/rainviewer_api_colors_table.csv
        image = Image.new('RGBA', (210, 111), 'white')
        draw = ImageDraw.Draw(image)
        for row, (title, color_index) in enumerate((('Rain (dBZ)', 1), ('Snow (dBZ)', 2))):
            y = row * 55
            draw.text((3, y), title, font=self.font, fill='black')
            for i, stop in enumerate(UNIVERSAL_BLUE_STOPS):
                dbz, color = stop[0], stop[color_index]
                x = 4 + i * 40
                draw.rectangle((x, y + 23, x + 34, y + 33), fill=color)
                draw.text((x + 2, y + 35), str(dbz), font=self.font, fill='black')
        return image

    def wms_image(self, kind, valid, run, style):
        output = Image.new('RGBA', (SIZE, SIZE))
        for bbox, width, height, x, y in maptiles.mercator_segments(self.x, self.y, self.zoom):
            query = {'SERVICE': 'WMS', 'VERSION': '1.3.0', 'REQUEST': 'GetMap',
                     'CRS': 'EPSG:3857', 'BBOX': ','.join(str(v) for v in bbox),
                     'WIDTH': width, 'HEIGHT': height, 'LAYERS': LAYERS[kind][0],
                     'STYLES': style, 'TIME': utc(valid), 'FORMAT': 'image/png', 'TRANSPARENT': 'TRUE'}
            if run:
                query['DIM_REFERENCE_TIME'] = utc(run)
            # Interpolate continuous temperature/cloud fields; preserve categorical precipitation.
            if kind in ('gctemp', 'gccloud'):
                query['INTERPOLATION'] = 'TRUE'
            tile = self.image(GEOMET + '?' + urlencode(query), (width, height))
            output.paste(tile, (x, y))
        return output

    def rv_image(self, stamp, path):
        layout = maptiles.radar_tile_layout(self.x, self.y, self.zoom)
        native = Image.new('RGBA', (layout['columns'] * 256, layout['rows'] * 256))
        def download(tile):
            x, y, px, py = tile
            url = 'https://tilecache.rainviewer.com{}/256/{}/{}/{}/2/1_1.png'.format(path, layout['zoom'], x, y)
            return px, py, self.image(url, (256, 256))
        with ThreadPoolExecutor(max_workers=3) as pool:
            images = list(pool.map(download, layout['tiles']))
        for x, y, tile in images:
            native.paste(tile, (x, y))
        affine = Image.Transform.AFFINE if hasattr(Image, 'Transform') else Image.AFFINE
        bilinear = Image.Resampling.BILINEAR if hasattr(Image, 'Resampling') else Image.BILINEAR
        return native.transform((SIZE, SIZE), affine, layout['affine'], resample=bilinear)

    def sequence(self, kind, geomet=True):
        """Build a bounded batch; return completion, image failure and legend readiness.

        Files are named by provider, model run, style and valid timestamp. New
        frames can therefore be accumulated over several service cycles without
        publishing a mixture of two forecast runs or exhausting free radar APIs.
        """
        now = int(self.clock())
        if geomet:
            info = self.metadata(kind)
            run, style = info['run'], info['style']
            if kind != 'rvradar' and not run:
                raise ValueError('Forecast model run is missing')
            if kind == 'rvradar':
                times = [t for t in info['times'] if 0 <= now - t <= 10800][-self.history:]
            else:
                times = forecast_times(info['times'], now, self.history)
            source = 'ECCC'
            legend, legend_ready = None, True
            if self.legends:
                try:
                    legend = self.legend(kind, style)
                except (OSError, ValueError, TypeError) as exc:
                    self.log('{} legend unavailable: {}'.format(kind, exc))
                    legend_ready = False
            frames = [(t, None) for t in times]
        else:
            data = json.loads(self.fetch('https://api.rainviewer.com/public/weather-maps.json'))
            frames = [(t, path) for t, path in maptiles.radar_frames(data)
                      if -900 <= now - t <= 10800][-self.history:]
            run, style, source, legend = 0, 'universal-blue', 'RainViewer', self.rv_legend()
            legend_ready = True

        if not frames:
            raise ValueError('No current map times available')

        radar = kind == 'rvradar'
        # Limit first-run downloads. Finish current frames before older history
        # and let short continuation passes fill the animation gradually.
        budget = (4 if geomet else 3) if radar else 2
        order = list(reversed(frames)) if radar else sorted(
            frames, key=lambda frame: (abs(frame[0] - now), frame[0]))
        completed = []
        requests = 0
        failed = False
        for valid, path in order:
            if self.aborted():
                break
            style_id = hashlib.sha256((style + str(legend_ready)).encode()).hexdigest()[:8]
            filename = '{}_{}_{}_{}_{}.png'.format(kind, source, run, style_id, valid)
            if not self.path(filename):
                if requests >= budget or failed:
                    continue
                requests += 1
                try:
                    image = self.wms_image(kind, valid, run, style) if geomet else self.rv_image(valid, path)
                    save_image(self.directory / filename, self.annotate(image, legend, source))
                except (OSError, ValueError, TypeError) as exc:
                    self.log('{} frame {} unavailable: {}'.format(kind, valid, exc))
                    failed = True
                    # Stop network requests, but still collect compatible cached frames.
                    continue
            completed.append({'valid': valid, 'file': filename})

        if not completed:
            raise ValueError('No complete frames')

        completed.sort(key=lambda frame: frame['valid'])
        new_state = {
            'frames': completed, 'run': run, 'source': source, 'style': style,
            'updated': now, 'attempt': now,
            'complete': len(completed) == len(frames) and legend_ready,
            'history': self.history, 'legend_ready': legend_ready,
        }

        old = self.state.get(kind, {})
        old_available = [
            f for f in old.get('frames', [])
            if isinstance(f, dict) and isinstance(f.get('valid'), int)
            and self.path(f.get('file'))
            and ((-900 <= now - f['valid'] <= 10800) if radar else
                 (0 <= now - old.get('updated', 0) <= 86400
                  and now - 10800 <= f['valid'] <= now + 86400))
        ]
        same_sequence = (
            old.get('run') == run and old.get('source') == source
            and old.get('style') == style
            and old.get('legend_ready') == legend_ready
        )

        if same_sequence:
            # Keep usable history after a failed request or a moving time window.
            # Never merge providers, model runs, styles or legend variants.
            by_time = {f['valid']: f for f in old_available}
            by_time.update((f['valid'], f) for f in completed)
            times = sorted(by_time)
            times = times[-self.history:] if radar else forecast_times(times, now, self.history)
            new_state['frames'] = [by_time[t] for t in times]

        # A metadata refresh, or rediscovery of PNGs already on disk, does not
        # make forecast imagery new. Use actual completed image write times,
        # never the time we last attempted a provider request.
        saved_times = []
        for frame in new_state['frames']:
            image = self.path(frame['file'])
            if image:
                try:
                    saved_times.append(int(image.stat().st_mtime))
                except OSError:
                    continue
        new_state['updated'] = min(now, max(saved_times)) if saved_times else old.get('updated', 0)
        desired_times = {stamp for stamp, _ in frames}
        new_state['complete'] = legend_ready and desired_times.issubset(
            {frame['valid'] for frame in new_state['frames']})

        keep_old = False
        if old_available and not same_sequence:
            if radar:
                old_newest = max(f['valid'] for f in old_available)
                new_newest = max(f['valid'] for f in new_state['frames'])
                # Never replace fresher radar with older observations, even
                # when the older provider reports a "complete" sequence.
                keep_old = (old_newest > new_newest
                            or (not new_state['complete'] and
                                old_newest == new_newest and
                                (len(old_available) > len(new_state['frames']) or
                                 (len(old_available) == len(new_state['frames'])
                                  and old.get('source') == 'ECCC'))))
            elif not new_state['complete']:
                # Keep a useful coherent model run while its successor fills.
                keep_old = (old.get('complete') and now - old.get('updated', 0) <= 21600
                            and len(old_available) > len(new_state['frames']))
        if keep_old:
            # The candidate frames remain on disk and are reused next pass,
            # but AF3 continues displaying the previous coherent sequence.
            old['attempt'] = now
            self.state[kind] = old
        else:
            self.state[kind] = new_state
        self.commit()
        # A bounded-but-healthy initial download is not an outage. Only
        # actual provider errors should trigger the Canadian radar fallback.
        newest_observation = max(f['valid'] for f in new_state['frames']) if radar else None
        return new_state['complete'], failed, legend_ready, newest_observation

    def refresh(self, enabled):
        """Refresh enabled layers independently; one unavailable layer cannot block others."""
        if not any(enabled.values()) or self.aborted():
            return
        try:
            self.basemap()
        except (OSError, ValueError, TypeError) as exc:
            self.log('Background unavailable: ' + str(exc))
            self.state['base_attempt'] = int(self.clock())
            self.commit()
        if not self.path(self.state.get('base', {}).get('file')):
            return
        for kind in LAYERS:
            if not enabled.get(kind) or self.aborted():
                continue
            old = self.state.get(kind, {})
            interval = 600 if kind == 'rvradar' else 10800
            now = int(self.clock())
            age = now - old.get('updated', 0)
            latest_old_radar = max(
                (f.get('valid', 0) for f in old.get('frames', [])
                 if isinstance(f, dict)), default=0)
            check_preferred_radar = (kind == 'rvradar' and self.prefer_geomet
                                     and (old.get('source') == 'RainViewer'
                                          or now - latest_old_radar > RADAR_STALE_AFTER))
            if (old.get('complete') and not old.get('continuing')
                    and old.get('history') == self.history and 0 <= age < interval
                    and not check_preferred_radar
                    and all(self.path(f['file']) for f in old.get('frames', []))):
                continue
            retry_interval = CONTINUE_INTERVAL if old.get('continuing') else 240
            if 0 <= now - old.get('attempt', 0) < retry_interval:
                continue
            try:
                complete, failed, legend_ready, newest = self.sequence(
                    kind, kind != 'rvradar' or self.prefer_geomet)
            except (OSError, ValueError, TypeError, KeyError, ET.ParseError) as exc:
                self.log('{} unavailable: {}'.format(kind, exc))
                complete, failed, legend_ready, newest = False, True, False, None
            # GeoMet can reply successfully with an old, complete catalogue.
            # Prefer a fresh RainViewer observation to a silently stalled feed.
            stale_eccc = (kind == 'rvradar' and self.prefer_geomet
                          and newest is not None and now - newest > RADAR_STALE_AFTER)
            if (kind == 'rvradar' and self.prefer_geomet
                    and (failed or stale_eccc) and not self.aborted()):
                try:
                    rv_complete, rv_failed, rv_legend, _ = self.sequence(kind, False)
                    if self.state.get(kind, {}).get('source') == 'RainViewer':
                        # Backfill the displayed fallback on the normal healthy
                        # 60-second cadence without repeatedly replacing it with
                        # a complete but stale GeoMet animation.
                        complete, failed, legend_ready = rv_complete, rv_failed, rv_legend
                except (OSError, ValueError, TypeError, KeyError) as fallback_exc:
                    self.log('RainViewer fallback unavailable: ' + str(fallback_exc))
            state = self.state.setdefault(kind, {})
            # Only successful bounded work gets the short cadence. Outages and
            # missing legends retain the normal retry backoff.
            state['continuing'] = not complete and not failed and legend_ready
            state['attempt'] = int(self.clock())
            self.commit()
        self.cleanup()
        return self.continuation_delay(enabled)

    def continuation_delay(self, enabled):
        """Return the next healthy backfill delay; completed/failed layers stay idle."""
        now = self.clock()
        delays = [max(1, CONTINUE_INTERVAL - max(0, now - state.get('attempt', 0)))
                  for kind, state in self.state.items()
                  if enabled.get(kind) and state.get('continuing')]
        return min([300] + delays)

    def cleanup(self):
        # Give Kodi a full day to release previously published textures before deletion.
        keep = {f['file'] for kind in LAYERS for f in self.state.get(kind, {}).get('frames', [])}
        keep.add(self.state.get('base', {}).get('file'))
        now = self.clock()
        for directory in self.directory.parent.iterdir():
            if not directory.is_dir():
                continue
            for path in directory.glob('*.png'):
                if directory == self.directory and (path.name in keep or path.name.startswith('legend_')):
                    continue
                try:
                    if now - path.stat().st_mtime > 86400:
                        path.unlink()
                except OSError:
                    pass

    def placeholder(self, message):
        name = 'status_' + hashlib.sha256(message.encode()).hexdigest()[:10] + '.png'
        if not self.path(name):
            image = Image.new('RGBA', (SIZE, SIZE))
            draw = ImageDraw.Draw(image)
            draw.rectangle((100, 260, 668, 345), fill='#101820')
            draw.text((125, 290), message, font=self.font, fill='white')
            save_image(self.directory / name, image)
        return str(self.directory / name)

    def properties(self, enabled, headings, dateformat, words, api=False):
        """Always clear legacy slots and keep Map.1.Area valid for stock AF3."""
        props = {}
        for i in range(10):
            for field in ('Area', 'Layer', 'Heading', 'Time', 'Legend'):
                props['Map.{}.{}'.format(i, field)] = ''
            for slot in range(HISTORY_SLOTS):
                props['Map.{}.Layer.{}'.format(i, slot)] = ''
                props['Map.{}.Time.{}'.format(i, slot)] = ''
        base = self.path(self.state.get('base', {}).get('file'))
        now = int(self.clock())
        for i, kind in enumerate(LAYERS, 0 if api else 1):
            prefix = 'Map.{}.'.format(i)
            props[prefix + 'Area'] = str(base) if base else ''
            if not enabled.get(kind):
                continue
            state = self.state.get(kind, {})
            radar = kind == 'rvradar'
            age = now - state.get('updated', 0)
            frames = [f for f in state.get('frames', []) if self.path(f['file']) and base
                      and ((-900 <= now - f['valid'] <= 10800) if radar
                           else (0 <= age <= 86400 and now - 10800 <= f['valid'] <= now + 86400))]
            frames = sorted(frames, key=lambda f: f['valid'], reverse=True)
            limit = min(self.history, 12) if radar and state.get('source') == 'RainViewer' else self.history
            if not radar and len(frames) > limit:
                frames = [frames[round(i * (len(frames) - 1) / (limit - 1))] for i in range(limit)]
            else:
                frames = frames[:limit]
            props[prefix + 'Heading'] = headings[kind]
            if not frames:
                status = words['unavailable'] if state.get('attempt') or self.state.get('base_attempt') else words['loading']
                props[prefix + 'Layer'] = props[prefix + 'Layer.0'] = self.placeholder(status)
                props[prefix + 'Time'] = props[prefix + 'Time.0'] = status
                continue
            stale = (now - max(f['valid'] for f in frames) > 1800) if radar else age > 21600
            for slot, frame in enumerate(frames):
                label = '{} | {}'.format(words['radar'] if radar else words['forecast'], dateformat(frame['valid']))
                if stale:
                    label = words['stale'] + ' | ' + label
                props[prefix + 'Layer.' + str(slot)] = str(self.path(frame['file']))
                props[prefix + 'Time.' + str(slot)] = label
            # Static consumers see the newest radar / nearest current forecast.
            current = frames[0] if radar else min(frames, key=lambda f: abs(f['valid'] - now))
            props[prefix + 'Layer'] = str(self.path(current['file']))
            props[prefix + 'Time'] = dateformat(current['valid'])
        return props
