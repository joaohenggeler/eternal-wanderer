#!/usr/bin/env python3

"""
	A module that defines any general purpose functions used by all scripts, including loading configuration files,
	connecting to the database, and interfacing with Firefox.
"""

import dataclasses
import itertools
import json
import locale
import logging
import msvcrt
import os
import re
import shutil
import sqlite3
import tempfile
import warnings
import winreg
from base64 import b64encode
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from glob import iglob
from math import ceil
from random import random
from subprocess import Popen
from time import sleep
from typing import Any, Optional, Union
from urllib.parse import ParseResult, unquote, urlparse, urlunparse
from winreg import (
	CreateKeyEx, DeleteKey, DeleteValue, EnumKey, EnumValue,
	OpenKey, QueryInfoKey, QueryValueEx, SetValueEx,
)
from xml.etree import ElementTree

import requests
from limits import RateLimitItemPerSecond
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from pywinauto.application import ( # type: ignore
	Application as WindowsApplication,
	ProcessNotFoundError as WindowProcessNotFoundError,
	TimeoutError as WindowTimeoutError,
	WindowSpecification,
)
from requests import RequestException
from requests.adapters import HTTPAdapter, Retry
from selenium import webdriver # type: ignore
from selenium.common.exceptions import ( # type: ignore
	NoSuchElementException, NoSuchWindowException,
	TimeoutException, WebDriverException,
)
from selenium.webdriver.firefox.firefox_binary import FirefoxBinary # type: ignore
from selenium.webdriver.firefox.firefox_profile import FirefoxProfile # type: ignore
from selenium.webdriver.firefox.webdriver import WebDriver # type: ignore
from selenium.webdriver.support import expected_conditions as webdriver_conditions # type: ignore
from selenium.webdriver.support.ui import WebDriverWait # type: ignore
from waybackpy import WaybackMachineCDXServerAPI as Cdx
from waybackpy.cdx_snapshot import CDXSnapshot

def container_to_lowercase(container: Union[list, dict]) -> Union[list, dict]:
	""" Converts the elements of a list or keys of a dictionary to lowercase. """

	if isinstance(container, list):
		return [x.lower() if isinstance(x, str) else x for x in container]
	elif isinstance(container, dict):
		return dict( (key.lower(), value) if isinstance(key, str) else (key, value) for key, value in container.items() )
	else:
		assert False, f'Unhandled container type "{type(container)}".'

class CommonConfig():
	""" The general purpose configuration that applies to all scripts. """

	# From the config file.
	json_config: dict

	debug: bool
	locale: str

	database_path: str
	database_error_wait: int

	gui_webdriver_path: str
	headless_webdriver_path: str
	page_load_timeout: int

	gui_firefox_path: str
	headless_firefox_path: str

	profile_path: str
	preferences: dict[str, Union[bool, int, str]]

	extensions_path: str
	extensions_before_running: dict[str, bool]
	extensions_after_running: dict[str, bool]
	user_scripts: dict[str, bool]

	plugins_path: str
	use_master_plugin_registry: bool
	plugins: dict[str, bool]

	shockwave_renderer: str
	
	java_show_console: bool
	java_add_to_path: bool
	java_arguments: list[str]

	cosmo_player_show_console: bool
	cosmo_player_renderer: str
	cosmo_player_animate_transitions: bool

	_3dvia_renderer: str

	autoit_path: str
	autoit_poll_frequency: int
	autoit_scripts: dict[str, bool]

	recordings_path: str
	max_recordings_per_directory: int
	compilations_path: str

	wayback_machine_rate_limit_amount: int
	wayback_machine_rate_limit_window: int
	
	cdx_api_rate_limit_amount: int
	cdx_api_rate_limit_window: int
	
	save_api_rate_limit_amount: int
	save_api_rate_limit_window: int
	
	rate_limit_poll_frequency: float
	
	wayback_machine_retry_backoff: float
	wayback_machine_retry_max_wait: int

	allowed_domains: list[list[str]] # Different from the config data type.
	disallowed_domains: list[list[str]] # Different from the config data type.
	
	enable_fallback_encoding: bool
	use_guessed_encoding_as_fallback: bool

	ffmpeg_path: Optional[str]
	ffmpeg_global_args: list[str]
	
	language_names: dict[str, str]

	# Determined at runtime.
	default_options: dict

	# Constants.
	TEMPORARY_PATH_PREFIX = 'wanderer.'

	MUTABLE_OPTIONS = [
		# For the recorder script.
		'page_cache_wait',
		'media_cache_wait',
		
		'plugin_load_wait',
		'base_plugin_crash_timeout',

		'viewport_scroll_percentage',
		'base_wait_after_load',
		'wait_after_load_per_plugin_instance',
		'base_wait_per_scroll',
		'wait_after_scroll_per_plugin_instance',
		'base_media_wait_after_load',
		
		'media_fallback_duration',
		'media_width', 
		'media_height',
		'media_background_color',
				
		'plugin_syncing_page_type',
		'plugin_syncing_media_type',
		'plugin_syncing_unload_delay',
		'plugin_syncing_reload_vrml_from_cache',

		'enable_plugin_input_repeater', 
		'plugin_input_repeater_initial_wait',
		'plugin_input_repeater_wait_per_cycle',
		'plugin_input_repeater_min_window_size',
		'plugin_input_repeater_keystrokes',
		
		'enable_cosmo_player_viewpoint_cycler',
		'cosmo_player_viewpoint_wait_per_cycle',

		'min_duration',
		'max_duration',

		'text_to_speech_read_image_alt_text',

		'enable_media_conversion',

		# For the publisher script.
		'show_media_metadata',
	]

	def __init__(self):

		with open('config.json', encoding='utf-8') as file:
			self.json_config = json.load(file)
		
		self.load_subconfig('common')

		self.database_path = os.path.abspath(self.database_path)
		self.gui_webdriver_path = os.path.abspath(self.gui_webdriver_path)
		self.headless_webdriver_path = os.path.abspath(self.headless_webdriver_path)
		self.gui_firefox_path = os.path.abspath(self.gui_firefox_path)
		self.headless_firefox_path = os.path.abspath(self.headless_firefox_path)

		self.profile_path = os.path.abspath(self.profile_path)
		self.extensions_path = os.path.abspath(self.extensions_path)
		self.plugins_path = os.path.abspath(self.plugins_path)
		self.autoit_path = os.path.abspath(self.autoit_path)
		self.recordings_path = os.path.abspath(self.recordings_path)
		self.compilations_path = os.path.abspath(self.compilations_path)
		self.ffmpeg_path = os.path.abspath(self.ffmpeg_path)

		self.extensions_before_running = container_to_lowercase(self.extensions_before_running)
		self.extensions_after_running = container_to_lowercase(self.extensions_after_running)
		self.user_scripts = container_to_lowercase(self.user_scripts)
		self.plugins = container_to_lowercase(self.plugins)
		self.autoit_scripts = container_to_lowercase(self.autoit_scripts)

		self.shockwave_renderer = self.shockwave_renderer.lower()
		self.cosmo_player_renderer = self.cosmo_player_renderer.lower()
		self._3dvia_renderer = getattr(self, '3dvia_renderer').lower()

		def parse_domain_list(domain_list: list[str]) -> list[list[str]]:
			""" Transforms a list of domain patterns into a list of each pattern's components. """

			domain_patterns = []

			if domain_list is not None:
				
				for domain in container_to_lowercase(domain_list):
					
					# Reversed because it makes it easier to work with a snapshot's URL key.
					components = domain.split('.')
					components.reverse()
					domain_patterns.append(components)

					# If the last component was a wildcard, match one or two top or second-level
					# domains (e.g. example.com or example.co.uk).
					if components[0] == '*':
						extra_components = components.copy()
						extra_components.insert(0, '*')
						domain_patterns.append(extra_components)
					
			return domain_patterns

		self.allowed_domains = parse_domain_list(self.allowed_domains)
		self.disallowed_domains = parse_domain_list(self.disallowed_domains)

		self.ffmpeg_global_args = container_to_lowercase(self.ffmpeg_global_args)
		self.language_names = container_to_lowercase(self.language_names)
		
		self.default_options = {}

	def load_subconfig(self, name: str) -> None:
		""" Loads a specific JSON object from the configuration file. """
		self.__dict__.update(self.json_config[name])

		# For apply_snapshot_options().
		for option in CommonConfig.MUTABLE_OPTIONS:
			if hasattr(self, option):
				self.default_options[option] = getattr(self, option)

	def apply_snapshot_options(self, snapshot: 'Snapshot') -> None:
		""" Applies custom options specific to a snapshot to the current configuration. This should only be used by the recorder and publisher scripts. """

		for option in CommonConfig.MUTABLE_OPTIONS:
			if hasattr(self, option):
				if option in snapshot.Options:
					old_value = getattr(self, option)
					new_value = snapshot.Options[option]
					log.info(f'Changing the option "{option}" from {old_value} to {new_value} for the current snapshot.')
					setattr(self, option, new_value)
				else:
					setattr(self, option, self.default_options[option])

	def get_recording_subdirectory_path(self, id_: int) -> str:
		""" Retrieves the absolute path of a snapshot recording given its ID. """
		bucket = ceil(id_ / self.max_recordings_per_directory) * self.max_recordings_per_directory
		return os.path.join(self.recordings_path, str(bucket))

for option in ['encoding', 'hide_title' 'notes']:
	assert option not in CommonConfig.MUTABLE_OPTIONS, f'The mutable option name "{option}" is reserved.'

config = CommonConfig()

if config.debug:
	sqlite3.enable_callback_tracebacks(True)

locale.setlocale(locale.LC_ALL, config.locale)

# Allow Selenium to set any Firefox preference. Otherwise, certain preferences couldn't be changed.
# See: https://github.com/SeleniumHQ/selenium/blob/selenium-3.141.0/py/selenium/webdriver/firefox/firefox_profile.py
FirefoxProfile.DEFAULT_PREFERENCES = {}
FirefoxProfile.DEFAULT_PREFERENCES['frozen'] = {}
FirefoxProfile.DEFAULT_PREFERENCES['mutable'] = config.preferences

if config.ffmpeg_path is not None:
	path = os.environ.get('PATH', '')
	os.environ['PATH'] = f'{config.ffmpeg_path};{path}'

log = logging.getLogger('eternal wanderer')
log.setLevel(logging.DEBUG if config.debug else logging.INFO)
log.debug('Running in debug mode.')

retry = Retry(total=5, status_forcelist=[502, 503, 504], backoff_factor=1)
adapter = HTTPAdapter(max_retries=retry)

global_session = requests.Session()
global_session.mount('http://web.archive.org', adapter)
global_session.mount('https://web.archive.org', adapter)

del option, path, retry, adapter

def setup_logger(filename: str) -> logging.Logger:
	""" Adds a stream and file handler to the Eternal Wanderer logger. """

	stream_handler = logging.StreamHandler()
	stream_formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
	stream_handler.setFormatter(stream_formatter)

	file_handler = logging.FileHandler(f'{filename}.log', 'a', 'utf-8')
	file_formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(filename)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
	file_handler.setFormatter(file_formatter)

	global log
	log.addHandler(stream_handler)
	log.addHandler(file_handler)
	
	return log

