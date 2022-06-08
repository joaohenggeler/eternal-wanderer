#!/usr/bin/env python3

"""
	A module that defines any general purpose functions used by all scripts, including loading configuration files,
	connecting to the database, and interfacing with Firefox.

	@TODO: Make the compile.py script to join multiple videos into a single one
	@TODO: Add VRML support via OpenVRML
	
	@TODO: Add Mastodon support
	@TODO: Make the stats.py script to print statistics
	
	@TODO: Docs
"""

import json
import locale
import logging
import msvcrt
import os
import re
import shutil
import sqlite3
import tempfile
import time
import warnings
import winreg
from collections import namedtuple
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from glob import iglob
from math import ceil
from subprocess import Popen
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union
from urllib.parse import urlparse
from winreg import CreateKeyEx, DeleteKey, DeleteValue, OpenKey, QueryValueEx, SetValueEx
from xml.etree import ElementTree

import requests
from limits import RateLimitItemPerSecond
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from pywinauto.application import Application as WinApplication, WindowSpecification, ProcessNotFoundError as WinProcessNotFoundError, TimeoutError as WinTimeoutError # type: ignore
from selenium import webdriver # type: ignore
from selenium.common.exceptions import NoSuchElementException, NoSuchWindowException, StaleElementReferenceException, TimeoutException, WebDriverException # type: ignore
from selenium.webdriver.firefox.firefox_binary import FirefoxBinary # type: ignore
from selenium.webdriver.firefox.firefox_profile import FirefoxProfile # type: ignore
from selenium.webdriver.firefox.webdriver import WebDriver # type: ignore
from selenium.webdriver.remote.webelement import WebElement # type: ignore
from selenium.webdriver.support import expected_conditions # type: ignore
from selenium.webdriver.support.ui import WebDriverWait # type: ignore
from waybackpy import WaybackMachineCDXServerAPI as Cdx
from waybackpy.cdx_snapshot import CDXSnapshot

####################################################################################################

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
	show_java_console: bool
	locale: str

	database_path: str
	database_error_wait: int

	gui_webdriver_path: str
	headless_webdriver_path: str
	page_load_timeout: int

	gui_firefox_path: str
	headless_firefox_path: str
	multiprocess_firefox: bool

	profile_path: str
	preferences: Dict[str, Union[bool, int, str]]

	extensions_path: str
	extensions_before_running: Dict[str, bool]
	extensions_after_running: Dict[str, bool]
	user_scripts: Dict[str, bool]

	plugins_path: str
	plugins_mode: str
	plugins: Dict[str, bool]

	compiled_autoit_path: str
	autoit_poll_frequency: int

	recordings_path: str
	max_recordings_per_directory: int

	cdx_api_rate_limit_amount: int
	cdx_api_rate_limit_multiple: int
	wayback_machine_rate_limit_amount: int
	wayback_machine_rate_limit_multiple: int
	rate_limit_poll_frequency: float
	unavailable_wayback_machine_wait: int

	# Determined at runtime.
	cdx_api_memory_storage: MemoryStorage
	cdx_api_rate_limiter: MovingWindowRateLimiter
	cdx_api_requests_per_second: RateLimitItemPerSecond
	wayback_machine_memory_storage: MemoryStorage
	wayback_machine_rate_limiter: MovingWindowRateLimiter
	wayback_machine_requests_per_minute: RateLimitItemPerSecond

	def __init__(self):

		with open('config.json') as file:
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
		self.compiled_autoit_path = os.path.abspath(self.compiled_autoit_path)
		self.recordings_path = os.path.abspath(self.recordings_path)

		self.extensions_before_running = container_to_lowercase(self.extensions_before_running)
		self.extensions_after_running = container_to_lowercase(self.extensions_after_running)
		self.user_scripts = container_to_lowercase(self.user_scripts)
		self.plugins = container_to_lowercase(self.plugins)
		assert self.plugins_mode in ['static', 'dynamic'], f'Unknown plugins mode "{self.plugins_mode}".'

		self.cdx_api_memory_storage = MemoryStorage()
		self.cdx_api_rate_limiter = MovingWindowRateLimiter(self.cdx_api_memory_storage)
		self.cdx_api_requests_per_second = RateLimitItemPerSecond(self.cdx_api_rate_limit_amount, self.cdx_api_rate_limit_multiple)

		self.wayback_machine_memory_storage = MemoryStorage()
		self.wayback_machine_rate_limiter = MovingWindowRateLimiter(self.wayback_machine_memory_storage)
		self.wayback_machine_requests_per_minute = RateLimitItemPerSecond(self.wayback_machine_rate_limit_amount, self.wayback_machine_rate_limit_multiple)

	def load_subconfig(self, name: str) -> None:
		""" Loads a specific JSON object from the configuration file. """
		self.__dict__.update(self.json_config[name])

	def get_recording_subdirectory_path(self, id: int) -> str:
		""" Retrieves the absolute path of a snapshot recording given its ID. """
		bucket = ceil(id / self.max_recordings_per_directory) * self.max_recordings_per_directory
		return os.path.join(self.recordings_path, str(bucket))

	def wait_for_cdx_api_rate_limit(self) -> None:
		""" Waits for a given amount of time if the user-specified CDX API rate limit has been reached. Otherwise, returns immediately. Thread-safe. """
		while not self.cdx_api_rate_limiter.hit(self.cdx_api_requests_per_second):
			time.sleep(self.rate_limit_poll_frequency)

	def wait_for_wayback_machine_rate_limit(self) -> None:
		""" Waits for a given amount of time if the user-specified Wayback Machine rate limit has been reached. Otherwise, returns immediately. Thread-safe. """
		while not self.wayback_machine_rate_limiter.hit(self.wayback_machine_requests_per_minute):
			time.sleep(self.rate_limit_poll_frequency)

