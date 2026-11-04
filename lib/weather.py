import os
import xbmc

from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from . import config
from . import maps
from . import utils
from . import conv

class Main():

	### MAIN
	def __init__(self, locid, mode='kodi'):

		if utils.monitor.abortRequested():
			return

		# Import API only when needed
		if mode == 'download' or mode == 'geoip' or locid.startswith('loc'):
			global api
			from . import api

		# GeoIP
		if mode == 'geoip':
			api.getloc(locid)
			return

		# Search
		if locid.startswith('loc'):
			api.setloc(locid[3])
			return

		# Init
		self.init(locid, mode)

		if not utils.setting(f'loc{locid}'):
			utils.log(f'[LOC{locid}] Not configured', 1)
			return

		# Download
		if self.mode == 'download':

			# Weather
			if utils.lastupdate(f'loc{locid}data') >= 3600:
				with ThreadPoolExecutor(3) as pool:
					pool.map(self.getdata, config.map)

				if api.network():
					utils.setupdate(f'loc{locid}data')

			# Snapshot location/settings once; each layer owns its cache and freshness.
			self.mapmanager().refresh(self.enabled_maps())

		# Update
		elif self.mode == 'update' or self.mode == 'kodi':

			# Wait for service thread
			if self.mode == 'kodi':
				utils.monitor.waitForService()

			# KODI
			if not config.addon.full:
				for map in config.map:
					self.setdata(map)

				self.setother()
				utils.setprops()

			# SKIN
			if config.addon.skin:
				config.addon.api = True
				config.loc.prop  = {}

				for map in config.map:
					self.setdata(map)

				self.setother()
				utils.setprops()

		# Update locs
		elif self.mode == 'updatelocs':

			# KODI
			self.setlocs()
			utils.setprops()

			# SKIN
			if config.addon.skin:
				config.addon.api = True
				config.loc.prop  = {}

				self.setlocs()
				utils.setprops()

		# Notification (Queue)
		elif self.mode == 'msgqueue':
			for map in config.map:
				self.msgqueue(map)

		# Notification (Send)
		elif self.mode == 'msgsend':
			self.notification()

	### INIT
	def init(self, locid, mode):

		if mode == 'download':
			utils.log(f'[LOC{locid}] Initialising: mode={mode}, neterr={config.neterr}, net={api.network()}, dnscache={len(config.dnscache)}', 3)
		else:
			utils.log(f'[LOC{locid}] Initialising: mode={mode}', 3)

		# Location
		config.loc(locid)

		# Vars
		self.mode     = mode
		self.data     = {}
		self.today    = utils.dt('nowloc').strftime('%Y-%m-%d')

		# Directory
		p = Path(f'{config.addon_cache}/{locid}')
		p.mkdir(parents=True, exist_ok=True)

	### GET DATA
	def getdata(self, type):
		utils.log(f'[LOC{config.loc.id}] Downloading data: {type}', 3)
		api.getdata(type, config.loc.id, [ config.loc.lat, config.loc.lon, self.today ])

	### SET DATA
	def setdata(self, type):

		# Data
		self.data[type] = utils.getfile(f'{config.loc.id}/{type}.json')
		if not self.data[type]:
			utils.log(f'No {type} data for location {config.loc.id}', 2)
			return

		# Index
		indexnow = utils.index("now", self.data[type])
		indexmid = utils.index("mid", self.data[type])
		indexday = utils.index("day", self.data[type])

		# Update data
		utils.log(f'[LOC{config.loc.id}] Updating data: {type}', 3)

		for map in config.map.get(type):

			# Current (Compatibility)
			if map[0] == 'current':
				self.setmap(type, map)

			# Current (Advanced)
			elif map[0] == 'currentskin' and config.addon.api:
				self.setmap(type, map)

			# Current (KODI)
			elif map[0] == 'currentkodi' and self.mode == 'kodi':
				self.setmap(type, map)

			# Hourly (Compatibility)
			elif map[0] == 'hourly':
				self.setmulti(type, [ map, 'hourly', indexnow, config.maxhours, config.minhours, 'hourly' ])

				if config.addon.enablehour:
					self.setmulti(type, [ map, 'hourly', indexmid, config.maxhours, config.minhours, 'hour' ])

			# Hourly (Advanced)
			elif map[0] == 'hourlyskin' and config.addon.api:
				self.setmulti(type, [ map, 'hourly', indexnow, config.maxhours, config.minhours, 'hourly' ])

				if config.addon.enablehour:
					self.setmulti(type, [ map, 'hourly', indexmid, config.maxhours, config.minhours, 'hour' ])

			# Daily (Compatibility)
			elif map[0] == 'daily':
				self.setmulti(type, [ map, 'daily', indexday, config.maxdays, config.mindays, 'daily' ])

				if not config.addon.api:
					self.setmulti(type, [ map, 'daily', indexday, config.maxdays, config.mindays, 'day' ])

			# Daily (Advanced)
			elif map[0] == 'dailyskin' and config.addon.api:
				self.setmulti(type, [ map, 'daily', indexday, config.maxdays, config.mindays, 'daily' ])

			# Daily (KODI)
			elif map[0] == 'dailykodi' and self.mode == 'kodi':
				self.setmulti(type, [ map, 'daily', indexday, config.maxdays, config.mindays, 'daily' ])
				self.setmulti(type, [ map, 'daily', indexday, config.maxdays, config.mindays, 'day' ])

			# TimeOfDay
			elif map[0] == 'timeofday':
				self.setmap(type, map)

			# Graph
			elif map[0] == 'graph':
				self.setmap(type, map)

			# Alert
			if map[3] == 'graph':
				self.setalert(type, [ map, indexnow ])

	### SET CURRENT
	def setcurrent(self, type, locid):

		# Data
		self.data[type] = utils.getfile(f'{locid}/{type}.json')
		if not self.data[type]:
			utils.log(f'No {type} data for location {locid}', 2)
			return

		# Update data
		utils.log(f'[LOC{locid}] Updating current: {type}', 3)

		for map in config.map.get(type):

			# Current (Compatibility)
			if map[0] == 'current':
				self.setmap(type, map, locid=locid)

			# Current (Advanced)
			elif map[0] == 'currentskin' and config.addon.api:
				self.setmap(type, map, locid=locid)

	### SET LOCATIONS
	def setlocs(self):
		locs = 0
		for locid in range(1, config.addon.maxlocs):
			loc     = utils.setting(f'loc{locid}')
			locuser = utils.setting(f'loc{locid}user')

			if loc:
				locs += 1

				# Set "Current.X" only if called from service
				if self.mode != 'kodi':
					for map in config.map:
						self.setcurrent(map, locid)

				if locuser:
					utils.addprop(f'location{locid}', locuser)
				else:
					utils.addprop(f'location{locid}', loc)
			else:
				utils.addprop(f'location{locid}', '')

		utils.addprop('locations', locs)

	## SET ALERT
	def setalert(self, src, map):
		winprops = [ 'name', 'value', 'icon', 'unit', 'time', 'hours', 'status' ]

		data   = self.data[src]
		type   = map[0][2][1]
		idx    = map[1]
		prop   = config.alert.map[type]['type']
		unit   = config.alert.map[type]['unit']
		icon   = config.alert.map[type]['icon']
		name   = utils.locaddon(config.alert.map[type]['loc'])
		hours  = 0
		code   = 0
		value  = 0

		# Invalid index
		if not idx:
			utils.log('Index invalid, weather data is not up to date ...', 3)
			return

		# Alert disabled
		if not utils.setting(f'alert_{prop}_enabled', 'bool', True):
			utils.log(f'Disabled alert: {prop}', 3)

			utils.addprop(f'alert.{prop}', '')
			for winprop in winprops:
				utils.addprop(f'alert.{prop}.{winprop}', '')

			return

		# Checking alert
		utils.log(f'Checking alert: {prop}', 3)
		l  = []
		ll = []

		for index in range(idx, idx + config.addon.alerthours):

			try:
				v  = int(data[map[0][1][0]][map[0][1][1]][index])
				vv = int(data[map[0][1][0]]['time'][index])
			except:
				if not self.mode == 'msgqueue':
					utils.addprop(f'alert.{prop}', 0)
					for winprop in winprops:
						utils.addprop(f'alert.{prop}.{winprop}', '')
				return
			else:
				l.append(v)
				ll.append(vv)

		for c, d in [(x, y) for x in [ 3, 2, 1 ] for y in [ 'high', 'low', 'wmo' ] ]:
			alert = f'alert_{prop}_{d}_{c}'
			last  = False

			try:
				if d == 'wmo':
					limit = list(config.alert.map[type][alert].split(' '))
				else:
					limit = int(config.alert.map[type][alert])
			except:
				continue

			for idx, v in enumerate(l):

				if d == 'high':
					if v >= limit:
						hours += 1
						if last and v > last:
							code, value, last, stamp = c, v, v, ll[idx]
						elif not last:
							code, value, last, stamp = c, v, v, ll[idx]
				elif d == 'low':
					if v <= limit:
						hours += 1
						if last and v < last:
							code, value, last, stamp = c, v, v, ll[idx]
						elif not last:
							code, value, last, stamp = c, v, v, ll[idx]
				elif d == 'wmo':
					for wmo in limit:
						if v == int(wmo):
							hours += 1
							if last and v > last:
								code, value, last, stamp = c, v, v, ll[idx]
							elif not last:
								code, value, last, stamp = c, v, v, ll[idx]
			if hours:
				break

		# Check alert code
		if code != 0:
			icon = f'{icon}{code}'
			time  = conv.time('time', stamp)

			if prop == 'condition':
				icon  = f'{config.map_alert_condition.get(value)}{code}'
				value = config.localization.wmo.get(f'{value}d')
			else:
				value, unit = conv.item(value, unit)

			# Notification Queue
			if self.mode == 'msgqueue':
				if code == 1 and utils.setting(f'alert_{prop}_notice', 'bool'):
					config.addon.msgqueue.append([ f'{config.loc.short} - {utils.locaddon(32340)} ({hours} {utils.locaddon(32288)})', f'({time}) {name}: {value}{unit}', f'{config.addon_icons}/alert/{icon}.png' ])
				elif code == 2 and utils.setting(f'alert_{prop}_caution', 'bool'):
					config.addon.msgqueue.append([ f'{config.loc.short} - {utils.locaddon(32341)} ({hours} {utils.locaddon(32288)})', f'({time}) {name}: {value}{unit}', f'{config.addon_icons}/alert/{icon}.png' ])
				elif code == 3 and utils.setting(f'alert_{prop}_danger', 'bool'):
					config.addon.msgqueue.append([ f'{config.loc.short} - {utils.locaddon(32342)} ({hours} {utils.locaddon(32288)})', f'({time}) {name}: {value}{unit}', f'{config.addon_icons}/alert/{icon}.png' ])

				return

			# Set alert properties
			utils.log(f'Updating alert: {prop} = {code}', 3)
			config.addon.alerts += 1

			utils.addprop(f'alert.{prop}', code)
			utils.addprop(f'alert.{prop}.name', name)
			utils.addprop(f'alert.{prop}.time', time)
			utils.addprop(f'alert.{prop}.hours', hours)
			utils.addprop(f'alert.{prop}.icon', f'{config.addon_icons}/alert/{icon}.png')
			utils.addprop(f'alert.{prop}.value', value)
			utils.addprop(f'alert.{prop}.unit', unit)

			if code == 1:
				utils.addprop(f'alert.{prop}.status', utils.locaddon(32340))
			elif code == 2:
				utils.addprop(f'alert.{prop}.status', utils.locaddon(32341))
			elif code == 3:
				utils.addprop(f'alert.{prop}.status', utils.locaddon(32342))

		else:
			if self.mode == 'msgqueue':
				return

			utils.addprop(f'alert.{prop}', 0)
			for winprop in winprops:
				utils.addprop(f'alert.{prop}.{winprop}', '')

	### SET QUEUE
	def msgqueue(self, type):

		# Data
		self.data[type] = utils.getfile(f'{config.loc.id}/{type}.json')
		if not self.data[type]:
			utils.log(f'No {type} data for location {config.loc.id}', 2)
			return

		# Index
		indexnow = utils.index("now", self.data[type])

		# Update msgqueue
		for map in config.map.get(type):

			# Alert
			if map[3] == 'graph':
				self.setalert(type, [ map, indexnow ])

	### SET MULTI
	def setmulti(self, src, map):
		data  = self.data[src]
		time  = map[1]
		idx   = map[2]
		max   = map[3]
		min   = map[4]
		prop  = map[5]

		if config.addon.api is False and ( prop == 'hourly' or prop == 'daily' ):
			count = 1
		else:
			count = 0

		if not idx:
			utils.log('Index invalid, weather data is not up to date ...', 3)
			return

		for index in range(idx, idx + max, 1):
			map[0][2][0] = prop
			self.setmap(src, map[0], index, count)
			count += 1

		count = -1
		for index in range(idx - 1, idx - min, -1):
			map[0][2][0] = prop
			self.setmap(src, map[0], index, count)
			count -= 1

	### SET MAP
	def setmap(self, src, map, idx=None, count=None, locid=None):
		data = self.data[src]

		# Property
		if idx is not None:
			if map[2][0] == 'day':
				property = f'{map[2][0]}{count}.{map[2][1]}'
			else:
				property = f'{map[2][0]}.{count}.{map[2][1]}'
		else:
			if locid:
				property = f'{map[2][0]}.{locid}.{map[2][1]}'
			else:
				property = f'{map[2][0]}.{map[2][1]}'

		# Content
		try:
			content = utils.getprop(data, map, idx, count)
		except TypeError as e:
			utils.log(f'{property}: {type(e).__name__} {e}', 4)
			utils.addprop(property, '')
		except Exception as e:
			utils.log(f'{property}: {type(e).__name__} {e}', 3)
			utils.addprop(property, '')
		else:
			utils.addprop(property, content)

	### MAPS
	def enabled_maps(self):
		active = utils.setting(f'loc{config.loc.id}maps', 'bool')
		return {kind: active and utils.setting(f'map{kind}', 'bool') for kind in maps.LAYERS}

	def mapmanager(self):
		return maps.Maps(
			f'{config.addon_cache}/{config.loc.id}', config.loc.lat, config.loc.lon,
			config.addon.mapzoom, config.addon.maphistory,
			radar=utils.setting('mapradarsource'),
			country=utils.setting(f'loc{config.loc.id}').split(',')[-1].strip(),
			muted=utils.setting('mapmuted', 'bool'), legends=utils.setting('maplegends', 'bool'),
			fetch=api.geturl if self.mode == 'download' else None,
			aborted=utils.monitor.abortRequested, log=lambda msg: utils.log(msg, 2),
			font_paths=[config.xbmcvfs.translatePath('special://xbmc/media/Fonts/arial.ttf')],
		)

	### PROPERTIES
	def setother(self):
		manager = self.mapmanager()
		words = {key: utils.locaddon(value) for key, value in {
			'loading': 32606, 'unavailable': 32607, 'stale': 32608,
			'radar': 32400, 'forecast': 32485,
		}.items()}
		dateformat = lambda stamp: utils.dt('stamploc', stamp).strftime(config.kodi.date + ' ' + config.kodi.time)
		for key, value in manager.properties(self.enabled_maps(), config.localization.layers,
				dateformat, words, api=config.addon.api).items():
			utils.addprop(key, value)

		# Locations
		utils.addprop('current.location', config.loc.name)
		utils.addprop('location', config.loc.name)
		self.setlocs()

		# Fetched
		for prop in [ 'current', 'weather', 'hourly', 'daily', 'timeofday', 'map' ]:
			utils.addprop(f'{prop}.isfetched', 'true')

		# Other
		utils.addprop('alerts', config.addon.alerts)

		if config.addon.api:
			utils.addprop('icons', config.addon.icons)
			utils.addprop('iconsdir', config.addon_icons)
			utils.addprop('Provider', 'open-meteo.com, rainviewer.com, weather.gc.ca, met.no')
			utils.addprop('ProviderLogo', f'{config.addon_path}/resources/banner.png')
		else:
			utils.addprop('WeatherProvider', 'open-meteo.com, rainviewer.com, weather.gc.ca, met.no')
			utils.addprop('WeatherProviderLogo', f'{config.addon_path}/resources/banner.png')

	### NOTIFICATION
	def notification(self):
		queue    = config.addon.msgqueue
		duration = utils.setting('alert_duration', 'int')

		if queue:
			for alert in queue:
				utils.notification(alert[0], alert[1], alert[2], config.loc.id)
				utils.monitor.waitForAbort(duration)
				if utils.monitor.abortRequested():
					utils.log(f'Abort requested ...', 3)
					break