class RateLimiter():
	""" A rate limiter wrapper that restricts the number of requests made to the Wayback Machine and its APIs. """

	wayback_machine_memory_storage: MemoryStorage
	wayback_machine_rate_limiter: MovingWindowRateLimiter
	wayback_machine_requests_per_minute: RateLimitItemPerSecond

	cdx_api_memory_storage: MemoryStorage
	cdx_api_rate_limiter: MovingWindowRateLimiter
	cdx_api_requests_per_second: RateLimitItemPerSecond

	save_api_memory_storage: MemoryStorage
	save_api_rate_limiter: MovingWindowRateLimiter
	save_api_requests_per_second: RateLimitItemPerSecond

	def __init__(self):

		self.wayback_machine_memory_storage = MemoryStorage()
		self.wayback_machine_rate_limiter = MovingWindowRateLimiter(self.wayback_machine_memory_storage)
		self.wayback_machine_requests_per_minute = RateLimitItemPerSecond(config.wayback_machine_rate_limit_amount, config.wayback_machine_rate_limit_window)
		
		self.cdx_api_memory_storage = MemoryStorage()
		self.cdx_api_rate_limiter = MovingWindowRateLimiter(self.cdx_api_memory_storage)
		self.cdx_api_requests_per_second = RateLimitItemPerSecond(config.cdx_api_rate_limit_amount, config.cdx_api_rate_limit_window)

		self.save_api_memory_storage = MemoryStorage()
		self.save_api_rate_limiter = MovingWindowRateLimiter(self.save_api_memory_storage)
		self.save_api_requests_per_second = RateLimitItemPerSecond(config.save_api_rate_limit_amount, config.save_api_rate_limit_window)

	def wait_for_wayback_machine_rate_limit(self, **kwargs) -> None:
		""" Waits for a given amount of time if the user-defined Wayback Machine rate limit has been reached. Otherwise, returns immediately. Thread-safe. """
		while not self.wayback_machine_rate_limiter.hit(self.wayback_machine_requests_per_minute, **kwargs):
			sleep(config.rate_limit_poll_frequency)

	def wait_for_cdx_api_rate_limit(self, **kwargs) -> None:
		""" Waits for a given amount of time if the user-defined CDX API rate limit has been reached. Otherwise, returns immediately. Thread-safe. """
		while not self.cdx_api_rate_limiter.hit(self.cdx_api_requests_per_second, **kwargs):
			sleep(config.rate_limit_poll_frequency)

	def wait_for_save_api_rate_limit(self, **kwargs) -> None:
		""" Waits for a given amount of time if the user-defined Save API rate limit has been reached. Otherwise, returns immediately. Thread-safe. """
		while not self.save_api_rate_limiter.hit(self.save_api_requests_per_second, **kwargs):
			sleep(config.rate_limit_poll_frequency)

# Note that different scripts use different global rate limiter instances.
# They're only the same between a script and this module.
global_rate_limiter = RateLimiter()

def url_key_matches_domain_pattern(url_key: str, domain_patterns: list[list[str]], cache: dict[str, bool]) -> bool:
	""" Checks whether a URL's key matches a list of domain patterns. """

	result = False

	if domain_patterns:

		# E.g. "com,geocities)/hollywood/hills/5988"
		domain, *_ = url_key.lower().partition(')')
		
		# E.g. "com,sun,java:8081)/products/javamail/index.html"
		domain, *_ = domain.partition(':')
			
		if domain in cache:
			return cache[domain]

		component_list = domain.split(',')

		for pattern_component_list in domain_patterns:
			
			# If the domain has fewer components then it can't match the allowed pattern.
			if len(component_list) < len(pattern_component_list):
				continue

			# If there are more components in the domain than in the allowed pattern, these will be ignored.
			# Since we're looking at these domains backwards, this means we'll match any subdomains.
			for component, pattern_component in zip(component_list, pattern_component_list):
				if pattern_component != '*' and component != pattern_component:
					break
			else:
				result = True
				break

		cache[domain] = result

	return result

checked_allowed_domains: dict[str, bool] = {}
checked_disallowed_domains: dict[str, bool] = {}

def is_url_key_allowed(url_key: str) -> bool:
	""" Checks whether a URL should be scouted or recorded given its URL key. """
	return (not config.allowed_domains or url_key_matches_domain_pattern(url_key, config.allowed_domains, checked_allowed_domains)) and (not config.disallowed_domains or not url_key_matches_domain_pattern(url_key, config.disallowed_domains, checked_disallowed_domains))

class Database():
	""" The database that contains all scraped snapshot metadata and their recordings. """

	connection: sqlite3.Connection

	def __init__(self):

		log.info(f'Connecting to the database in "{config.database_path}".')

		os.makedirs(os.path.dirname(config.database_path), exist_ok=True)

		self.connection = sqlite3.connect(config.database_path)
		self.connection.row_factory = sqlite3.Row

		self.connection.execute('PRAGMA journal_mode = WAL;')
		self.connection.execute('PRAGMA synchronous = NORMAL;')
		self.connection.execute('PRAGMA temp_store = MEMORY;')

		def rank_snapshot_by_points(points: Optional[int], offset: Optional[int]) -> float:
			""" Ranks a snapshot by its points so that the highest ranked one will be scouted or recorded next. """
			
			if offset is None:
				return random()

			# This uses a modified weighted random sampling algorithm:
			# - https://stackoverflow.com/a/56006340/18442724
			# - https://stackoverflow.com/a/51090191/18442724
			# - http://utopia.duth.gr/~pefraimi/research/data/2007EncOfAlg.pdf
			
			# For snapshots without a parent during scouting.
			if points is None:
				return 0

			sign = 1 if points >= 0 else -1
			return sign * random() ** (1 / (abs(points) + 1 + offset))
	
		self.connection.create_function('IS_URL_KEY_ALLOWED', 1, is_url_key_allowed)
		self.connection.create_function('RANK_SNAPSHOT_BY_POINTS', 2, rank_snapshot_by_points)

		# A few notes for future reference:
		#
		# The following two pages have different URLs and timestamps but their digest (i.e. content) is the same:
		# - https://web.archive.org/web/20010203164200if_/http://www.tripod.lycos.com:80/service/welcome/preferences
		# - https://web.archive.org/web/20010203180900if_/http://www.tripod.lycos.com:80/bin/membership/login
		#
		# Some examples of the Url, Timestamp, UrlKey, and Digest database columns as seen in the CDX API.
		# Notice how the UrlKey and Digest are the same, even though the URLs and timestamps are different.
		# - http://www.geocities.com/Heartland/Plains/1036/africa.gif	20090730213441	com,geocities)/heartland/plains/1036/africa.gif	RRCC3TTUVIQTMFN6BDRRIXR7OBNCGS6X
		# - http://geocities.com/Heartland/Plains/1036/africa.gif		20090820053240	com,geocities)/heartland/plains/1036/africa.gif	RRCC3TTUVIQTMFN6BDRRIXR7OBNCGS6X
		# - http://geocities.com/Heartland/Plains/1036/africa.gif		20091026145159	com,geocities)/heartland/plains/1036/africa.gif	RRCC3TTUVIQTMFN6BDRRIXR7OBNCGS6X	

		self.connection.execute(f'''
								CREATE TABLE IF NOT EXISTS Snapshot
								(
									Id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
									ParentId INTEGER,
									Depth INTEGER NOT NULL,
									State INTEGER NOT NULL,
									Priority INTEGER NOT NULL DEFAULT {Snapshot.NO_PRIORITY},
									IsExcluded BOOLEAN NOT NULL,
									IsMedia BOOLEAN,
									PageLanguage TEXT,
									PageTitle TEXT,
									PageUsesPlugins BOOLEAN,
									MediaExtension TEXT,
									MediaTitle TEXT,
									MediaAuthor TEXT,
									Url TEXT NOT NULL,
									Timestamp VARCHAR(14) NOT NULL,
									LastModifiedTime VARCHAR(14),
									UrlKey TEXT,
									Digest VARCHAR(64),
									IsSensitiveOverride BOOLEAN,
									Options JSON,

									UNIQUE (Url, Timestamp)
									UNIQUE (Url, Digest)

									FOREIGN KEY (ParentId) REFERENCES Snapshot (Id)
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS Topology
								(
									ParentId INTEGER NOT NULL,
									ChildId INTEGER NOT NULL,

									PRIMARY KEY (ParentId, ChildId),
									FOREIGN KEY (ParentId) REFERENCES Snapshot (Id),
									FOREIGN KEY (ChildId) REFERENCES Snapshot (Id)
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS Word
								(
									Id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
									Word TEXT NOT NULL,
									IsTag BOOLEAN NOT NULL,
									Points INTEGER NOT NULL DEFAULT 0,
									IsSensitive BOOLEAN NOT NULL DEFAULT FALSE,

									UNIQUE (Word, IsTag)
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS SnapshotWord
								(
									SnapshotId INTEGER NOT NULL,
									WordId INTEGER NOT NULL,
									Count INTEGER NOT NULL,

									PRIMARY KEY (SnapshotId, WordId),
									FOREIGN KEY (SnapshotId) REFERENCES Snapshot (Id),
									FOREIGN KEY (WordId) REFERENCES Word (Id)
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS Config
								(
									Name TEXT NOT NULL PRIMARY KEY,
									Value TEXT NOT NULL
								);
								''')

		# Regular words should only count once, even if they appear multiple times on a page.
		self.connection.execute(f'''
								CREATE VIEW IF NOT EXISTS SnapshotInfo AS
								SELECT
									S.Id AS Id,
									(
										CASE WHEN S.State = {Snapshot.QUEUED} THEN NULL
											 ELSE IFNULL(CASE WHEN S.IsMedia THEN (SELECT CAST(Value AS INTEGER) FROM Config WHERE Name = 'media_points')
															  WHEN W.IsTag THEN SUM(SW.Count * W.Points)
															  ELSE SUM(MIN(SW.Count, 1) * W.Points)
														 END, 0)
										END
									) AS Points,
									(
										CASE WHEN S.State = {Snapshot.QUEUED} THEN NULL
										ELSE IFNULL(S.IsSensitiveOverride, IFNULL(MAX(W.IsSensitive), FALSE))
										END
									) AS IsSensitive
								FROM Snapshot S
								LEFT JOIN SnapshotWord SW ON S.Id = SW.SnapshotId
								LEFT JOIN Word W ON SW.WordId = W.Id
								GROUP BY S.Id;
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS SavedUrl
								(
									Id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
									SnapshotId INTEGER NOT NULL,
									RecordingId INTEGER NOT NULL,
									Url TEXT NOT NULL UNIQUE,
									Timestamp VARCHAR(14),
									Failed BOOLEAN NOT NULL,

									FOREIGN KEY (SnapshotId) REFERENCES Snapshot (Id),
									FOREIGN KEY (RecordingId) REFERENCES Recording (Id)
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS Recording
								(
									Id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
									SnapshotId INTEGER NOT NULL,
									IsProcessed BOOLEAN NOT NULL,
									UploadFilename TEXT NOT NULL UNIQUE,
									ArchiveFilename TEXT UNIQUE,
									TextToSpeechFilename TEXT UNIQUE,
									CreationTime TIMESTAMP NOT NULL,
									PublishTime TIMESTAMP,
									TwitterMediaId INTEGER,
									TwitterStatusId INTEGER,
									MastodonMediaId INTEGER,
									MastodonStatusId INTEGER,

									FOREIGN KEY (SnapshotId) REFERENCES Snapshot (Id)
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS Compilation
								(
									Id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
									UploadFilename TEXT NOT NULL UNIQUE,
									TimestampsFilename TEXT NOT NULL UNIQUE,
									CreationTime TIMESTAMP NOT NULL
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS RecordingCompilation
								(
									RecordingId INTEGER NOT NULL,
									CompilationId INTEGER NOT NULL,
									SnapshotId INTEGER NOT NULL,
									Position INTEGER NOT NULL,

									PRIMARY KEY (RecordingId, CompilationId),
									FOREIGN KEY (RecordingId) REFERENCES Recording (Id),
									FOREIGN KEY (CompilationId) REFERENCES Compilation (Id),
									FOREIGN KEY (SnapshotId) REFERENCES Snapshot (Id)
								);
								''')

		self.connection.commit()

	def disconnect(self) -> None:
		""" Disconnects from the database. """

		try:
			self.connection.close()
		except sqlite3.Error as error:
			log.error(f'Failed to close the database with the error: {repr(error)}')

	def __enter__(self):
		return self.connection

	def __exit__(self, exception_type, exception_value, traceback):
		self.disconnect()

