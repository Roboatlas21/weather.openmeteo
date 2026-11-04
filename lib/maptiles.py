"""Tile planning and RainViewer metadata validation without Kodi dependencies.

Map rendering uses 768x768 images, centred on a 3x3 OSM tile grid. When
RainViewer's native zoom is lower, the returned affine transform precisely
reprojects the low-zoom radar tiles to the unchanged OSM viewport.
"""

import math

TILE_SIZE = 256
CANVAS_SIZE = TILE_SIZE * 3
RADAR_MAX_ZOOM = 7


def location_tile(lat, lon, zoom):
    """Tile and exact marker position in the shared three-tile viewport."""
    lat = max(-85.05112878, min(85.05112878, float(lat)))
    lon = (float(lon) + 180) % 360 - 180
    size = 1 << zoom
    px = (lon + 180) / 360 * size
    py = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size
    x, y = math.floor(px), max(0, min(size - 1, math.floor(py)))
    return x, y, ((px - x + 1) * TILE_SIZE, (py - y + 1) * TILE_SIZE)


def mercator_segments(x, y, zoom):
    """WMS extents in metres, split at the dateline and clipped at the poles.

    Each result is (bbox, width, height, paste_x, paste_y). The output pixel
    grid is identical to OSM and RainViewer's EPSG:3857 grid.
    """
    world = 1 << zoom
    extent = math.pi * 6378137
    step = 2 * extent / world
    top, bottom = max(0, y - 1), min(world, y + 2)
    left, remaining, offset = (x - 1) % world, 3, 0
    result = []
    while remaining:
        count = min(remaining, world - left)
        bbox = (-extent + left * step, extent - bottom * step,
                -extent + (left + count) * step, extent - top * step)
        result.append((bbox, count * TILE_SIZE, (bottom - top) * TILE_SIZE,
                       offset * TILE_SIZE, (top - y + 1) * TILE_SIZE))
        remaining -= count
        offset += count
        left = 0
    return result


def radar_tile_layout(display_x, display_y, display_zoom):
    """Return radar tile requests and an inverse native-pixel transform.

    Each tile is (request_x, request_y, native_paste_x, native_paste_y).
    Request coordinates wrap at the antimeridian; paste coordinates do not.
    The affine tuple maps a 768x768 display pixel into the native canvas.
    """
    if not isinstance(display_zoom, int) or not 2 <= display_zoom <= 15:
        raise ValueError("Invalid display zoom")

    zoom = min(display_zoom, RADAR_MAX_ZOOM)
    scale = 1 << (display_zoom - zoom)
    left = (display_x - 1) * TILE_SIZE
    top = (display_y - 1) * TILE_SIZE
    native_span = TILE_SIZE * scale

    min_x = math.floor(left / native_span)
    min_y = math.floor(top / native_span)
    max_x = math.floor((left + CANVAS_SIZE - 1) / native_span)
    max_y = math.floor((top + CANVAS_SIZE - 1) / native_span)

    tiles = []
    world_size = 1 << zoom
    for y in range(min_y, max_y + 1):
        if not 0 <= y < world_size:
            continue
        for x in range(min_x, max_x + 1):
            tiles.append((x % world_size, y,
                          (x - min_x) * TILE_SIZE,
                          (y - min_y) * TILE_SIZE))

    affine = (
        1 / scale, 0, left / scale - min_x * TILE_SIZE,
        0, 1 / scale, top / scale - min_y * TILE_SIZE,
    )
    return {
        "zoom": zoom,
        "columns": max_x - min_x + 1,
        "rows": max_y - min_y + 1,
        "affine": affine,
        "tiles": tiles,
    }


def radar_frames(index, limit=12):
    """Return sorted unique (timestamp, path) tuples from a RainViewer index.

    Only standard /v2/radar/{timestamp} paths are accepted, so a malformed
    response cannot turn the catalogue into an arbitrary download URL.
    """
    if not isinstance(index, dict):
        return []
    radar = index.get("radar")
    if not isinstance(radar, dict):
        return []
    past = radar.get("past")
    if not isinstance(past, list):
        return []

    valid = {}
    for frame in past:
        if not isinstance(frame, dict):
            continue
        try:
            timestamp = int(frame["time"])
            path = frame["path"]
        except (TypeError, ValueError, KeyError):
            continue
        if timestamp <= 0 or not isinstance(path, str):
            continue
        if path == "/v2/radar/{}".format(timestamp):
            valid[timestamp] = path

    return sorted(valid.items())[-limit:] if limit > 0 else []

