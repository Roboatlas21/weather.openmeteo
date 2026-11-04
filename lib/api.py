import os
import io
import socket
import json
import requests
import time
from collections import deque
from threading import Lock
import xbmc
import xbmcgui

from PIL import Image
from pathlib import Path
from requests.adapters import HTTPAdapter, Retry

from . import config
from . import maptiles
from . import utils
from . import weather

# DNS cache
old_getaddrinfo = socket.getaddrinfo

def new_getaddrinfo(*args):
	try:
		return config.dnscache[args]
	except KeyError:
		r = old_getaddrinfo(*args)
		config.dnscache[args] = r
		return r

socket.getaddrinfo = new_getaddrinfo

# Requests
r = Retry(total=2, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
s = requests.Session()
s.headers.update(config.addon_ua)
s.mount('https://', HTTPAdapter(max_retries=r))

# Network
def network():
	if config.neterr > 10:
		return False
	else:
		return True

# Keep within the public RainViewer API's announced rate limit.
# Multiple locations and simultaneous tile workers share this process-wide budget.
_radar_tile_times = deque()
_radar_tile_lock = Lock()
_radar_index_cache = (0, [])


def _reserve_radar_request():
    now = time.monotonic()
    with _radar_tile_lock:
        while _radar_tile_times and now - _radar_tile_times[0] >= 60:
            _radar_tile_times.popleft()
        if len(_radar_tile_times) >= 90:
            return False
        _radar_tile_times.append(now)
        return True


def _save_map(image, path):
    """Write a completed image atomically so Kodi never sees a partial PNG."""
    target = Path(path)
    tmp = target.with_suffix(".png.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(tmp), format="PNG")
        os.replace(str(tmp), str(target))
        return True
    except (OSError, ValueError) as exc:
        utils.log(f"Map save failed: {target} ({exc})", 2)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def migrate_map_cache():
    """Invalidate the old radar warning images and retired GeoMet layer tiles."""
    cache_root = Path(config.addon_cache)
    cache_root.mkdir(parents=True, exist_ok=True)
    marker = cache_root / ".map-schema-2026-11-v2"
    if marker.is_file():
        return

    for locid in range(1, config.addon.maxlocs):
        path = cache_root / str(locid)
        if path.is_dir():
            for layer in ("rvradar", "rvsatellite", "gctemp", "gcwind", "gccloud", "gcprecip"):
                for file in path.glob(layer + "_*.png"):
                    try:
                        file.unlink()
                    except OSError as exc:
                        utils.log(f"Could not clear old map {file}: {exc}", 2)
        if utils.setting(f"loc{locid}"):
            utils.setsetting(f"loc{locid}rv", "0")
            utils.setsetting(f"loc{locid}gc", "0")

    try:
        marker.write_text("2026-11 map service migration\n", encoding="utf-8")
    except OSError as exc:
        utils.log(f"Could not record map cache migration: {exc}", 2)


# Get url
def geturl(url, head=False):

	if url.startswith("https://tilecache.rainviewer.com/") and not _reserve_radar_request():
		utils.log("RainViewer tile rate budget exhausted; retrying next update", 2)
		return None

	# Network timeout
	if network():
		timeout = 6
	else:
		timeout = 1

	# Download
	try:
		if head:
			utils.log(f'Checking: {url}', 3)
			r = s.head(url, timeout=timeout)
		else:
			utils.log(f'Download: {url}', 3)
			r = s.get(url, timeout=timeout)

	except Exception as e:
		utils.log(f'Download: {url} ({e})', 3)
		config.dnscache = {}
		config.neterr  += 1
		return None

	else:
		config.neterr = 0

		if r.ok:
			utils.log(f'Download: {url} ({r.status_code})', 3)
			return r.content
		else:
			utils.log(f'Download: {url} ({r.status_code})', 2)
			config.dnscache = {}
			return None

# Get data
def getdata(type, loc, map=None):

	# URL
	if type == 'weather':
		url = config.map_api.get(type).format(map[0], map[1], config.maxdays)
	elif type == 'airquality':
		url = config.map_api.get(type).format(map[0], map[1])
	elif type == 'sun':
		url = config.map_api.get(type).format(map[0], map[1], map[2])
	elif type == 'moon':
		url = config.map_api.get(type).format(map[0], map[1], map[2])

	# Weather
	file = f'{config.addon_cache}/{loc}/{type}.json'
	data = geturl(url)

	if data:
		with open(Path(file), 'wb') as f:
			f.write(data)

# Get Map ( 0:loc, 1:type, 2:count, 3:z, 4:x, 5:y,
#           6:paste_x, 7:paste_y, 8:path, 9:time, 10-13:bbox )
def getmap(map, head=False):
    kind = map[1]
    if kind == "osm":
        # Wrap across the antimeridian, but never request invalid polar rows.
        if not 0 <= map[5] < (1 << map[3]):
            return False
        url = config.map_api[kind].format(map[3], map[4] % (1 << map[3]), map[5])
    elif kind == "rvradar":
        url = config.map_api[kind].format(map[8], map[3], map[4], map[5])
    elif kind in ("gctemp", "gcwind", "gccloud", "gcprecip"):
        url = config.map_api[kind].format(map[10], map[11], map[12], map[13])
    else:
        return False

    if head:
        return geturl(url, head=True)

    data = geturl(url)
    if not data or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        utils.log(f"Skipping invalid or unavailable {kind} map tile", 2)
        return False

    # WMS errors can be returned as HTTP 200 XML; don't store those, or
    # substitute a missing-tile warning that would appear in AF3's animation.
    try:
        with Image.open(io.BytesIO(data)) as tile:
            if tile.format != "PNG" or tile.size != (256, 256):
                raise ValueError(f"Unexpected {kind} tile dimensions or format")
            tile.verify()
    except (OSError, ValueError) as exc:
        utils.log(f"Invalid {kind} PNG tile: {exc}", 2)
        return False

    config.mapcache[kind][map[2]] = data
    return True


def _tile_data(kind, index):
    data = config.mapcache.get(kind, {}).get(index)
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as tile:
            return tile.convert("RGBA")
    except (OSError, ValueError):
        return None


def mapmerge(map):
    """Merge the original 3x3 map, with correct 768-pixel dimensions."""
    if not map:
        return False
    image = Image.new("RGBA", (maptiles.CANVAS_SIZE, maptiles.CANVAS_SIZE))
    rendered = 0
    for item in map:
        tile = _tile_data(item[1], item[2])
        if tile is not None:
            image.paste(tile, (item[6], item[7]))
            rendered += 1
    if not rendered:
        return False

    loc, kind, timestamp = map[0][0], map[0][1], map[0][9]
    suffix = f"{kind}.png" if kind == "osm" else f"{kind}_{timestamp}.png"
    return _save_map(image, f"{config.addon_cache}/{loc}/{suffix}")


def mapmerge_radar(map, layout):
    """Resample zoom-7 radar imagery into the OSM map's original viewport.

    An inverse affine transform avoids shifting the rain relative to roads
    when the user selects zoom 8, 9 or 10 in Kodi's weather settings.
    """
    if not map:
        return False
    native = Image.new("RGBA", (
        layout["columns"] * maptiles.TILE_SIZE,
        layout["rows"] * maptiles.TILE_SIZE,
    ))
    rendered = 0
    for item in map:
        tile = _tile_data("rvradar", item[2])
        if tile is not None:
            native.paste(tile, (item[6], item[7]))
            rendered += 1
    # Do not save a half-loaded radar mosaic as a seemingly valid frame.
    if rendered != len(map):
        return False

    affine_mode = Image.Transform.AFFINE if hasattr(Image, "Transform") else Image.AFFINE
    bilinear = Image.Resampling.BILINEAR if hasattr(Image, "Resampling") else Image.BILINEAR
    output = native.transform(
        (maptiles.CANVAS_SIZE, maptiles.CANVAS_SIZE),
        affine_mode, layout["affine"], resample=bilinear,
    )
    timestamp = map[0][9]
    loc = map[0][0]
    return _save_map(output, f"{config.addon_cache}/{loc}/rvradar_{timestamp}.png")


def getrvframes():
    """Return current RainViewer past-radar frames, cached for five minutes."""
    global _radar_index_cache
    now = time.monotonic()
    cached_at, cached_frames = _radar_index_cache
    if cached_frames and now - cached_at < 300:
        return cached_frames

    data = geturl(config.map_api["rvindex"])
    try:
        frames = maptiles.radar_frames(json.loads(data), limit=12)
        now_utc = utils.dt("nowutcstamp")
        frames = [(stamp, path) for stamp, path in frames
                  if -900 <= now_utc - stamp <= 10800]
    except (TypeError, ValueError, UnicodeDecodeError):
        frames = []
    if frames:
        _radar_index_cache = (now, frames)
    else:
        utils.log("RainViewer past radar catalogue unavailable", 2)
    return frames


# Get location (GeoIP)
def getloc(locid):
	utils.log(f'Geolocation ...')

	# Get location
	try:
		data    = json.loads(geturl(config.map_api.get('geoip')))
		city    = data['city']
		region  = data.get('region_name')
		country = data.get('country_code')

		# Search
		data     = json.loads(geturl(config.map_api.get('search').format(city)))
		location = data['results'][0]

		for item in data['results']:

			if country and region:
				if country in location['country_code'] and region in location['admin1']:
					location = item
					break

			if country:
				if country in location['country_code']:
					location = item
					break
	except Exception as e:
		utils.log(f'Geolocation: Unknown ({e})')
	else:
		utils.log(f'Geolocation: {location["name"]}, {location["admin1"]}, {location["country_code"]} [{location["latitude"]}, {location["longitude"]}]')
		utils.setsetting(f'loc{locid}', f'{location["name"]}, {location["admin1"]}, {location["country_code"]}')
		utils.setsetting(f'loc{locid}lat', str(location["latitude"]))
		utils.setsetting(f'loc{locid}lon', str(location["longitude"]))
		utils.setsetting(f'loc{locid}tz', str(location["timezone"]))

# Clear location
def clearloc(locid, last=False):
	if last:
		utils.setsetting(f'loc{locid}data', '321318000')
		utils.setsetting(f'loc{locid}map', '321318000')
		utils.setsetting(f'loc{locid}rv', '321318000')
		utils.setsetting(f'loc{locid}gc', '321318000')
	else:
		utils.setsetting(f'loc{locid}', '')
		utils.setsetting(f'loc{locid}user', '')
		utils.setsetting(f'loc{locid}alert', 'true')
		utils.setsetting(f'loc{locid}utz', 'false')
		utils.setsetting(f'loc{locid}tz', '')
		utils.setsetting(f'loc{locid}lat', '0')
		utils.setsetting(f'loc{locid}lon', '0')

# Set location
def setloc (locid):
	utils.log(f'Search dialog ...')

	dialog   = xbmcgui.Dialog()
	input    = utils.setting(f'loc{locid}')
	keyboard = xbmc.Keyboard(input, utils.loc(14024), False)
	keyboard.doModal()

	if keyboard.isConfirmed():
		search = keyboard.getText()

		# No changes
		if search == input:
			utils.log(f'[LOC{locid}] No changes')

		# Remove location
		elif search == '':
			check = utils.setting(f'loc{int(locid)+1}')

			if not check:
				utils.log(f'[LOC{locid}] Removed')
				clearloc(locid)
				clearloc(locid, True)

		# Search location
		else:
			try:
				locs   = []
				url    = config.map_api.get('search').format(search)
				data   = json.loads(geturl(url))['results']
			except:
				utils.log('[LOC{locid}] No results')
				dialog.ok('Open-meteo', utils.loc(284))
			else:
				for item in data:
					li = xbmcgui.ListItem(f'{item.get("name")}, {item.get("admin1")}, {item.get("country_code")} (Lat: {item.get("latitude")}, Lon: {item.get("longitude")})')
					locs.append(li)

				select = dialog.select(utils.loc(396), locs, useDetails=True)

				if select != -1:

					# Cleanup cache dir
					dir   = f'{config.addon_cache}/{locid}'
					files = sorted(list(Path(dir).glob('*')))

					for file in files:
						os.remove(file)

					# Set location
					utils.log(f'Location {locid}: {data[select].get("name")}, {data[select].get("admin1")}, {data[select].get("country_code")} {data[select].get("latitude")} {data[select].get("longitude")}')
					utils.setsetting(f'loc{locid}', f'{data[select].get("name")}, {data[select].get("admin1")}, {data[select].get("country_code")}')
					utils.setsetting(f'loc{locid}lat', data[select]["latitude"])
					utils.setsetting(f'loc{locid}lon', data[select]["longitude"])
					utils.setsetting(f'loc{locid}tz', data[select]["timezone"])

					# Wait for settings dialog
					while xbmcgui.getCurrentWindowDialogId() == 10140:
						utils.log(f'Waiting for settings dialog ...')
						utils.monitor.waitForAbort(1)

						if utils.monitor.abortRequested():
							return

					# Cleanup lastupdate
					clearloc(locid, True)

					# Refresh
					if int(utils.settingrpc("weather.currentlocation")) == int(locid):
						weather.Main(str(locid), mode='download')
						weather.Main(str(locid), mode='update')
					else:
						weather.Main(str(locid), mode='download')
						weather.Main(str(locid), mode='updatelocs')