class Snapshot():
	""" A snapshot from the Wayback Machine at a specific time and location. """

	# From the database.
	Id: int
	ParentId: Optional[int]
	Depth: int
	State: int
	Priority: int
	IsExcluded: bool
	IsMedia: Optional[bool]
	PageLanguage: Optional[str]
	PageTitle: Optional[str]
	PageUsesPlugins: Optional[bool]
	MediaExtension: Optional[str]
	MediaTitle: Optional[str]
	MediaAuthor: Optional[str]
	Url: str
	Timestamp: str
	LastModifiedTime: Optional[str]
	UrlKey: Optional[str]
	Digest: Optional[str]
	IsSensitiveOverride: Optional[bool]
	Options: dict # Different from the database data type.

	# Determined dynamically if joined with the SnapshotInfo view.
	Points: Optional[int]
	IsSensitive: Optional[bool]

	# Determined from the Options column.
	Encoding: str
	HideTitle: bool
	Notes: str

	# Determined at runtime.
	WaybackUrl: str
	OldestTimestamp: str
	OldestDatetime: datetime
	ShortDate: str
	DisplayTitle: str
	DisplayMetadata: Optional[str]
	LanguageName: Optional[str]
	
	# Constants. Each of these must be greater than the last.
	QUEUED = 0
	INVALID = 1
	SCOUTED = 2
	ABORTED = 3
	RECORDED = 4
	REJECTED = 5
	APPROVED = 6
	PUBLISHED = 7
	ARCHIVED = 8

	STATE_NAMES: dict[int, str]

	NO_PRIORITY = 0
	SCOUT_PRIORITY = 1
	RECORD_PRIORITY = 2
	PUBLISH_PRIORITY = 3

	PRIORITY_NAMES: dict[int, str]

	TIMESTAMP_FORMAT = '%Y%m%d%H%M%S'

	IFRAME_MODIFIER = 'if_'
	OBJECT_EMBED_MODIFIER = 'oe_'
	IDENTICAL_MODIFIER = 'id_'

	def __init__(self, **kwargs):
		
		self.Points = None
		self.IsSensitive = None
		self.__dict__.update(kwargs)
		
		def bool_or_none(value: Any) -> Union[bool, None]:
			return bool(value) if value is not None else None

		self.IsExcluded = bool_or_none(self.IsExcluded)
		self.IsMedia = bool_or_none(self.IsMedia)
		self.PageUsesPlugins = bool_or_none(self.PageUsesPlugins)	
		self.IsSensitiveOverride = bool_or_none(self.IsSensitiveOverride)
		self.IsSensitive = bool_or_none(self.IsSensitive)

		if self.Options is not None:
			try:
				self.Options = json.loads(self.Options)
			except json.JSONDecodeError as error:
				log.error(f'Failed to load the options for the snapshot {self} with the error: {repr(error)}')
				self.Options = {}
		else:
			self.Options = {}

		self.Encoding = self.Options.get('encoding', '')
		self.HideTitle = self.Options.get('hide_title', False)
		self.Notes = self.Options.get('notes', '')

		modifier = Snapshot.OBJECT_EMBED_MODIFIER if self.IsMedia else Snapshot.IFRAME_MODIFIER
		self.WaybackUrl = compose_wayback_machine_snapshot_url(timestamp=self.Timestamp, modifier=modifier, url=self.Url)

		# If the last modified time is older than the first website (August 1991)
		# or if it's newer than the archival date, use the snapshot's timestamp.
		# See: https://en.wikipedia.org/wiki/List_of_websites_founded_before_1995
		#
		# E.g. https://web.archive.org/web/19961111002723if_/http://www.metamor.com:80/pages/missioncontrol/mission_control.html
		# Where the last modified time is 19800501233128 (too old).
		# E.g. https://web.archive.org/web/19961222034448if_/http://panter.soci.aau.dk:80/
		# Where the last modified time is 20090215174615 (too new).
		if self.LastModifiedTime is not None and self.LastModifiedTime > '1991':
			self.OldestTimestamp = min(self.Timestamp, self.LastModifiedTime)
		else:
			self.OldestTimestamp = self.Timestamp

		self.OldestDatetime = datetime.strptime(self.OldestTimestamp, Snapshot.TIMESTAMP_FORMAT)
		# How the date is formatted depends on the current locale.
		self.ShortDate = self.OldestDatetime.strftime('%b %Y')

		self.DisplayTitle = self.PageTitle
		if self.HideTitle or not self.DisplayTitle:
			
			parts = urlparse(unquote(self.Url))
			self.DisplayTitle = os.path.basename(parts.path)
			
			if not self.DisplayTitle:
				new_parts = parts._replace(netloc=parts.hostname, params='', query='', fragment='')
				self.DisplayTitle = urlunparse(new_parts)

		if self.MediaTitle and self.MediaAuthor:
			self.DisplayMetadata = f'"{self.MediaTitle}" by "{self.MediaAuthor}"'
		elif self.MediaTitle:
			self.DisplayMetadata = f'"{self.MediaTitle}"'
		elif self.MediaAuthor:
			self.DisplayMetadata =  f'By "{self.MediaAuthor}"'
		else:
			self.DisplayMetadata = None

		self.LanguageName = config.language_names.get(self.PageLanguage, self.PageLanguage) if self.PageLanguage is not None else None

	def __str__(self):
		return f'({self.Url}, {self.Timestamp})'

Snapshot.STATE_NAMES = {
	Snapshot.QUEUED: 'Queued',
	Snapshot.INVALID: 'Invalid',
	Snapshot.SCOUTED: 'Scouted',
	Snapshot.ABORTED: 'Aborted',
	Snapshot.RECORDED: 'Recorded',
	Snapshot.REJECTED: 'Rejected',
	Snapshot.APPROVED: 'Approved',
	Snapshot.PUBLISHED: 'Published',
	Snapshot.ARCHIVED: 'Archived',
}

Snapshot.PRIORITY_NAMES = {
	Snapshot.NO_PRIORITY: 'None',
	Snapshot.SCOUT_PRIORITY: 'Scout',
	Snapshot.RECORD_PRIORITY: 'Record',
	Snapshot.PUBLISH_PRIORITY: 'Publish',
}

class Recording():
	""" A video recording of a Wayback Machine snapshot. """

	# From the database.
	Id: int
	SnapshotId: int
	IsProcessed: bool
	UploadFilename: str
	ArchiveFilename: Optional[str]
	TextToSpeechFilename: Optional[str]
	CreationTime: str
	PublishTime: Optional[str]
	TwitterMediaId: Optional[int]
	TwitterStatusId: Optional[int]
	MastodonMediaId: Optional[int]
	MastodonStatusId: Optional[int]

	# Determined at runtime.
	UploadFilePath: str
	ArchiveFilePath: Optional[str]
	TextToSpeechFilePath: Optional[str]
	CompilationSegmentFilePath: Optional[str]

	def __init__(self, **kwargs):
		
		self.__dict__.update(kwargs)
		
		subdirectory_path = config.get_recording_subdirectory_path(self.Id)
		self.UploadFilePath = os.path.join(subdirectory_path, self.UploadFilename)
		self.ArchiveFilePath = os.path.join(subdirectory_path, self.ArchiveFilename) if self.ArchiveFilename is not None else None
		self.TextToSpeechFilePath = os.path.join(subdirectory_path, self.TextToSpeechFilename) if self.TextToSpeechFilename is not None else None
		self.CompilationSegmentFilePath = None # Set in the compilation script.