config = CommonConfig()
locale.setlocale(locale.LC_ALL, config.locale)

log = logging.getLogger('eternal wanderer')
log.setLevel(logging.DEBUG if config.debug else logging.INFO)
log.debug('Running in debug mode.')

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

####################################################################################################

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
	
		# E.g. https://web.archive.org/web/20010203164200if_/http://www.tripod.lycos.com:80/service/welcome/preferences
		# And https://web.archive.org/web/20010203180900if_/http://www.tripod.lycos.com:80/bin/membership/login

		# Some examples of the Url, Timestamp, UrlKey, and Digest columns from the CDX API:
		# http://www.geocities.com/Heartland/Plains/1036/africa.gif 20090730213441 com,geocities)/heartland/plains/1036/africa.gif RRCC3TTUVIQTMFN6BDRRIXR7OBNCGS6X
		# http://geocities.com/Heartland/Plains/1036/africa.gif 	20090820053240 com,geocities)/heartland/plains/1036/africa.gif RRCC3TTUVIQTMFN6BDRRIXR7OBNCGS6X
		# http://geocities.com/Heartland/Plains/1036/africa.gif 	20091026145159 com,geocities)/heartland/plains/1036/africa.gif RRCC3TTUVIQTMFN6BDRRIXR7OBNCGS6X	

		self.connection.execute(f'''
								CREATE TABLE IF NOT EXISTS Snapshot
								(
									Id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
									ParentId INTEGER,
									State INTEGER NOT NULL,
									Depth INTEGER NOT NULL,
									Priority INTEGER NOT NULL DEFAULT {Snapshot.NO_PRIORITY},
									Title TEXT,
									UsesPlugins BOOLEAN,
									IsStandaloneMedia BOOLEAN NOT NULL,
									Url TEXT NOT NULL,
									Timestamp VARCHAR(14) NOT NULL,
									LastModifiedTime VARCHAR(14),
									IsExcluded BOOLEAN NOT NULL,
									UrlKey TEXT,
									Digest VARCHAR(64),

									UNIQUE (Url, Timestamp)

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

		self.connection.execute(f'''
								CREATE VIEW IF NOT EXISTS SnapshotInfo AS
								SELECT
									S.Id AS Id,
									(
										CASE WHEN S.State = {Snapshot.QUEUED} THEN NULL
											 ELSE IFNULL(CASE WHEN S.IsStandaloneMedia THEN (SELECT CAST(Value AS INTEGER) FROM Config WHERE Name = 'standalone_media_points')
															  WHEN W.IsTag THEN SUM(SW.Count * W.Points)
															  ELSE SUM(MIN(SW.Count, 1) * W.Points)
														 END, 0)
										END
									) AS Points,
									(
										CASE WHEN S.State = {Snapshot.QUEUED} THEN NULL
										ELSE IFNULL(MAX(W.IsSensitive), FALSE)
										END
									) AS IsSensitive
								FROM Snapshot S
								LEFT JOIN SnapshotWord SW ON S.Id = SW.SnapshotId
								LEFT JOIN Word W ON SW.WordId = W.Id
								GROUP BY S.Id;
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS Recording
								(
									Id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
									SnapshotId INTEGER NOT NULL,
									IsProcessed BOOLEAN NOT NULL,
									ArchiveFilename TEXT,
									UploadFilename TEXT NOT NULL,
									CreationTime TIMESTAMP NOT NULL,
									PublishTime TIMESTAMP,
									MediaId INTEGER,
									TweetId INTEGER,

									FOREIGN KEY (SnapshotId) REFERENCES Snapshot (Id)
								);
								''')

		self.connection.commit()

	def disconnect(self) -> None:
		""" Commits any unsaved changes and disconnects from the database. """

		try:
			self.connection.commit()
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
	State: int
	Depth: int
	Priority: int
	Title: Optional[str]
	UsesPlugins: Optional[bool]
	IsStandaloneMedia: bool
	Url: str
	Timestamp: str
	LastModifiedTime: Optional[str]
	IsExcluded: bool
	UrlKey: Optional[str]
	Digest: Optional[str]

	# Determined dynamically if joined with the SnapshotInfo view.
	Points: Optional[int]
	IsSensitive: Optional[bool]

	# Determined at runtime.
	WaybackUrl: str
	OldestTimestamp: str

	# Constants. Each of these must be greater than the last.
	QUEUED = 0
	SCOUTED = 1
	ABORTED = 2
	RECORDED = 3
	APPROVED = 4
	REJECTED = 5
	PUBLISHED = 6
	ARCHIVED = 7

	NO_PRIORITY = 0
	SCOUT_PRIORITY = 1
	RECORD_PRIORITY = 2
	PUBLISH_PRIORITY = 3

	IFRAME_MODIFIER = 'if_'
	OBJECT_EMBED_MODIFIER = 'oe_'
	IDENTICAL_MODIFIER = 'id_'

	def __init__(self, **kwargs):
		
		self.Points = None
		self.IsSensitive = None
		self.__dict__.update(kwargs)
		
		def bool_or_none(value: Any) -> Union[bool, None]:
			return bool(value) if value is not None else None

		self.UsesPlugins = bool_or_none(self.UsesPlugins)
		self.IsSensitive = bool_or_none(self.IsSensitive)
		self.IsStandaloneMedia = bool_or_none(self.IsStandaloneMedia)
		self.IsExcluded = bool_or_none(self.IsExcluded)

		modifier = Snapshot.OBJECT_EMBED_MODIFIER if self.IsStandaloneMedia else Snapshot.IFRAME_MODIFIER
		self.WaybackUrl = compose_wayback_machine_snapshot_url(timestamp=self.Timestamp, modifier=modifier, url=self.Url)

		if self.LastModifiedTime is not None:
			self.OldestTimestamp = min(self.Timestamp, self.LastModifiedTime)
		else:
			self.OldestTimestamp = self.Timestamp

	def __str__(self):
		return f'({self.Url}, {self.Timestamp})'

class Recording():
	""" A video recording of a Wayback Machine snapshot. """

	# From the database.
	Id: int
	SnapshotId: int
	IsProcessed: bool
	ArchiveFilename: Optional[str]
	UploadFilename: str
	CreationTime: str
	PublishTime: Optional[str]
	MediaId: Optional[int]
	MediaUrl: Optional[str]
	TweetId: Optional[int]

	# Determined at runtime.
	ArchiveFilePath: Optional[str]
	UploadFilePath: str

	def __init__(self, **kwargs):
		
		self.__dict__.update(kwargs)
		
		subdirectory_path = config.get_recording_subdirectory_path(self.Id)
		self.ArchiveFilePath = os.path.join(subdirectory_path, self.ArchiveFilename) if self.ArchiveFilename is not None else None
		self.UploadFilePath = os.path.join(subdirectory_path, self.UploadFilename)

class CustomFirefoxProfile(FirefoxProfile):
	""" A custom Firefox profile used to bypass the frozen Mozilla preferences dictionary defined by Selenium. """

	def __init__(self, profile_directory: Optional[str] = None, user_script_filter: Optional[List[str]] = None):
		
		if not FirefoxProfile.DEFAULT_PREFERENCES:
			FirefoxProfile.DEFAULT_PREFERENCES = {}
			FirefoxProfile.DEFAULT_PREFERENCES['frozen'] = {}
			FirefoxProfile.DEFAULT_PREFERENCES['mutable'] = config.preferences

		super().__init__(profile_directory)

		if config.plugins_mode == 'dynamic':
			plugin_reg_path = os.path.join(self.profile_dir, 'pluginreg.dat')
			delete_file(plugin_reg_path)

		scripts_path = os.path.join(self.profile_dir, 'gm_scripts')
		scripts_config_path = os.path.join(scripts_path, 'config.xml')

		try:
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

class Browser():
	""" A Firefox browser instance created by Selenium. """

	headless: bool
	extra_preferences: Optional[Dict[str, Union[bool, int, str]]]
	use_extensions: bool
	extension_filter: Optional[List[str]]
	user_script_filter: Optional[List[str]]
	use_plugins: bool
	use_autoit: bool

	firefox_path: str
	firefox_directory_path: str
	webdriver_path: str
	autoit_processes: List[Popen]
	registry: 'TemporaryRegistry'
	java_deployment_path: str

	driver: WebDriver
	profile_path: str
	pid: int
	application: Optional[WinApplication]
	window: Optional[WindowSpecification]
	
	def __init__(self, 	headless: bool = False,
						extra_preferences: Optional[Dict[str, Union[bool, int, str]]] = None,
						use_extensions: bool = False,
						extension_filter: Optional[List[str]] = None,
						user_script_filter: Optional[List[str]] = None,
						use_plugins: bool = False,
						use_autoit: bool = False):

		self.headless = headless
		self.extra_preferences = extra_preferences
		self.use_extensions = use_extensions
		self.extension_filter = container_to_lowercase(extension_filter) if extension_filter else extension_filter # type: ignore
		self.user_script_filter = container_to_lowercase(user_script_filter) if user_script_filter else user_script_filter # type: ignore
		self.use_plugins = use_plugins
		self.use_autoit = use_autoit

		self.firefox_path = config.headless_firefox_path if self.headless else config.gui_firefox_path
		self.firefox_directory_path = os.path.dirname(self.firefox_path)
		self.webdriver_path = config.headless_webdriver_path if self.headless else config.gui_webdriver_path
		self.autoit_processes = []
		self.registry = TemporaryRegistry()
		self.java_deployment_path = os.path.join(os.environ['USERPROFILE'], 'AppData', 'LocalLow', 'Sun', 'Java', 'Deployment')

		log.info('Configuring Firefox.')

		if config.profile_path is not None:
			log.info(f'Using the custom Firefox profile at "{config.profile_path}".')
		else:
			log.info(f'Using a temporary Firefox profile.')

		profile = CustomFirefoxProfile(config.profile_path, self.user_script_filter)
		
		for key, value in config.preferences.items():
			profile.set_preference(key, value)

		if self.extra_preferences is not None:
			log.info(f'Setting additional preferences: {self.extra_preferences}')
			for key, value in self.extra_preferences.items():
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

			plugin_extender_source_path = os.path.join(config.plugins_path, 'BrowserPluginExtender.dll')
			shutil.copy(plugin_extender_source_path, self.firefox_directory_path)

			# The value we're changing here is the default one that is usually displayed as "(Default)",
			# even though the subkey is actually an empty string.
			self.registry.set('HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 11\\allowfallback\\', 'y')
			self.registry.set('HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 12\\allowfallback\\', 'y')

			self.configure_java_plugin()
		else:
			os.environ['MOZ_PLUGIN_PATH'] = ''

		if self.use_extensions:

			log.info(f'Installing the extensions in "{config.extensions_path}".')

			for extension, enabled in config.extensions_before_running.items():
				
				filtered = self.extension_filter is not None and extension not in self.extension_filter

				if enabled and not filtered:
					log.info(f'Installing the extension "{extension}".')
					full_extension_path = os.path.join(config.extensions_path, extension)
					profile.add_extension(full_extension_path)
				else:
					log.info(f'Skipping the extension "{extension}" at the user\'s request.')

		options = webdriver.FirefoxOptions()
		options.binary = FirefoxBinary(self.firefox_path)
		options.profile = profile
		options.headless = self.headless

		if config.multiprocess_firefox:
			os.environ.pop('MOZ_FORCE_DISABLE_E10S', None)
		else:
			log.info('Disabling multiprocess Firefox.')
			os.environ['MOZ_FORCE_DISABLE_E10S'] = '1'
		
		# Disable DPI scaling to fix potential display issues in Firefox.
		os.environ['__COMPAT_LAYER'] = 'GDIDPISCALING DPIUNAWARE'

		log.info(f'Creating the Firefox WebDriver using the Firefox executable at "{self.firefox_path}" and the WebDriver at "{self.webdriver_path}".')
		self.driver = webdriver.Firefox(executable_path=self.webdriver_path, options=options, service_log_path=None)
		self.driver.set_page_load_timeout(config.page_load_timeout)
		self.driver.maximize_window()

		self.profile_path = self.driver.capabilities['moz:profile']
		self.pid = self.driver.capabilities['moz:processID']

		self.application = None
		self.window = None

		if not self.headless:
			try:
				log.info(f'Connecting to the Firefox executable with the PID {self.pid}.')
				self.application = WinApplication(backend='win32')
				self.application.connect(process=self.pid, timeout=30)
				self.window = self.application.top_window()
			except (WinProcessNotFoundError, WinTimeoutError, RuntimeError):
				log.error('Failed to connect to the Firefox executable.')
			
		if self.use_extensions:

			for extension, enabled in config.extensions_after_running.items():
				
				filtered = self.extension_filter is not None and extension not in self.extension_filter

				if enabled and not filtered:
					log.info(f'Installing the extension "{extension}".')
					full_extension_path = os.path.join(config.extensions_path, extension)
					self.driver.install_addon(full_extension_path)
				else:
					log.info(f'Skipping the extension "{extension}" at the user\'s request.')

		self.driver.get('about:blank')

		if self.use_autoit:
			
			log.info(f'Running the compiled AutoIt scripts in "{config.compiled_autoit_path}" with a poll frequency of {config.autoit_poll_frequency} milliseconds.')
			
			compiled_autoit_search_path = os.path.join(config.compiled_autoit_path, '*.exe')
			for path in iglob(compiled_autoit_search_path):
				try:
					# If we enable the AutoIt scripts twice, this will kill any existing ones.
					# This is fine in practice since we only do this for the recorder script and
					# since we only want one of each running at the same time anyways.
					kill_processes_by_path(path)
					process = Popen([path, str(config.autoit_poll_frequency)])
					self.autoit_processes.append(process)
				except OSError as error:
					log.error(f'Failed to run the compiled AutoIt script "{path}" with the error: {repr(error)}')

	def configure_java_plugin(self) -> None:
		""" Configures the Java Plugin by generating the appropriate deployment files and passing any useful paramters to the JRE. """

		java_plugin_search_path = os.path.join(config.plugins_path, '**', 'jre*', 'bin', 'plugin2')
		java_plugin_path = next(iglob(java_plugin_search_path, recursive=True), None)
		if java_plugin_path is None:
			log.error('Could not find the path to the Java Runtime Environment. The Java Plugin was not be set up correctly.')
			return

		java_jre_path = os.path.dirname(os.path.dirname(java_plugin_path))
		log.info(f'Configuring the Java Plugin using the runtime environment located in "{java_jre_path}".')

		java_lib_path = os.path.join(java_jre_path, 'lib')
		java_bin_path = os.path.join(java_jre_path, 'bin')

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
		java_product = re.findall(r'(?:jdk|jre)((?:\d+\.\d+\.\d+)(?:_\d+)?)', java_jre_path, flags=re.IGNORECASE)[-1]
		java_platform, _ = java_product.rsplit('.', 1) # E.g. "1.8"
		_, java_version = java_platform.split('.', 1) # E.g. "8"

		java_firefox_path = os.path.join(java_bin_path, 'javaws.exe')

		java_exception_sites_path = os.path.join(java_lib_path, 'exception.sites')
		java_exception_sites_template_path = os.path.join(config.plugins_path, 'Java', 'exception.sites.template')
		shutil.copy(java_exception_sites_template_path, java_exception_sites_path)

		def escape_java_deployment_properties_path(path: str) -> str:
			return path.replace('\\', '\\\\').replace(':', '\\:').replace(' ', '\\u0020')

		content = content.replace('{comment}', f'Generated by "{__file__}" on {get_current_timestamp()}.')
		content = content.replace('{jre_platform}', java_platform)
		content = content.replace('{jre_product}', java_product)
		content = content.replace('{jre_path}', escape_java_deployment_properties_path(java_firefox_path))
		content = content.replace('{jre_version}', java_version)
		content = content.replace('{security_level}', 'LOW' if java_product <= '1.7.0_17' else 'MEDIUM')
		content = content.replace('{exception_sites_path}', escape_java_deployment_properties_path(java_exception_sites_path))
		content = content.replace('{console_startup}', 'SHOW' if config.show_java_console else 'NEVER')

		with open(java_properties_path, 'w', encoding='utf-8') as file:
			file.write(content)

		java_policy_path = os.path.join(java_lib_path, 'security', 'java.policy')
		java_policy_template_path = os.path.join(config.plugins_path, 'Java', 'java.policy.template')
		shutil.copy(java_policy_template_path, java_policy_path)

		# Disable Java bytecode verification to run older applets correctly.
		# Originally, we wanted to pass the character encoding and locale Java arguments on a page-by-page basis.
		# This would allow Japanese applets to display their content correctly. Although the code to do this still
		# exists in the "Improve Java Applets" Greasemonkey user script, it has since been commented out. This is
		# because, in practice, the applets wouldn't change their encoding or locale even when the "java_arguments"
		# and "java-vm-args" parameters were set. We'll just set them globally since that seems to work out, though
		# it means that we only support Latin and Japanese text. Note that changing this to a different language may
		# require you to add the localized security prompt's title to the "close_java_popups" AutoIt script.
		os.environ['deployment.expiration.check.enabled'] = 'false'
		os.environ['JAVA_TOOL_OPTIONS'] = '-Xverify:none -Dfile.encoding=UTF8 -Duser.language=ja -Duser.country=JP'
		os.environ['_JAVA_OPTIONS'] = ''

		self.delete_user_level_java_properties()
		self.delete_java_plugin_cache()

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
		
		# Delete the temporary directories from previous executions. Remeber that there's a bug
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

	def go_to_wayback_url(self, wayback_url: str, allow_redirects: bool = False) -> None:
		""" Navigates to a Wayback Machine URL, taking into account any rate limiting and retrying if the service is unavailable. """

		try:
			config.wait_for_wayback_machine_rate_limit()
			self.driver.get(wayback_url)

			while not self.is_current_url_valid_wayback_machine_page(allow_redirects=allow_redirects):
				log.warning(f'Waiting {config.unavailable_wayback_machine_wait} seconds for the Wayback Machine to become available again.')
				time.sleep(config.unavailable_wayback_machine_wait)
				self.driver.get(wayback_url)

		except TimeoutException:
			log.warning(f'Timed out while loading the page "{wayback_url}".')

	def switch_through_frames(self) -> Iterator[str]:
		""" Traverses recursively through the current web page's frames. This function yields the frame's source URL.
		Note that, for pages archived by the Wayback Machine, only the root window's URL will be a snapshot URL."""

		def traverse_frames(current_url: str) -> Iterator[str]:
			""" Helper function used to traverse through every frame recursively. """

			log.debug(f'Traversing the frame "{current_url}".')
			yield current_url
			
			frame_list = self.driver.find_elements_by_tag_name('frame') + self.driver.find_elements_by_tag_name('iframe')
			for frame in frame_list:
				
				frame_source = frame.get_attribute('src')

				# Skip frames whose source would be converted to "https://web.archive.org/".
				if not frame_source:
					log.debug('Skipping a frame without a source.')
					continue

				parts = urlparse(frame_source)

				# Skip frames without valid URLs.
				if not parts.netloc:
					log.debug(f'Skipping the invalid frame "{frame_source}".')
					continue

				# Skip any frames that were added by the Internet Archive (e.g. https://archive.org/includes/donate.php).
				if parts.hostname is not None and (parts.hostname == 'archive.org' or parts.hostname.endswith('.archive.org')):
					log.debug(f'Skipping the Internet Archive frame "{frame_source}".')
					continue

				try:
					condition = expected_conditions.frame_to_be_available_and_switch_to_it(frame)
					WebDriverWait(self.driver, 10).until(condition)
				except TimeoutException:
					continue
				
				yield from traverse_frames(frame_source)
				self.driver.switch_to.parent_frame()

		try:
			yield from traverse_frames(self.driver.current_url)
		except WebDriverException as error:
			log.error(f'Failed to traverse the frames with the error: {repr(error)}')

		self.driver.switch_to.default_content()
	
	def reload_plugin_media(self) -> None:
		""" Reloads any content embedded using the object, embed, or applet tags in the current web page and its frames. """

		try:
			for _ in self.switch_through_frames():
				self.driver.execute_script(	'''
											const SOURCE_ATTRIBUTES = ["data", "src", "target", "mrl", "filename", "code", "object"];
											
											const object_tags = Array.from(document.getElementsByTagName("object"));
											const embed_tags = Array.from(document.getElementsByTagName("embed"));
											const applet_tags = Array.from(document.getElementsByTagName("applet"));

											for(const element of object_tags.concat(embed_tags).concat(applet_tags))
											{
												for(const source_attribute of SOURCE_ATTRIBUTES)
												{
													if(element.hasAttribute(source_attribute)) element[source_attribute] += "";
												}											
											}
											''')
		except WebDriverException as error:
			log.error(f'Failed to reload all plugin media with the error: {repr(error)}')

	def toggle_fullscreen(self) -> None:
		""" Toggles fullscreen Firefox. Does nothing if Firefox is running in headless mode. """
		if self.window is not None:
			# Using Selenium's ActionChains didn't seem to work.
			self.window.send_keystrokes('{F11}')
		
	def bring_to_front(self) -> None:
		""" Focuses and brings the Firefox window to front. Does nothing if Firefox is running in headless mode. """
		if self.window is not None:
			self.window.set_focus()

	def close_all_windows_except(self, window_handle) -> None:
		""" Closes every Firefox tab or window except a specific one. """

		try:			
			for handle in self.driver.window_handles:
				if handle != window_handle:
					self.driver.switch_to.window(handle)
					self.driver.close()

			self.driver.switch_to.window(window_handle)

			try:
				condition = expected_conditions.number_of_windows_to_be(1)
				WebDriverWait(self.driver, 5).until(condition)
			except TimeoutException:
				log.warning(f'Timed out while waiting for the other WebDriver windows to close.')

		except NoSuchWindowException:
			pass

	def is_current_url_valid_wayback_machine_page(self, allow_redirects: bool = False) -> bool:
		""" Checks if the current web page points to a Wayback Machine snapshot. """

		if self.driver.current_url.lower().startswith('file:'):
			return True

		try:
			# Check for a specific Wayback Machine script.
			self.driver.find_element_by_xpath(r'//script[contains(@src, "/_static/js/wombat.js")]')
			result = True
		except NoSuchElementException:
			# Check if the page exists by sending a request to the Wayback Machine.
			# Used for uncommon cases where the previous script isn't embedded in the page.
			# E.g. https://web.archive.org/web/19961220114110if_/http://store.geocities.com:80/
			result = is_url_available(self.driver.current_url, allow_redirects=allow_redirects)
			
		return result

class TemporaryRegistry():
	""" A temporary registry that remembers and undos any changes (key additions and deletions) made to the Windows registry. """

	original_state: Dict[Tuple[int, str, str], Tuple[Optional[int], Any]]
	keys_to_delete: Set[Tuple[int, str, str]]
	key_paths_to_delete: Dict[Tuple[int, str], bool]

	OPEN_HKEYS = {
		'hkey_classes_root': winreg.HKEY_CLASSES_ROOT,
		'hkey_current_user': winreg.HKEY_CURRENT_USER,
		'hkey_local_machine': winreg.HKEY_LOCAL_MACHINE,
		'hkey_users': winreg.HKEY_USERS,
		'hkey_performance_data': winreg.HKEY_PERFORMANCE_DATA,
		'hkey_current_config': winreg.HKEY_CURRENT_CONFIG,
		'hkey_dyn_data': winreg.HKEY_DYN_DATA,
	}

	VALUE_TYPES = {
		'reg_binary': winreg.REG_BINARY,
		'reg_dword': winreg.REG_DWORD,
		'reg_dword_little_endian': winreg.REG_DWORD_LITTLE_ENDIAN,
		'reg_dword_big_endian': winreg.REG_DWORD_BIG_ENDIAN,
		'reg_expand_sz': winreg.REG_EXPAND_SZ,
		'reg_link': winreg.REG_LINK,
		'reg_multi_sz': winreg.REG_MULTI_SZ,
		'reg_none': winreg.REG_NONE,
		'reg_qword': winreg.REG_QWORD,
		'reg_qword_little_endian': winreg.REG_QWORD_LITTLE_ENDIAN,
		'reg_resource_list': winreg.REG_RESOURCE_LIST,
		'reg_full_resource_descriptor': winreg.REG_FULL_RESOURCE_DESCRIPTOR,
		'reg_resource_requirements_list': winreg.REG_RESOURCE_REQUIREMENTS_LIST,
		'reg_sz': winreg.REG_SZ,
	}

	def __init__(self):
		self.original_state = {}
		self.keys_to_delete = set()
		self.key_paths_to_delete = {}

	@staticmethod
	def partition_key(key: str) -> Tuple[int, str, str]:
		""" Separates a registry key string into its hkey, key path, and sub key components. """

		first_key, key_path = key.split('\\', 1)
		key_path, sub_key = key_path.rsplit('\\', 1)

		first_key = first_key.lower()
		if first_key not in TemporaryRegistry.OPEN_HKEYS:
			raise KeyError(f'The registry key "{key}" does not start with a valid HKEY.')

		hkey = TemporaryRegistry.OPEN_HKEYS[first_key]
		return (hkey, key_path, sub_key)

	def get(self, key: str) -> Any:
		""" Gets the value of a registry key. Returns None if the key doesn't exist. """

		try:
			hkey, key_path, sub_key = TemporaryRegistry.partition_key(key)
			with OpenKey(hkey, key_path, access=winreg.KEY_READ) as key_handle:
				value, _ = QueryValueEx(key_handle, sub_key)
		except OSError:
			value = None

		return value

	def set(self, key: str, value: Union[int, str], type: Optional[str] = None) -> Any:
		""" Sets the value of a registry key. Any missing intermediate keys are automatically created. """

		hkey, key_path, sub_key = TemporaryRegistry.partition_key(key)
		
		value_type: Optional[int]
		if type is None:
			if isinstance(value, int):
				value_type = winreg.REG_DWORD
			elif isinstance(value, str):
				value_type = winreg.REG_SZ
			else:
				raise ValueError(f'The type of the value "{value}" could not be autodetected for the registry key "{key}".')	
		else:
			value_type = TemporaryRegistry.VALUE_TYPES.get(type.lower())
			if value_type is None:
				raise ValueError(f'Unknown value type "{type}" for the registry key "{key}".')
	
		if (hkey, key_path) not in self.key_paths_to_delete:

			self.key_paths_to_delete[(hkey, key_path)] = True

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

		original_state_key = (hkey, key_path, sub_key)
		original_state_value: Tuple[Optional[int], Any]

		with CreateKeyEx(hkey, key_path, access=winreg.KEY_ALL_ACCESS) as key_handle:
			try:
				original_value, original_type = QueryValueEx(key_handle, sub_key)
				original_state_value = (original_type, original_value)
				result = original_value
			except OSError:
				original_state_value = (None, None)
				result = None

			SetValueEx(key_handle, sub_key, 0, value_type, value)

		if original_state_key not in self.original_state:
			self.original_state[original_state_key] = original_state_value

		return result

	def delete(self, key: str) -> bool:
		""" Removes a registry key. Returns true if it existed, otherwise false. """

		hkey, key_path, sub_key = TemporaryRegistry.partition_key(key)

		try:
			with OpenKey(hkey, key_path, access=winreg.KEY_ALL_ACCESS) as key_handle:
				original_value, original_type = QueryValueEx(key_handle, sub_key)
				DeleteValue(key_handle, sub_key)

			original_state_key = (hkey, key_path, sub_key)
			original_state_value = (original_type, original_value)

			if original_state_key not in self.original_state:
				self.original_state[original_state_key] = original_state_value

			success = True
		except OSError:
			success = False

		return success

	def restore(self) -> None:
		""" Restores the Windows registry to its original state by undoing any changes, additions, and deletions. """

		for (hkey, key_path, sub_key), (type, value) in self.original_state.items():
			try:
				with OpenKey(hkey, key_path, access=winreg.KEY_WRITE) as key_handle:
					if type is None:
						DeleteValue(key_handle, sub_key)
					else:
						SetValueEx(key_handle, sub_key, 0, type, value)
			except OSError as error:
				log.error(f'Failed to restore the original value "{value}" type {type} of the registry key "{hkey}\\{key_path}\\{sub_key}" with the error: {repr(error)}')

		keys_to_delete = sorted(self.keys_to_delete, key=lambda x: len(x[1]), reverse=True)
		for (hkey, key_path, sub_key) in keys_to_delete:
			try:
				with OpenKey(hkey, key_path, access=winreg.KEY_WRITE) as key_handle:
					DeleteKey(key_handle, sub_key)
			except OSError as error:
				log.error(f'Failed to delete the registry key "{hkey}\\{key_path}\\{sub_key}" with the error: {repr(error)}')

		self.original_state = {}
		self.keys_to_delete = set()
		self.key_paths_to_delete = {}

	def __enter__(self):
		return self

	def __exit__(self, exception_type, exception_value, traceback):
		self.restore()

####################################################################################################

def get_current_timestamp() -> str:
	""" Retrieves the current timestamp in UTC. """
	return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

def find_best_wayback_machine_snapshot(timestamp: str, url: str, standalone_media: bool) -> CDXSnapshot:
	""" Finds the best Wayback Machine snapshot given its timestamp and URL. By best snapshot we mean
	locating the nearest one and then finding the oldest capture where the content is identical. """

	mime_type_filter = r'!mimetype:text/.*' if standalone_media else r'mimetype:text/html'

	config.wait_for_cdx_api_rate_limit()
	cdx = Cdx(url=url, filters=['statuscode:200', mime_type_filter])
	snapshot = cdx.near(wayback_machine_timestamp=timestamp)

	cdx.filters.append(f'digest:{snapshot.digest}')
	snapshot = cdx.oldest()

	return snapshot

def find_wayback_machine_snapshot_last_modified_time(wayback_url: str) -> Optional[str]:
	""" Finds the last modified time of a Wayback Machine snapshot. Note that not every snapshot has this information. """

	result = None

	try:
		response = requests.head(wayback_url)
		response.raise_for_status()
		
		last_modified = response.headers.get('x-archive-orig-last-modified')
		if last_modified is not None:
			last_modified_datetime = parsedate_to_datetime(last_modified)
			result = last_modified_datetime.strftime('%Y%m%d%H%M%S')

	except (requests.RequestException, ValueError):
		pass

	return result

WaybackParts = namedtuple('WaybackParts', ['Timestamp', 'Modifier', 'Url'])
WAYBACK_MACHINE_SNAPSHOT_URL_REGEX = re.compile(r'https?://web\.archive\.org/web/(?P<timestamp>\d+)(?P<modifier>[a-z]+_)?/(?P<url>.+)', re.IGNORECASE)

def parse_wayback_machine_snapshot_url(url: str) -> Optional[WaybackParts]:
	""" Divides the URL to a Wayback Machine snapshot into its basic components. """
	
	result = None

	match = WAYBACK_MACHINE_SNAPSHOT_URL_REGEX.fullmatch(url)
	if match is not None:
		
		timestamp = match.group('timestamp')
		modifier = match.group('modifier')
		url = match.group('url')
		result = WaybackParts(timestamp, modifier, url)

	return result

def compose_wayback_machine_snapshot_url(	timestamp: Optional[str] = None, modifier: Optional[str] = None, url: Optional[str] = None,
											parts: Optional[WaybackParts] = None) -> str:
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
		response.raise_for_status()
		result = True
	except requests.RequestException:
		result = False

	return result

def is_wayback_machine_available() -> bool:
	""" Checks if the Wayback Machine website is available. """
	return is_url_available('https://web.archive.org/', allow_redirects=True)

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

		if 'exit' in command or 'quit' in command or 'stop' in command:
			result = True

	return result

def delete_file(path: str) -> None:
	""" Deletes a file. Does nothing if it doesn't exist. """
	try:
		os.remove(path)
	except OSError:
		pass

def delete_directory(path: str) -> None:
	""" Deletes a directory and all of its subdirectories. Does nothing if it doesn't exist. """
	try:
		shutil.rmtree(path)
	except OSError:
		pass

# Ignore the PyWinAuto warning about connecting to a 32-bit executable while using a 64-bit Python environment.
warnings.simplefilter('ignore', category=UserWarning)

def kill_processes_by_path(path: str, timeout: int = 5) -> None:
	""" Kills all processes running an executable at a given path. """

	path = os.path.abspath(path)

	try:
		application = WinApplication(backend='win32')
		while True:
			application.connect(path=path, timeout=timeout)
			application.kill(soft=False)
	except (WinProcessNotFoundError, WinTimeoutError):
		pass
	except Exception as error:
		log.error(f'Failed to kill the processes using the path "{path}" with the error: {repr(error)}')

def kill_process_by_pid(pid: int, timeout: int = 5) -> None:
	""" Kills a process given its PID. """

	try:
		application = WinApplication(backend='win32')
		application.connect(process=pid, timeout=timeout)
		application.kill(soft=False)	
	except (WinProcessNotFoundError, WinTimeoutError):
		pass
	except Exception as error:
		log.error(f'Failed to kill the process using the PID {pid} with the error: {repr(error)}')

if __name__ == '__main__':
	pass