class Browser():
	""" A Firefox browser instance created by Selenium. """

	use_plugins: bool
	use_autoit: bool

	firefox_path: str
	firefox_directory_path: str
	webdriver_path: str
	
	registry: 'TemporaryRegistry'
	java_deployment_path: str
	java_bin_path: Optional[str]
	autoit_processes: list[Popen]

	driver: WebDriver
	version: str
	profile_path: str
	pid: int
	
	application: Optional[WindowsApplication]
	window: Optional[WindowSpecification]
	
	BLANK_URL = 'about:blank'
	CONFIG_URL = 'about:config'

	def __init__(self, 	headless: bool = False,
						multiprocess: bool = True,
						extra_preferences: Optional[dict[str, Union[bool, int, str]]] = None,
						use_extensions: bool = False,
						extension_filter: Optional[list[str]] = None,
						user_script_filter: Optional[list[str]] = None,
						use_plugins: bool = False,
						use_autoit: bool = False):

		extension_filter = container_to_lowercase(extension_filter) if extension_filter is not None else extension_filter # type: ignore
		user_script_filter = container_to_lowercase(user_script_filter) if user_script_filter is not None else user_script_filter # type: ignore
		self.use_plugins = use_plugins
		self.use_autoit = use_autoit

		self.firefox_path = config.headless_firefox_path if headless else config.gui_firefox_path
		self.firefox_directory_path = os.path.dirname(self.firefox_path)
		self.webdriver_path = config.headless_webdriver_path if headless else config.gui_webdriver_path
		
		self.registry = TemporaryRegistry()
		self.java_deployment_path = os.path.join(os.environ['USERPROFILE'], 'AppData', 'LocalLow', 'Sun', 'Java', 'Deployment')
		self.java_bin_path = None
		self.autoit_processes = []

		log.info('Configuring Firefox.')

		if config.profile_path is not None:
			log.info(f'Using the custom Firefox profile at "{config.profile_path}".')
		else:
			log.info(f'Using a temporary Firefox profile.')

		profile = FirefoxProfile(config.profile_path)
		
		if not config.use_master_plugin_registry:
			plugin_reg_path = os.path.join(profile.profile_dir, 'pluginreg.dat')
			delete_file(plugin_reg_path)

		try:
			scripts_path = os.path.join(profile.profile_dir, 'gm_scripts')
			scripts_config_path = os.path.join(scripts_path, 'config.xml')

			tree = ElementTree.parse(scripts_config_path)
			for script in tree.getroot():
				
				name = script.get('name')
				directory = script.get('basedir')

				if name is not None and directory is not None:

					name = name.lower()
					enabled = config.user_scripts.get(name, False)
					filtered = user_script_filter is not None and name not in user_script_filter

					if enabled and not filtered:
						log.info(f'Enabling the user script "{name}".')
						script.set('enabled', 'true')
					else:
						log.info(f'Disabling the user script "{name}" at the user\'s request.')
						script.set('enabled', 'false')

			tree.write(scripts_config_path)

		except ElementTree.ParseError as error:
			log.error(f'Failed to update the user scripts configuration file with the error: {repr(error)}')

		for key, value in config.preferences.items():
			profile.set_preference(key, value)

		if extra_preferences is not None:
			log.info(f'Setting additional preferences: {extra_preferences}')
			for key, value in extra_preferences.items():
				profile.set_preference(key, value)

		if self.use_plugins:

			log.info(f'Using the plugins in "{config.plugins_path}".')

			plugin_paths = [''] * len(config.plugins)
			plugin_precedence = {key: i for i, key in enumerate(config.plugins)}
			
			plugin_search_path = os.path.join(config.plugins_path, '**', 'np*.dll')
			for path in iglob(plugin_search_path, recursive=True):
				
				filename = os.path.basename(path).lower()
				if filename in config.plugins:

					if config.plugins[filename]:
						log.info(f'Using the plugin "{filename}".')
						index = plugin_precedence[filename]
						plugin_paths[index] = os.path.dirname(path)
					else:
						log.info(f'Skipping the plugin "{filename}" at the user\'s request.')

				else:
					log.info(f'The plugin file "{path}" was found but is not specified in the configuration.')

			os.environ['MOZ_PLUGIN_PATH'] = ';'.join(plugin_paths)

			plugin_extender_source_path = os.path.join(config.plugins_path, 'BrowserPluginExtender', 'BrowserPluginExtender.dll')
			shutil.copy(plugin_extender_source_path, self.firefox_directory_path)

			self.configure_shockwave_player()
			self.configure_java_plugin()
			self.configure_cosmo_player()
			self.configure_3dvia_player()
		else:
			os.environ['MOZ_PLUGIN_PATH'] = ''

		if use_extensions:

			log.info(f'Installing the extensions in "{config.extensions_path}".')

			for filename, enabled in config.extensions_before_running.items():
				
				filtered = extension_filter is not None and filename not in extension_filter

				if enabled and not filtered:
					log.info(f'Installing the extension "{filename}".')
					extension_path = os.path.join(config.extensions_path, filename)
					profile.add_extension(extension_path)
				else:
					log.info(f'Skipping the extension "{filename}" at the user\'s request.')

		options = webdriver.FirefoxOptions()
		options.binary = FirefoxBinary(self.firefox_path)
		options.profile = profile
		options.headless = headless

		if multiprocess:
			os.environ.pop('MOZ_FORCE_DISABLE_E10S', None)
		else:
			log.warning('Disabling multiprocess Firefox.')
			os.environ['MOZ_FORCE_DISABLE_E10S'] = '1'
		
		# Disable DPI scaling to fix potential display issues in Firefox.
		# See:
		# - https://stackoverflow.com/a/37881453/18442724
		# - https://ss64.com/nt/syntax-compatibility.html
		os.environ['__COMPAT_LAYER'] = 'GDIDPISCALING DPIUNAWARE'

		log.info(f'Creating the WebDriver using the Firefox executable at "{self.firefox_path}" and the driver at "{self.webdriver_path}".')
		
		while True:
			try:
				self.driver = webdriver.Firefox(executable_path=self.webdriver_path, options=options, service_log_path=None)
				break
			except WebDriverException as error:
				log.error(f'Failed to create the WebDriver with the error: {repr(error)}')
				kill_processes_by_path(self.firefox_path)
				sleep(30)

		self.driver.set_page_load_timeout(config.page_load_timeout)
		self.driver.maximize_window()

		# See: https://web.archive.org/web/20220602183757if_/https://www.selenium.dev/documentation/webdriver/capabilities/shared/
		assert self.driver.capabilities['pageLoadStrategy'] == 'normal', 'The page load strategy must be "normal".'

		self.version =  self.driver.capabilities['browserVersion']
		self.profile_path = self.driver.capabilities['moz:profile']
		self.pid = self.driver.capabilities['moz:processID']

		log.info(f'Running Firefox version {self.version}.')

		self.application = None
		self.window = None

		if not headless:
			try:
				log.info(f'Connecting to the Firefox executable with the PID {self.pid}.')
				self.application = WindowsApplication(backend='win32')
				self.application.connect(process=self.pid, timeout=30)
				self.window = self.application.top_window()
			except (WindowProcessNotFoundError, WindowTimeoutError) as error:
				log.error(f'Failed to connect to the Firefox executable with the error: {repr(error)}')
			
		if use_extensions:

			for filename, enabled in config.extensions_after_running.items():
				
				filtered = extension_filter is not None and filename not in extension_filter

				if enabled and not filtered:
					log.info(f'Installing the extension "{filename}".')
					extension_path = os.path.join(config.extensions_path, filename)
					self.driver.install_addon(extension_path)
				else:
					log.info(f'Skipping the extension "{filename}" at the user\'s request.')

		self.driver.get(Browser.BLANK_URL)

		if self.use_autoit:
			
			log.info(f'Running the compiled AutoIt scripts in "{config.autoit_path}" with a poll frequency of {config.autoit_poll_frequency} milliseconds.')

			for filename, enabled in config.autoit_scripts.items():

				if enabled:
					try:
						# If we enable the AutoIt scripts twice, this will kill any existing ones.
						# This is fine in practice since we only do this for the recorder script and
						# since we only want one of each running at the same time anyways.

						log.info(f'Running the AutoIt script "{filename}".')
						script_path = os.path.join(config.autoit_path, filename)
						
						kill_processes_by_path(script_path)
						process = Popen([script_path, str(config.autoit_poll_frequency)])
						self.autoit_processes.append(process)
					
					except OSError as error:
						log.error(f'Failed to run the AutoIt script "{filename}" with the error: {repr(error)}')
				else:
					log.info(f'Skipping the AutoIt script "{filename}" at the user\'s request.')
		
	def configure_shockwave_player(self) -> None:
		""" Configures the Shockwave Player by setting the appropriate registry keys. """

		log.info('Configuring the Shockwave Player.')

		# The values we're changing here are the default ones that are usually displayed as "(Default)",
		# even though their actually empty strings.
		
		# Enable the legacy Shockwave 10 player that's included to play older movies.
		self.registry.set('HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 11\\allowfallback\\', 'y')
		self.registry.set('HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 12\\allowfallback\\', 'y')

		# Shockwave Renderers:
		#
		# - 0 = Obey Content Settings
		# - 1 = Always Use Software Renderer
		# - 2 = Always Use Hardware - OpenGL
		# - 3 = Always Use Hardware - DirectX 5
		# - 4 = Always Use Hardware - DirectX 7
		# - 5 = Always Use Hardware - DirectX 9
		#
		# These are REG_SZ and not REG_DWORD values.

		renderer = {'auto': '0', 'software': '1', 'opengl': '2', 'directx 5': '3', 'directx 7': '4', 'directx 9': '5', 'directx': '5'}.get(config.shockwave_renderer)
		assert renderer is not None, f'Unknown Shockwave renderer "{config.shockwave_renderer}".'

		self.registry.set('HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Macromedia\\Shockwave 10\\renderer3dsetting\\', renderer)
		self.registry.set('HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Macromedia\\Shockwave 10\\renderer3dsettingPerm\\', renderer)
		
		self.registry.set('HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 11\\renderer3dsetting\\', renderer)
		self.registry.set('HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 11\\renderer3dsettingPerm\\', renderer)
		
		self.registry.set('HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 12\\renderer3dsetting\\', renderer)
		self.registry.set('HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 12\\renderer3dsettingPerm\\', renderer)

	def configure_java_plugin(self) -> None:
		""" Configures the Java Plugin by generating the appropriate deployment files and passing any useful parameters to the JRE. """

		java_plugin_search_path = os.path.join(config.plugins_path, '**', 'jre*', 'bin', 'plugin2')
		java_plugin_path = next(iglob(java_plugin_search_path, recursive=True), None)
		if java_plugin_path is None:
			log.error('Could not find the path to the Java Runtime Environment. The Java Plugin was not set up correctly.')
			return

		java_jre_path = os.path.dirname(os.path.dirname(java_plugin_path))
		log.info(f'Configuring the Java Plugin using the runtime environment located in "{java_jre_path}".')

		java_lib_path = os.path.join(java_jre_path, 'lib')
		self.java_bin_path = os.path.join(java_jre_path, 'bin')

		if config.java_add_to_path:
			path = os.environ.get('PATH', '')
			os.environ['PATH'] = f'{self.java_bin_path};{path}'

		java_config_path = os.path.join(java_lib_path, 'deployment.config')
		java_properties_path = os.path.join(java_lib_path, 'deployment.properties')

		java_config_template_path = os.path.join(config.plugins_path, 'Java', 'deployment.config.template')
		java_properties_template_path = os.path.join(config.plugins_path, 'Java', 'deployment.properties.template')

		with open(java_config_template_path, encoding='utf-8') as file:
			content = file.read()

		content = content.replace('{comment}', f'Generated by "{__file__}" on {get_current_timestamp()}.')
		content = content.replace('{system_config_path}', java_properties_path.replace('\\', '/').replace(' ', '\\u0020'))
		
		with open(java_config_path, 'w', encoding='utf-8') as file:
			file.write(content)

		with open(java_properties_template_path, encoding='utf-8') as file:
			content = file.read()

		# E.g. "1.8.0" or "1.8.0_11"
		java_product = re.findall(r'(?:jdk|jre)((?:\d+\.\d+\.\d+)(?:_\d+)?)', java_jre_path, re.IGNORECASE)[-1]
		java_platform, *_ = java_product.rpartition('.') # E.g. "1.8"
		*_, java_version = java_platform.partition('.') # E.g. "8"

		java_web_start_path = os.path.join(self.java_bin_path, 'javaws.exe')

		java_exception_sites_path = os.path.join(java_lib_path, 'exception.sites')
		java_exception_sites_template_path = os.path.join(config.plugins_path, 'Java', 'exception.sites.template')
		shutil.copy(java_exception_sites_template_path, java_exception_sites_path)

		def escape_java_deployment_properties_path(path: str) -> str:
			return path.replace('\\', '\\\\').replace(':', '\\:').replace(' ', '\\u0020')

		content = content.replace('{comment}', f'Generated by "{__file__}" on {get_current_timestamp()}.')
		content = content.replace('{jre_platform}', java_platform)
		content = content.replace('{jre_product}', java_product)
		content = content.replace('{jre_path}', escape_java_deployment_properties_path(java_web_start_path))
		content = content.replace('{jre_version}', java_version)
		content = content.replace('{security_level}', 'LOW' if java_product <= '1.7.0_17' else 'MEDIUM')
		content = content.replace('{exception_sites_path}', escape_java_deployment_properties_path(java_exception_sites_path))
		content = content.replace('{console_startup}', 'SHOW' if config.java_show_console else 'NEVER')

		with open(java_properties_path, 'w', encoding='utf-8') as file:
			file.write(content)

		java_policy_path = os.path.join(java_lib_path, 'security', 'java.policy')
		java_policy_template_path = os.path.join(config.plugins_path, 'Java', 'java.policy.template')
		shutil.copy(java_policy_template_path, java_policy_path)

		# Override any security properties from other locally installed Java versions in order to allow applets
		# to run even if they use a disabled cryptographic algorithm.
		java_security_path = os.path.join(java_lib_path, 'security', 'java.security')

		with open(java_security_path, encoding='utf-8') as file:
			content = file.read()

		content = re.sub(r'^jdk\.certpath\.disabledAlgorithms=.*', 'jdk.certpath.disabledAlgorithms=', content, re.IGNORECASE | re.MULTILINE)

		with open(java_security_path, 'w', encoding='utf-8') as file:
			file.write(content)

		# Disable Java bytecode verification in order to run older applets correctly.
		#
		# Originally, we wanted to pass the character encoding and locale Java arguments on a page-by-page basis.
		# This would allow Japanese applets to display their content correctly regardless of its encoding. This
		# feature was removed since the encoding and locale didn't seem to change even when the "java_arguments"
		# and "java-vm-args" parameters were set.
		#
		# We'll just set them globally since that seems to work out, though it means that we only support Latin and
		# Japanese text (see the java_arguments option in the configuration file). Note that changing this to a
		# different language may require you to add the localized security prompt's title to the "close_java_popups"
		# AutoIt script.
		escaped_java_security_path = java_security_path.replace('\\', '/')
		required_java_arguments = [f'-Djava.security.properties=="file:///{escaped_java_security_path}"', '-Xverify:none']
		os.environ['JAVA_TOOL_OPTIONS'] = ' '.join(required_java_arguments + config.java_arguments)
		os.environ['_JAVA_OPTIONS'] = ''
		os.environ['deployment.expiration.check.enabled'] = 'false'

		self.delete_user_level_java_properties()
		self.delete_java_plugin_cache()

	def configure_cosmo_player(self) -> None:
		""" Configures the Cosmo Player by setting the appropriate registry keys. """
		
		cosmo_player_search_path = os.path.join(config.plugins_path, '**', 'npcosmop211.dll')
		cosmo_player_path = next(iglob(cosmo_player_search_path, recursive=True), None)
		if cosmo_player_path is None:
			log.error('Could not find the path to the Cosmo Player plugin files. The Cosmo Player was not be set up correctly.')
			return

		cosmo_player_path = os.path.dirname(cosmo_player_path)
		log.info(f'Configuring the Cosmo Player using the plugin files located in "{cosmo_player_path}".')

		cosmo_player_system32_path = os.path.join(cosmo_player_path, 'System32')
		path = os.environ.get('PATH', '')
		os.environ['PATH'] = f'{cosmo_player_system32_path};{path}'

		# Keep in mind that the temporary registry always operates on the 32-bit view of the registry.
		# In other words, the following values will be redirected to the following registry keys:
		#
		# HKEY_LOCAL_MACHINE\SOFTWARE\Classes\WOW6432Node\CLSID\{06646731-BCF3-11D0-9518-00C04FC2DD79}
		# HKEY_LOCAL_MACHINE\SOFTWARE\Classes\WOW6432Node\CLSID\{06646732-BCF3-11D0-9518-00C04FC2DD79}
		# HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Classes\CLSID\{06646731-BCF3-11D0-9518-00C04FC2DD79}
		# HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Classes\CLSID\{06646732-BCF3-11D0-9518-00C04FC2DD79}
		# HKEY_CLASSES_ROOT\WOW6432Node\CLSID\{06646731-BCF3-11D0-9518-00C04FC2DD79}
		# HKEY_CLASSES_ROOT\WOW6432Node\CLSID\{06646732-BCF3-11D0-9518-00C04FC2DD79}
		#
		# HKEY_CLASSES_ROOT\Filter\{06646731-BCF3-11D0-9518-00C04FC2DD79}
		# HKEY_CLASSES_ROOT\Filter\{06646732-BCF3-11D0-9518-00C04FC2DD79}
		#
		# HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\COSMOSOFTWARE

		required_registry_keys: dict[str, Union[int, str]] = {
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646731-BCF3-11D0-9518-00C04FC2DD79}\\': 'CosmoMedia AudioRenderer3',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646731-BCF3-11D0-9518-00C04FC2DD79}\\INPROCSERVER32\\': os.path.join(cosmo_player_system32_path, 'cm12_dshow.dll'),
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646731-BCF3-11D0-9518-00C04FC2DD79}\\INPROCSERVER32\\THREADINGMODEL': 'Both',	
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646731-BCF3-11D0-9518-00C04FC2DD79}\\MERIT': 2097152,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646731-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\IN\\DIRECTION': 0,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646731-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\IN\\ISRENDERED': 0,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646731-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\IN\\ALLOWEDZERO': 0,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646731-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\IN\\ALLOWEDMANY': 0,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646731-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\IN\\CONNECTSTOPIN': 'Output',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646731-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\IN\\TYPES\\{73647561-0000-0010-8000-00AA00389B71}\\{00000000-0000-0000-0000-000000000000}\\': '',
			
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\FILTER\\{06646731-BCF3-11D0-9518-00C04FC2DD79}\\': 'CosmoMedia AudioRenderer3',

			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\': 'CosmoMedia VideoRenderer3',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\INPROCSERVER32\\': os.path.join(cosmo_player_system32_path, 'cm12_dshow.dll'),
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\INPROCSERVER32\\THREADINGMODEL': 'Both',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\MERIT': 2097152,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\INPUT\\DIRECTION': 0,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\INPUT\\ISRENDERED': 0,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\INPUT\\ALLOWEDZERO': 0,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\INPUT\\ALLOWEDMANY': 0,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\INPUT\\CONNECTSTOPIN': 'Output',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\INS\\INPUT\\TYPES\\{73646976-0000-0010-8000-00AA00389B71}\\{00000000-0000-0000-0000-000000000000}\\': '',
			
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\FILTER\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\': 'CosmoMedia VideoRenderer3',

			'HKEY_LOCAL_MACHINE\\SOFTWARE\\COSMOSOFTWARE\\ROBRENDERER\\1.0\\D3D\\PATH': os.path.join(cosmo_player_system32_path, 'rob10_d3d.dll'),
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\COSMOSOFTWARE\\ROBRENDERER\\1.0\\D3D\\UINAME': 'Direct3D Renderer',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\COSMOSOFTWARE\\ROBRENDERER\\1.0\\NORENDER\\PATH': os.path.join(cosmo_player_system32_path, 'rob10_none.dll'),
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\COSMOSOFTWARE\\ROBRENDERER\\1.0\\NORENDER\\UINAME': 'NonRendering Renderer',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\COSMOSOFTWARE\\ROBRENDERER\\1.0\\OPENGL\\PATH': os.path.join(cosmo_player_system32_path, 'rob10_gl.dll'),
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\COSMOSOFTWARE\\ROBRENDERER\\1.0\\OPENGL\\UINAME': 'OpenGL Renderer',
		}

		# Cosmo Player Renderers:
		#
		# - AUTO = Automatic Renderer Choice
		# - D3D = Direct3D Renderer
		# - NORENDER = NonRendering Renderer
		# - OPENGL = OpenGL Renderer

		renderer = {'auto': 'AUTO', 'directx': 'D3D', 'opengl': 'OPENGL'}.get(config.cosmo_player_renderer)
		assert renderer is not None, f'Unknown Cosmo Player renderer "{config.cosmo_player_renderer}".'

		settings_registry_keys: dict[str, Union[int, str]] = {
			'HKEY_CURRENT_USER\\SOFTWARE\\CosmoSoftware\\CosmoPlayer\\2.1.1\\PANEL_MAXIMIZED': 0, # Minimize dashboard.
			'HKEY_CURRENT_USER\\SOFTWARE\\CosmoSoftware\\CosmoPlayer\\2.1.1\\textureQuality': 1, # Texture quality (auto = 0, best = 1, fastest = 2).
			'HKEY_CURRENT_USER\\SOFTWARE\\CosmoSoftware\\CosmoPlayer\\2.1.1\\transparency': 1, # Nice transparency (off = 0, on = 1).
			'HKEY_CURRENT_USER\\SOFTWARE\\CosmoSoftware\\CosmoPlayer\\2.1.1\\twoPassTextures': 1, # Enable specular and emissive color shine-through on textured objects (off = 0, on = 1).

			'HKEY_CURRENT_USER\\SOFTWARE\\CosmoSoftware\\CosmoPlayer\\2.1.1\\animate': 1 if config.cosmo_player_animate_transitions else 0, # Animate transitions between viewpoints (off = 0, on = 1).
			'HKEY_CURRENT_USER\\SOFTWARE\\CosmoSoftware\\CosmoPlayer\\2.1.1\\renderer': renderer, # See above.
			'HKEY_CURRENT_USER\\SOFTWARE\\CosmoSoftware\\CosmoPlayer\\2.1.1\\showConsoleType': 2 if config.cosmo_player_show_console else 0, # Console on startup (hide = 0, show = 2).
		}

		try:
			for key, value in required_registry_keys.items():
				self.registry.set(key, value)

			# These don't require elevated privileges but there's no point in setting them if the Cosmo Player isn't set up correctly.
			self.registry.clear('HKEY_CURRENT_USER\\SOFTWARE\\CosmoSoftware\\CosmoPlayer\\2.1.1')
			
			for key, value in settings_registry_keys.items():
				self.registry.set(key, value)

		except PermissionError:
			log.error('Failed to configure the Cosmo Player since elevated privileges are required to temporarily set the necessary registry keys.')

	def configure_3dvia_player(self) -> None:
		""" Configures the 3DVIA Player by setting the appropriate registry keys. """

		log.info('Configuring the 3DVIA Player.')

		try:
			# Prevent the update window from popping up every time a 3DVIA experience is loaded.
			self.registry.set('HKEY_LOCAL_MACHINE\\SOFTWARE\\Virtools\\WebPlayer\\Config\\AutoUpdate', 0)

			# 3DVIA Player Renderers:
			#
			# - 0 = Let the page decide
			# - 1 = Force Hardware
			# - 2 = Force Software

			renderer = {'auto': 0, 'hardware': 1, 'software': 2}.get(config._3dvia_renderer)
			assert renderer is not None, f'Unknown 3DVIA Player renderer "{config._3dvia_renderer}".'

			self.registry.set('HKEY_LOCAL_MACHINE\\SOFTWARE\\Virtools\\WebPlayer\\Config\\ForceRenderMode', renderer)

			# For future reference, the 3DVIA network settings are here: HKEY_CURRENT_USER\SOFTWARE\Virtools\Network

		except PermissionError:
			log.error('Failed to configure the 3DVIA Player since elevated privileges are required to temporarily set the necessary registry keys.')

	def delete_user_level_java_properties(self) -> None:
		""" Deletes the current user-level Java deployment properties file. """
		user_level_java_properties_path = os.path.join(self.java_deployment_path, 'deployment.properties')
		delete_file(user_level_java_properties_path)

	def delete_java_plugin_cache(self) -> None:
		""" Deletes the Java Plugin cache directory. """
		java_cache_path = os.path.join(self.java_deployment_path, 'cache')
		delete_directory(java_cache_path)

	def shutdown(self):
		""" Closes the browser, quits the WebDriver, and performs any other cleanup operations. """

		try:
			self.driver.quit()
		except WebDriverException as error:
			log.error(f'Failed to quit the WebDriver with the error: {repr(error)}')

		temporary_path = tempfile.gettempdir()
		
		# Delete the temporary files directories from previous executions. Remeber that there's a bug
		# when running the Firefox WebDriver on Windows that prevents it from shutting down properly
		# if Ctrl-C is used.

		temporary_search_path = os.path.join(temporary_path, 'rust_mozprofile*')
		for path in iglob(temporary_search_path):
			try:
				log.info(f'Deleting the temporary directory "{path}".')
				delete_directory(path)
			except PermissionError as error:
				log.error(f'Failed to delete the temporary directory with the error: "{repr(error)}".')

		temporary_search_path = os.path.join(temporary_path, '*', 'webdriver-py-profilecopy')
		for path in iglob(temporary_search_path):
			try:
				path = os.path.dirname(path)
				log.info(f'Deleting the temporary directory "{path}".')
				delete_directory(path)
			except PermissionError as error:
				log.error(f'Failed to delete the temporary directory with the error: "{repr(error)}".')

		temporary_search_path = os.path.join(temporary_path, 'tmpaddon-*')
		for path in iglob(temporary_search_path):
			log.info(f'Deleting the temporary file "{path}".')
			delete_file(path)

		if self.use_plugins:
			self.delete_user_level_java_properties()
			self.delete_java_plugin_cache()

		if self.use_autoit:
			for process in self.autoit_processes:
				try:
					process.terminate()
				except OSError as error:
					log.error(f'Failed to terminate the process {process} with the error: {repr(error)}')

		self.registry.restore()

		# Kill a potential orphan Firefox process because of the bug described above.
		kill_process_by_pid(self.pid)

	def __enter__(self):
		return (self, self.driver)

	def __exit__(self, exception_type, exception_value, traceback):
		self.shutdown()

	def set_preference(self, name: str, value: Union[bool, int, str]) -> None:
		""" Sets a Firefox preference at runtime via XPCOM. Note that this will change the current page to "about:config".
		This should be done sparingly since the vast majority of preferences can be defined before creating the WebDriver. """

		# We can't use this interface to change the prefs in a regular page. We must navigate to "about:config" first.
		# See:
		# - https://stackoverflow.com/a/48816511/18442724
		# - https://web.archive.org/web/20210417185248if_/https://developer.mozilla.org/en-US/docs/Mozilla/JavaScript_code_modules/Services.jsm
		# - https://web.archive.org/web/20210629053921if_/https://developer.mozilla.org/en-US/docs/Mozilla/Tech/XPCOM/Reference/Interface/nsIPrefBranch
		setter_name = 'setBoolPref' if isinstance(value, bool) else ('setIntPref' if isinstance(value, int) else 'setCharPref')
		self.driver.get(Browser.CONFIG_URL)
		self.driver.execute_script(f'''
									// const prefs = Components.classes["@mozilla.org/preferences-service;1"].getService(Components.interfaces.nsIPrefBranch);
									// prefs.{setter_name}(arguments[0], arguments[1]);
									Components.utils.import("resource://gre/modules/Services.jsm");
									Services.prefs.{setter_name}(arguments[0], arguments[1]);
									''', name, value)

	def set_fallback_encoding_for_snapshot(self, snapshot: Snapshot) -> None:
		""" Changes Firefox's fallback character encoding to the best charset for a given Wayback Machine snapshot.
		This will either be a user-defined charset or an autodetected charset determined by the Wayback Machine.
		Note that this function will retry this last operation if the Wayback Machine is unavailable. """
		
		if not config.enable_fallback_encoding:
			return

		while True:

			retry = False

			try:
				encoding = snapshot.Encoding

				# Note that not every snapshot has a guessed encoding.
				if not encoding and config.use_guessed_encoding_as_fallback:

					global_rate_limiter.wait_for_wayback_machine_rate_limit()
					response = global_session.head(snapshot.WaybackUrl)
					response.raise_for_status()
					
					# This header requires a snapshot URL with the iframe modifier.
					encoding = response.headers.get('x-archive-guessed-charset', '')

				# E.g. https://web.archive.org/web/19991011153317if_/http://www.geocities.com/Athens/Delphi/1240/midigr.htm
				# In older Firefox versions, the "windows-1252" encoding is used.
				# In modern versions or when using the Wayback Machine's guessed encoding, "iso-8859-7" is used.
				log.debug(f'Setting the fallback encoding to "{encoding}".')
				self.set_preference('intl.charset.fallback.override', encoding)

			except RequestException as error:
				log.error(f'Failed to find the guessed encoding for the snapshot {snapshot} with the error: {repr(error)}')
				# Keep trying until the Wayback Machine is available.
				retry = not is_wayback_machine_available()
			except WebDriverException as error:
				log.error(f'Failed to set the fallback preference with the error: {repr(error)}')
			finally:
				if retry:
					continue
				else:
					break

	def go_to_wayback_url(self, wayback_url: str, close_windows: bool = False) -> None:
		""" Navigates to a Wayback Machine URL, taking into account any rate limiting and retrying if the service is unavailable. """

		for i in itertools.count():

			retry = False
			retry_wait = min(config.wayback_machine_retry_backoff * 2 ** (i-1), config.wayback_machine_retry_max_wait) if i > 0 else 0

			try:
				if close_windows:
					self.close_all_windows()

				self.driver.get(Browser.BLANK_URL)

				global_rate_limiter.wait_for_wayback_machine_rate_limit()
				self.driver.get(wayback_url)

				# Skip the availability check for auto-generated media pages.
				# This check works for expected downtime (e.g. maintenance).
				retry = not wayback_url.startswith('file:') and not is_wayback_machine_available()

			except TimeoutException:
				log.warning(f'Timed out after waiting {config.page_load_timeout} seconds for the page to load: "{wayback_url}".')
				# This covers the same case as the next exception without passing the error
				# along to the caller if a regular page took too long to load.
				retry = not is_wayback_machine_available()

			except WebDriverException:
				# For cases where the Wayback Machine is unreachable (unexpected downtime)
				# and an error is raised because we were redirected to "about:neterror".
				# If this was some other error and the service is available, then it should
				# be handled by the caller.
				if is_wayback_machine_available():
					raise
				else:
					retry = True

			finally:
				if retry:
					log.warning(f'Waiting {retry_wait} seconds for the Wayback Machine to become available again.')
					sleep(retry_wait)
					continue
				else:
					break

	def reload_page_from_cache(self) -> None:
		""" Reloads the current page using the F5 shortcut so that its assets are loaded from the cache.
		Does nothing if Firefox is running in headless mode. """

		if self.window is not None:
			
			self.window.send_keystrokes('{F5}')

			try:
				condition = lambda driver: driver.execute_script('return document.readyState === "complete";')
				WebDriverWait(self.driver, config.page_load_timeout).until(condition)
			except TimeoutException:
				log.warning(f'Timed out after waiting {config.page_load_timeout} seconds for the page to reload from cache: "{self.driver.current_url}".')

	def was_wayback_url_redirected(self, expected_wayback_url: str) -> tuple[bool, Optional[str], Optional[str]]:
		""" Checks if a Wayback Machine page was redirected. In order to cover all edge cases, this function only works with snapshot URLs
		and not any generic website. """
		
		# The redirectCount only seems be greater than zero for some redirected snapshots when opened in Firefox 52 ESR.
		# In modern Firefox versions, the redirectCount is more accurate. That being said, the tests in Firefox 52 didn't
		# result in any false positives so we'll use it too.
		#
		# 1. https://web.archive.org/web/20100823194716if_/http://www.netfx-inc.com:80/purr/
		# Redirects to https://web.archive.org/web/20100822160707/http://www.netfx-inc.com/loan-tax-card/loan-tax-card.php
		# In this case, the modifier is removed and the redirectCount is zero.
		#
		# 2. https://web.archive.org/web/19981205113927if_/http://www.fortunecity.com/millenium/bigears/43/index.html
		# Redirects to https://web.archive.org/web/19981203010807if_/http://www.fortunecity.com/millenium/bigears/43/startherest.html
		# In this case, the modifier is kept and the redirectCount is one.
		# Note that this page is only redirected after a few seconds.
		#
		# 3. https://web.archive.org/web/20010201051300if_/http://mail.quote.com:80/
		# Redirects to https://web.archive.org/web/20010201051300if_/http://mail.quote.com:80/default.asp
		# In this case, the URL was not archived, a 404 page is served by the Wayback machine, and the redirectCount is zero.
		#
		# 4. https://web.archive.org/web/20081203054436if_/http://www.symbolicsoft.com:80/vrml/pong3d.wrl
		# Redirects to "https://web.archive.org/", even though it's a valid page according to the CDX API:
		# https://web.archive.org/cdx/search/cdx?url=http://www.symbolicsoft.com:80/vrml/pong3d.wrl&fl=original,timestamp,statuscode,mimetype
		# In this case, the redirectCount is zero.
		# Note also that the resulting page no longer follows the snapshot URL format.
		#
		# 5. https://web.archive.org/web/19970214141804if_/http://www.worldculture.com:80/intro.htm
		# Redirects to https://web.archive.org/web/19970414134642if_/http://www.worldculture.com/intro.htm
		# In this case, the modifier is kept and the redirectCount is one.
		# Note also that, timestamps aside, the only difference between the archived URLs is the port.
		#
		# 6. https://web.archive.org/web/19990117005032if_/http://www.ce.washington.edu:80/%7Esoroos/java/published/pool.html
		# Is decoded when viewed in GUI mode (i.e. not headless):
		# https://web.archive.org/web/19990117005032if_/http://www.ce.washington.edu:80/~soroos/java/published/pool.html
		# But this is *not* a redirect even though the URL strings are technically different.
		# In this case, the redirectCount is zero.
		#
		# Additionally, keep in mind that the Wayback Machine considers certain URLs the same for the sake of convenience.
		# For example, the following are all the same snapshot:
		# - https://web.archive.org/web/20020120142510if_/http://example.com/
		# - https://web.archive.org/web/20020120142510if_/https://example.com/
		# - https://web.archive.org/web/20020120142510if_/http://example.com:80/
		# - https://web.archive.org/web/20020120142510if_/http://www.example.com/
		#
		# This shouldn't matter for our checks since the Wayback Machine will use whichever URL you pass it, but it's
		# worth noting for future reference.

		expected_wayback_parts = parse_wayback_machine_snapshot_url(expected_wayback_url)
		assert expected_wayback_parts is not None, f'The expected URL "{expected_wayback_url}" is not a valid Wayback Machine snapshot.'

		current_url = self.driver.current_url
		current_wayback_parts = parse_wayback_machine_snapshot_url(current_url)

		# Catches example #4.
		if current_wayback_parts is None:
			log.debug(f'Passed the redirection test since the current page is not a valid snapshot: "{expected_wayback_url}" -> "{current_url}".')
			return True, current_url, expected_wayback_parts.Timestamp

		# Catches examples #2 and #5.
		redirect_count = self.driver.execute_script('return window.performance.navigation.redirectCount;')
		if redirect_count > 0:
			log.debug(f'Passed the redirection test with the redirect count at {redirect_count}: "{expected_wayback_url}" -> "{current_url}".')
			return True, current_wayback_parts.Url, current_wayback_parts.Timestamp

		# Catches example #1.
		if current_wayback_parts.Modifier != expected_wayback_parts.Modifier:
			log.debug(f'Passed the redirection test since the modifiers changed: "{expected_wayback_url}" -> "{current_url}".')
			return True, current_wayback_parts.Url, current_wayback_parts.Timestamp

		# Catches examples #2 and #5 if they weren't detected before.
		if current_wayback_parts.Timestamp != expected_wayback_parts.Timestamp:
			log.debug(f'Passed the redirection test since the timestamps changed: "{expected_wayback_url}" -> "{current_url}".')
			return True, current_wayback_parts.Url, current_wayback_parts.Timestamp

		# Catches example #3 but lets #6 through.
		if current_wayback_parts.Url.lower() not in [expected_wayback_parts.Url.lower(), unquote(expected_wayback_parts.Url.lower())]:
			log.debug(f'Passed the redirection test since the URLs changed: "{expected_wayback_url}" -> "{current_url}".')
			return True, current_wayback_parts.Url, current_wayback_parts.Timestamp

		return False, None, None

	def go_to_blank_page_with_text(self, *args) -> None:
		""" Navigates to an autogenerated page where each argument is displayed in a different line. """
		
		text = '<br>'.join(args)
		html = f'''
				<!DOCTYPE html>
				<html lang="en-US">

				<head>
					<meta charset="utf-8">
					<title>Eternal Wanderer</title>

					<style>
						.center {{
							margin: 0;
							padding: 0;
							text-align: center;
							position: absolute;
							top: 50%;
							left: 50%;
							transform: translateX(-50%) translateY(-50%);
						}}

						.text {{
							font-size: 30px;
							overflow: hidden;
							white-space: nowrap;
						}}
					</style>
				</head>

				<body>
					<div class="center">
						<div class="text">
							{text}
						</div>
					</div>
				</body>

				</html>
				'''

		base64_html = b64encode(html.encode()).decode()
		self.driver.get(f'data:text/html;base64,{base64_html}')

	def traverse_frames(self, format_wayback_urls: bool = False, skip_missing: bool = False) -> Iterator[str]:
		""" Traverses recursively through the current web page's frames. This function yields each frame's source URL.
		It can optionally skip 404 pages and convert every URL into a Wayback Machine snapshot. Note that the latter
		requires the document's URL to already be formatted correctly, otherwise the it wouldn't be possible to
		determine each snapshot's URL. """

		if format_wayback_urls:
			root_wayback_parts = parse_wayback_machine_snapshot_url(self.driver.current_url)

		def recurse(current_url: str) -> Iterator[str]:
			""" Helper function used to traverse through every frame recursively. """

			if format_wayback_urls and root_wayback_parts is not None:
				wayback_parts = parse_wayback_machine_snapshot_url(current_url)

				if wayback_parts is not None:
					wayback_parts.Modifier = root_wayback_parts.Modifier
				else:
					wayback_parts = dataclasses.replace(root_wayback_parts, Url=current_url)
				
				current_url = compose_wayback_machine_snapshot_url(parts=wayback_parts)

			if skip_missing and format_wayback_urls:
				global_rate_limiter.wait_for_wayback_machine_rate_limit()

			# Redirects are allowed here since the frame's timestamp is inherited from the root page's snapshot,
			# meaning that in the vast majority of cases we're going to be redirected to the nearest archived
			# copy (if one exists). Redirected pages keep their modifier.
			# E.g. https://web.archive.org/web/19970702100947if_/http://www.informatik.uni-rostock.de:80/~knorr/homebomb.html
			# Where the following frame wasn't archived:
			# https://web.archive.org/web/19970702100947if_/http://www.informatik.uni-rostock.de/~knorr/bombtitle.html
			if skip_missing and not is_url_available(current_url, allow_redirects=True):
				log.warning(f'Skipping the frame "{current_url}" since the page was not found.')
				return

			log.debug(f'Traversing the frame "{current_url}".')
			yield current_url
			
			frame_list = self.driver.find_elements_by_tag_name('frame') + self.driver.find_elements_by_tag_name('iframe')
			for frame in frame_list:
				
				frame_source = frame.get_attribute('src')

				# Skip missing frames whose source would otherwise be converted to "https://web.archive.org/".
				if not frame_source:
					log.debug('Skipping a frame without a source.')
					continue

				# Checking for valid URLs using netloc only makes sense if it was properly decoded.
				# E.g. "http%3A//www.geocities.com/Hollywood/Hills/5988/main.html" would result in
				# an empty netloc instead of "www.geocities.com".
				frame_source = unquote(frame_source)
				parts = urlparse(frame_source)

				# Skip frames without valid URLs.
				if not parts.netloc:
					log.debug(f'Skipping the invalid frame "{frame_source}".')
					continue

				# Skip any frames that were added by the Internet Archive (e.g. https://archive.org/includes/donate.php).
				# This works with the format Wayback Machine URLs parameter since we only format them after calling the
				# function recursively below.
				if is_url_from_domain(parts, 'archive.org'):
					log.debug(f'Skipping the Internet Archive frame "{frame_source}".')
					continue

				try:
					condition = webdriver_conditions.frame_to_be_available_and_switch_to_it(frame)
					WebDriverWait(self.driver, 15).until(condition)
				except TimeoutException:
					log.debug('Timed out while waiting for the frame to be available.')
					continue
				
				yield from recurse(frame_source)
				self.driver.switch_to.parent_frame()

		try:
			yield from recurse(self.driver.current_url)
		except WebDriverException as error:
			log.error(f'Failed to traverse the frames with the error: {repr(error)}')

		self.driver.switch_to.default_content()
	
	def close_all_windows(self) -> None:
		""" Closes every Firefox tab and window, leaving only a single blank page. """

		try:
			self.driver.get(Browser.BLANK_URL)
			current_handle = self.driver.current_window_handle

			for handle in self.driver.window_handles:
				if handle != current_handle:
					self.driver.switch_to.window(handle)
					self.driver.close()

			self.driver.switch_to.window(current_handle)

			try:
				condition = webdriver_conditions.number_of_windows_to_be(1)
				WebDriverWait(self.driver, 15).until(condition)
			except TimeoutException:
				log.warning('Timed out while waiting for the other browser windows to close.')

		except NoSuchWindowException:
			pass
	
	def unload_plugin_content(self, skip_applets: bool = False) -> None:
		""" Unloads any content embedded using the object/embed/applet tags in the current web page and its frames.
		This function should not be called more than once if any of this content is being played by the VLC plugin. """

		selectors = 'object, embed' if skip_applets else 'object, embed, applet'

		try:
			for _ in self.traverse_frames():
				self.driver.execute_script(	'''
											const SOURCE_ATTRIBUTES = ["data", "src", "code", "object", "target", "mrl", "filename"];

											const plugin_nodes = document.querySelectorAll(arguments[0]);

											for(const element of plugin_nodes)
											{
												// If the element is using the VLC plugin and is currently being
												// monitored so it doesn't play twice, then we need to ensure that
												// the last known position matches with any changes we make here.
												// See the Fix Vlc Embed user script for more details.
												//
												// Note also that the NPObject wrapper (i.e. element.input) is
												// undefined after the content is unloaded.
												if("vlcLastPosition" in element.dataset && element.input)
												{
													// Just changing the position doesn't make it start playing.
													element.input.position = 0;
													element.dataset.vlcLastPosition = element.input.position;
												}

												for(const source_attribute of SOURCE_ATTRIBUTES)
												{
													const source = element.getAttribute(source_attribute);
													if(source)
													{
														// Clearing the source attribute directly is what unloads
														// the content.
														element.dataset["wanderer_" + source_attribute] = source;
														element[source_attribute] = "";
													}
												}
											}
											''', selectors)
		except WebDriverException as error:
			log.error(f'Failed to unload the plugin content with the error: {repr(error)}')

	def reload_plugin_content(self, skip_applets: bool = False) -> None:
		""" Reloads any content embedded using the object/embed/applet tags in the current web page and its frames.
		This function should not be called more than once if any of this content is being played by the VLC plugin. """

		selectors = 'object, embed' if skip_applets else 'object, embed, applet'

		try:
			for _ in self.traverse_frames():
				self.driver.execute_script(	'''
											const SOURCE_ATTRIBUTES = ["data", "src", "code", "object", "target", "mrl", "filename"];

											const plugin_nodes = document.querySelectorAll(arguments[0]);

											for(const element of plugin_nodes)
											{
												// See the comments in unload_plugin_content().
												if("vlcLastPosition" in element.dataset && element.input)
												{
													element.input.position = 0;
													element.dataset.vlcLastPosition = element.input.position;
												}

												for(const source_attribute of SOURCE_ATTRIBUTES)
												{
													const original_attribute = "wanderer_" + source_attribute;
													if(original_attribute in element.dataset)
													{
														element.setAttribute(source_attribute, element.dataset[original_attribute]);
														delete element.dataset[original_attribute];
													}

													// This extra check is for cases when the content wasn't unloaded
													// (i.e. unload_plugin_content() wasn't called).
													if(element.hasAttribute(source_attribute)) element[source_attribute] += "";
												}
											}
											''', selectors)
		except WebDriverException as error:
			log.error(f'Failed to reload the plugin content with the error: {repr(error)}')

	def toggle_fullscreen(self) -> None:
		""" Toggles fullscreen Firefox. Does nothing if Firefox is running in headless mode. """
		if self.window is not None:
			self.window.send_keystrokes('{F11}')
		
	def bring_to_front(self) -> None:
		""" Focuses and brings the Firefox window to front. Does nothing if Firefox is running in headless mode. """
		if self.window is not None:
			self.window.set_focus()

	def count_plugin_instances(self, class_name: str = 'GeckoPluginWindow') -> Optional[int]:
		""" Counts the total number of running plugin instances in every Firefox tab and window. For example,
		if a page has two Flash movies and one Java applet, this function would count three instances (assuming
		Firefox had the required plugins installed). Returns None if Firefox is running in headless mode. """
		return len(self.window.children(class_name=class_name)) if self.window is not None else None

class TemporaryRegistry():
	""" A temporary registry that remembers and undos any changes (key additions and deletions) made to the Windows registry. """

	# For the sake of convenience, this class mostly deals with registry key values and forces all queries to look at the
	# 32-bit view of the registry in both 32 and 64-bit applications. Although key values are the main focus, we do keep
	# track of any keys to delete since setting a value may require creating any missing intermediate keys.
	#
	# Focusing only on 32-bit applications makes sense since we're configuring old web plugins. Depending on the registry
	# key, Windows will redirect a query to a different location. For example, writing a value to the registry key
	# "HKEY_CLASSES_ROOT\CLSID\{06646731-BCF3-11D0-9518-00C04FC2DD79}" will store the value in the following keys:
	#
	# - "HKEY_CLASSES_ROOT\WOW6432Node\CLSID\{06646731-BCF3-11D0-9518-00C04FC2DD79}"
	# - "HKEY_LOCAL_MACHINE\SOFTWARE\Classes\WOW6432Node\CLSID\{06646731-BCF3-11D0-9518-00C04FC2DD79}"
	# - "HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Classes\CLSID\{06646731-BCF3-11D0-9518-00C04FC2DD79}"
	#
	# See:
	# - https://docs.microsoft.com/en-us/windows/win32/winprog64/registry-reflection
	# - https://docs.microsoft.com/en-us/windows/win32/winprog64/accessing-an-alternate-registry-view

	original_state: dict[tuple[int, str, str], tuple[Optional[int], Any]]
	keys_to_delete: set[tuple[int, str, str]]
	key_paths_to_delete: set[tuple[int, str]]

	OPEN_HKEYS = {
		'hkey_classes_root': winreg.HKEY_CLASSES_ROOT,
		'hkey_current_user': winreg.HKEY_CURRENT_USER,
		'hkey_local_machine': winreg.HKEY_LOCAL_MACHINE,
		'hkey_users': winreg.HKEY_USERS,
		'hkey_performance_data': winreg.HKEY_PERFORMANCE_DATA,
		'hkey_current_config': winreg.HKEY_CURRENT_CONFIG,
		'hkey_dyn_data': winreg.HKEY_DYN_DATA,
	}

	def __init__(self):
		self.original_state = {}
		self.keys_to_delete = set()
		self.key_paths_to_delete = set()

	@staticmethod
	def partition_key(key: str) -> tuple[int, str, str]:
		""" Separates a registry key string into its hkey, key path, and sub key components. """

		first_key, _, key_path = key.partition('\\')
		key_path, _, sub_key = key_path.rpartition('\\')

		first_key = first_key.lower()
		if first_key not in TemporaryRegistry.OPEN_HKEYS:
			raise KeyError(f'The registry key "{key}" does not start with a valid HKEY.')

		hkey = TemporaryRegistry.OPEN_HKEYS[first_key]
		return (hkey, key_path, sub_key)

	def get(self, key: str) -> Any:
		""" Gets the value of a registry key. Returns None if the key doesn't exist. """

		try:
			hkey, key_path, sub_key = TemporaryRegistry.partition_key(key)
			with OpenKey(hkey, key_path, access=winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as key_handle:
				value, _ = QueryValueEx(key_handle, sub_key)
		except OSError:
			value = None

		return value

	def set(self, key: str, value: Union[int, str], type_: Optional[int] = None) -> Any:
		""" Sets the value of a registry key. Any missing intermediate keys are automatically created. """

		hkey, key_path, sub_key = TemporaryRegistry.partition_key(key)
		
		if type_ is None:
			if isinstance(value, int):
				type_ = winreg.REG_DWORD
			elif isinstance(value, str):
				type_ = winreg.REG_SZ
			else:
				raise ValueError(f'The type of the value "{value}" could not be autodetected for the registry key "{key}".')	
	
		if (hkey, key_path) not in self.key_paths_to_delete:

			intermediate_keys = key_path.split('\\')
			while len(intermediate_keys) > 1:

				try:
					intermediate_full_key_path = '\\'.join(intermediate_keys)
					with OpenKey(hkey, intermediate_full_key_path) as key_handle:
						sub_key_exists = True
				except OSError:
					sub_key_exists = False

				intermediate_sub_key = intermediate_keys.pop()
				intermediate_key_path = '\\'.join(intermediate_keys)

				if sub_key_exists:
					break
				else:
					self.keys_to_delete.add((hkey, intermediate_key_path, intermediate_sub_key))

			self.key_paths_to_delete.add((hkey, key_path))

		original_state_key = (hkey, key_path, sub_key)
		original_state_value: tuple[Optional[int], Any]

		with CreateKeyEx(hkey, key_path, access=winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_32KEY) as key_handle:
			try:
				original_value, original_type = QueryValueEx(key_handle, sub_key)
				original_state_value = (original_type, original_value)
				result = original_value
			except OSError:
				original_state_value = (None, None)
				result = None

			SetValueEx(key_handle, sub_key, 0, type_, value)

		if original_state_key not in self.original_state:
			self.original_state[original_state_key] = original_state_value

		return result

	def delete(self, key: str) -> tuple[bool, Any]:
		""" Removes a value from a registry key. Returns true and its data if it existed, otherwise false and None. """

		hkey, key_path, sub_key = TemporaryRegistry.partition_key(key)

		try:
			with OpenKey(hkey, key_path, access=winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_32KEY) as key_handle:
				original_value, original_type = QueryValueEx(key_handle, sub_key)
				DeleteValue(key_handle, sub_key)

			original_state_key = (hkey, key_path, sub_key)
			original_state_value = (original_type, original_value)

			if original_state_key not in self.original_state:
				self.original_state[original_state_key] = original_state_value

			success = True
			result = original_value
		except OSError as error:
			log.error(f'Failed to delete the value "{key}" with the error: {repr(error)}')
			success = False
			result = None

		return success, result

	def clear(self, key: str) -> None:
		""" Deletes every value in a registry key. Does not modify its subkeys or their values. """

		key_list = [key for key, _, _ in TemporaryRegistry.traverse(key)]
		for key in key_list:
			self.delete(key)

	@staticmethod
	def traverse(key: str, recursive: bool = False) -> Iterator[tuple[str, Any, int]]:
		""" Iterates over the values of a registry key and optionally its subkeys. """

		hkey, key_path, sub_key = TemporaryRegistry.partition_key(key)
		key_path = f'{key_path}\\{sub_key}'

		try:
			with OpenKey(hkey, key_path, access=winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as key_handle: 
				
				num_keys, num_values, _ = QueryInfoKey(key_handle)

				for i in range(num_values):
					try:
						name, data, type_ = EnumValue(key_handle, i)
						yield f'{key}\\{name}', data, type_
					except OSError as error:
						log.error(f'Failed to enumerate value {i+1} of {num_values} in the registry key "{key}" with the error: {repr(error)}')

				if recursive:

					for i in range(num_keys):
						try:
							child_sub_key = EnumKey(key_handle, i)
							yield from TemporaryRegistry.traverse(f'{key}\\{child_sub_key}', recursive=recursive)
						except OSError as error:
							log.error(f'Failed to enumerate subkey {i+1} of {num_keys} in the registry key "{key}" with the error: {repr(error)}')

		except OSError as error:
			log.error(f'Failed to traverse the registry key "{key}" with the error: {repr(error)}')

	@staticmethod
	def delete_key_tree(key: str) -> None:
		""" Deletes a registry key and all of its subkeys. """

		hkey, key_path, sub_key = TemporaryRegistry.partition_key(key)
		key_path = f'{key_path}\\{sub_key}'

		try:
			with OpenKey(hkey, key_path, access=winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_32KEY) as key_handle: 
				
				num_keys, _, _ = QueryInfoKey(key_handle)

				for i in range(num_keys):
					try:
						child_sub_key = EnumKey(key_handle, i)
						TemporaryRegistry.delete_key_tree(f'{key}\\{child_sub_key}')
					except OSError as error:
						log.error(f'Failed to enumerate subkey {i+1} of {num_keys} in the registry key "{key}" with the error: {repr(error)}')

				try:
					# Delete self.
					DeleteKey(key_handle, '')
				except OSError as error:
					log.error(f'Failed to delete the registry key "{key}" with the error: {repr(error)}')

		except OSError as error:
			log.error(f'Failed to delete the registry key tree "{key}" with the error: {repr(error)}')

	def restore(self) -> None:
		""" Restores the Windows registry to its original state by undoing any changes, additions, and deletions. """

		for (hkey, key_path, sub_key), (type_, value) in self.original_state.items():
			try:
				with OpenKey(hkey, key_path, access=winreg.KEY_WRITE | winreg.KEY_WOW64_32KEY) as key_handle:
					if type_ is None:
						DeleteValue(key_handle, sub_key)
					else:
						SetValueEx(key_handle, sub_key, 0, type_, value)
			except OSError as error:
				log.error(f'Failed to restore the original value "{value}" type {type_} of the registry key "{hkey}\\{key_path}\\{sub_key}" with the error: {repr(error)}')

		keys_to_delete = sorted(self.keys_to_delete, key=lambda x: len(x[1]), reverse=True)
		for (hkey, key_path, sub_key) in keys_to_delete:
			try:
				with OpenKey(hkey, key_path, access=winreg.KEY_WRITE | winreg.KEY_WOW64_32KEY) as key_handle:
					DeleteKey(key_handle, sub_key)
			except OSError as error:
				log.error(f'Failed to delete the registry key "{hkey}\\{key_path}\\{sub_key}" with the error: {repr(error)}')

		self.original_state = {}
		self.keys_to_delete = set()
		self.key_paths_to_delete = set()

	def __enter__(self):
		return self

	def __exit__(self, exception_type, exception_value, traceback):
		self.restore()

def clamp(value: float, min_value: float, max_value: float) -> float:
	""" Clamps a number between a minimum and maximum value. """
	return max(min_value, min(value, max_value))

def get_current_timestamp() -> str:
	""" Retrieves the current timestamp in UTC. """
	return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

def extract_media_extension_from_url(url: str) -> str:
	""" Retrieves the file extension from a media file URL. The returned extension may be
	different from the real value for the sake of convenience (e.g. compressed VRML worlds). """

	parts = urlparse(url)
	path = parts.path.lower()

	# For compressed VRML worlds that would otherwise be stored as "gz".
	if path.endswith('.wrl.gz'):
		extension = 'wrz'
	else:
		_, extension = os.path.splitext(path)
		extension = extension.removeprefix('.')

	return extension

def find_best_wayback_machine_snapshot(timestamp: str, url: str) -> tuple[CDXSnapshot, bool, Optional[str]]:
	""" Finds the best Wayback Machine snapshot given its timestamp and URL. By best snapshot we mean
	locating the nearest one and then finding the oldest capture where the content is identical. """

	global_rate_limiter.wait_for_cdx_api_rate_limit()
	cdx = Cdx(url=url, filters=['statuscode:200'])
	snapshot = cdx.near(wayback_machine_timestamp=timestamp)

	global_rate_limiter.wait_for_cdx_api_rate_limit()
	cdx.filters.append(f'digest:{snapshot.digest}')
	snapshot = cdx.oldest()

	# Consider plain text files since regular HTML pages may be served with this MIME type.
	# E.g. https://web.archive.org/web/20011201170113if_/http://www.yahoo.co.jp/bin/top3
	is_media = snapshot.mimetype not in ['text/html', 'text/plain']
	media_extension = extract_media_extension_from_url(snapshot.original) if is_media else None

	return snapshot, is_media, media_extension

def find_extra_wayback_machine_snapshot_info(wayback_url: str) -> Optional[str]:
	""" Finds the last modified time of a Wayback Machine snapshot. Note that not every snapshot has this information. """

	# The last modified time seems to always be returned regardless of the modifier.
	# There's other headers that require the iframe modifier (e.g. x-archive-guessed-charset).

	last_modified_time = None

	try:
		global_rate_limiter.wait_for_wayback_machine_rate_limit()
		response = global_session.head(wayback_url)
		response.raise_for_status()
		
		last_modified_header = response.headers.get('x-archive-orig-last-modified')
		if last_modified_header is not None:

			# Fix an issue where the time zone appears twice.
			# E.g. https://web.archive.org/web/19961018174824if_/http://www.com-stock.com:80/dave/
			# Where the last modified time is "Friday, 18-Oct-96 15:48:24 GMT GMT".
			if last_modified_header.endswith('GMT GMT'):
				log.warning(f'Fixing the broken last modified time "{last_modified_header}".')
				last_modified_header = last_modified_header.replace('GMT GMT', 'GMT')

			# Fix an issue where the time zone and time are not delimited.
			# E.g. https://web.archive.org/web/20060813091112if_/http://www.phone-books.net/
			# Where the last modified time is "Sun, 13 Aug 2006 09:11:11GMT".
			if last_modified_header.endswith('GMT') and not last_modified_header.endswith(' GMT'):
				log.warning(f'Fixing the broken last modified time "{last_modified_header}".')
				last_modified_header = last_modified_header.removesuffix('GMT') + ' GMT'

			# Fix an issue where the minutes and seconds are not delimited.
			# E.g. https://web.archive.org/web/20010926042147if_/http://geocities.yahoo.co.jp:80/
			# Where the last modified time is "Mon, 24 Sep 2001 04:2146 GMT".
			split_header = last_modified_header.split(':')
			if len(split_header) == 2:
				log.warning(f'Fixing the broken last modified time "{last_modified_header}".')
				last_modified_header = ':'.join([split_header[0], split_header[1][:2], split_header[1][2:]])

			# Fix an issue where the time is missing. This solution adds potentially
			# incorrect information to the datetime, which is fine for our purposes.
			# E.g. https://web.archive.org/web/19970112174206if_/http://www.manish.com:80/jneko/
			# Where the last modified time is "Wed, 27 Mar 1996 ? GMT".
			if last_modified_header.endswith('? GMT'):
				log.warning(f'Fixing the broken last modified time "{last_modified_header}".')
				last_modified_header = last_modified_header.replace('? GMT', '00:00:00 GMT')

			last_modified_time = parsedate_to_datetime(last_modified_header).strftime(Snapshot.TIMESTAMP_FORMAT)

	except RequestException as error:
		log.error(f'Failed to find any extra information from the snapshot "{wayback_url}" with the error: {repr(error)}')
	except (ValueError, TypeError) as error:
		# Catching TypeError is necessary for other unhandled broken dates.
		log.error(f'Failed to parse the last modified time "{last_modified_header}" of the snapshot "{wayback_url}" with the error: {repr(error)}')
	
	return last_modified_time

@dataclass
class WaybackParts:
	Timestamp: str
	Modifier: Optional[str]
	Url: str

WAYBACK_MACHINE_SNAPSHOT_URL_REGEX = re.compile(r'https?://web\.archive\.org/web/(?P<timestamp>\d+)(?P<modifier>[a-z]+_)?/(?P<url>.+)', re.IGNORECASE)

def parse_wayback_machine_snapshot_url(url: str) -> Optional[WaybackParts]:
	""" Divides the URL of a Wayback Machine snapshot into its basic components. """
	
	result = None

	match = WAYBACK_MACHINE_SNAPSHOT_URL_REGEX.fullmatch(url)
	if match is not None:
		
		timestamp = match['timestamp']
		modifier = match['modifier']
		url = match['url']
		result = WaybackParts(timestamp, modifier, url)

	return result

def compose_wayback_machine_snapshot_url(*, timestamp: Optional[str] = None, modifier: Optional[str] = None,
										 url: Optional[str] = None, parts: Optional[WaybackParts] = None) -> str:
	""" Combines the basic components of a Wayback Machine snapshot into a URL. """

	if parts is not None:
		timestamp = parts.Timestamp
		modifier = parts.Modifier
		url = parts.Url

	if timestamp is None or url is None:
		raise ValueError('Missing the Wayback Machine timestamp and URL.')

	modifier = modifier or ''
	return f'https://web.archive.org/web/{timestamp}{modifier}/{url}'

def is_url_available(url: str, allow_redirects: bool = False) -> bool:
	""" Checks if a URL is available. """
	
	try:
		response = requests.head(url, allow_redirects=allow_redirects)
		result = response.status_code < 400 if allow_redirects else response.status_code == 200
	except RequestException:
		result = False

	return result

def is_wayback_machine_available() -> bool:
	""" Checks if both the Wayback Machine website and the CDX server are available. """
	global_rate_limiter.wait_for_wayback_machine_rate_limit()
	global_rate_limiter.wait_for_cdx_api_rate_limit()
	return 	is_url_available('https://web.archive.org/', allow_redirects=True) \
		and is_url_available('https://web.archive.org/cdx/search/cdx?url=archive.org&limit=1', allow_redirects=True)

def is_url_from_domain(url: Union[str, ParseResult], domain: str) -> bool:
	""" Checks if a URL is part of a domain or any of its subdomains. """
	parts = urlparse(url) if isinstance(url, str) else url
	return parts.hostname is not None and (parts.hostname == domain or parts.hostname.endswith('.' + domain))

def was_exit_command_entered() -> bool:
	""" Checks if an exit command was typed. Used to stop the execution of scripts that can't use Ctrl-C to terminate. """

	result = False

	if msvcrt.kbhit():
		keys = [msvcrt.getwch()]

		while msvcrt.kbhit():
			keys.append(msvcrt.getwch())

		command = ''.join(keys)

		if 'pause' in command:
			command = input('Paused: ')

		if 'exit' in command:
			result = True

	return result

def delete_file(path: str) -> bool:
	""" Deletes a file. Does nothing if it doesn't exist. """
	try:
		os.remove(path)
		success = True
	except OSError:
		success = False
	return success

def delete_directory(path: str) -> bool:
	""" Deletes a directory and all of its subdirectories. Does nothing if it doesn't exist. """
	try:
		shutil.rmtree(path)
		success = True
	except OSError:
		success = False
	return success

# Ignore the PyWinAuto warning about connecting to a 32-bit executable while using a 64-bit Python environment.
warnings.simplefilter('ignore', category=UserWarning)

def kill_processes_by_path(path: str) -> None:
	""" Kills all processes running an executable at a given path. """

	path = os.path.abspath(path)

	try:
		application = WindowsApplication(backend='win32')
		while True:
			application.connect(path=path, timeout=5)
			application.kill(soft=False)
	except (WindowProcessNotFoundError, WindowTimeoutError):
		pass
	except Exception as error:
		log.error(f'Failed to kill the processes using the path "{path}" with the error: {repr(error)}')

def kill_process_by_pid(pid: int) -> None:
	""" Kills a process given its PID. """

	try:
		application = WindowsApplication(backend='win32')
		application.connect(process=pid, timeout=5)
		application.kill(soft=False)	
	except (WindowProcessNotFoundError, WindowTimeoutError):
		pass
	except Exception as error:
		log.error(f'Failed to kill the process using the PID {pid} with the error: {repr(error)}')

if __name__ == '__main__':
	pass