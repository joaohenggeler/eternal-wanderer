#!/usr/bin/env python3

import ctypes
import queue
import random
import sqlite3
from argparse import ArgumentParser
from base64 import b64encode
from collections import Counter
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from sys import exit
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
from typing import Optional, Union
from urllib.parse import urljoin, urlparse, urlunparse

import pywinauto # type: ignore
from apscheduler.schedulers import SchedulerNotRunningError # type: ignore
from apscheduler.schedulers.blocking import BlockingScheduler # type: ignore
from requests import RequestException
from selenium.common.exceptions import (
	SessionNotCreatedException, WebDriverException,
) # type: ignore
from waybackpy import WaybackMachineSaveAPI
from waybackpy.exceptions import TooManyRequestsError

from common.browser import Browser
from common.config import CommonConfig
from common.database import Database
from common.ffmpeg import (
	ffmpeg, ffmpeg_detect_audio,
	FfmpegException, ffprobe_duration,
	ffprobe_has_video_stream, ffprobe_info,
	ffprobe_is_audio_only,
)
from common.fluidsynth import fluidsynth, FluidSynthException
from common.logger import setup_logger
from common.net import (
	extract_media_extension_from_url,
	global_session, is_url_available,
)
from common.plugin_crash_timer import PluginCrashTimer
from common.plugin_input_repeater import CosmoPlayerViewpointCycler, PluginInputRepeater
from common.proxy import Proxy
from common.rate_limiter import global_rate_limiter
from common.screen_capture import ScreenCapture
from common.snapshot import Snapshot
from common.temporary_registry import TemporaryRegistry
from common.util import (
	clamp, container_to_lowercase,
	delete_file, was_exit_command_entered,
)
from common.wayback import parse_wayback_machine_snapshot_url

@dataclass
class RecordConfig(CommonConfig):
	""" The configuration that applies to the recorder script. """

	# From the config file.
	scheduler: dict[str, Union[int, str]]
	num_snapshots_per_scheduled_batch: int

	ranking_offset: Optional[int]
	min_year: Optional[int]
	max_year: Optional[int]
	record_sensitive_snapshots: bool
	min_recordings_for_same_host: Optional[int]
	min_publish_days_for_same_url: Optional[int]

	allowed_media_extensions: frozenset[str] # Different from the config data type.
	multi_asset_media_extensions: frozenset[str] # Different from the config data type.

	enable_proxy: bool
	proxy_port: Optional[int]
	proxy_queue_timeout: int
	proxy_total_timeout: int
	proxy_block_requests_outside_internet_archive: bool
	proxy_convert_realmedia_metadata_snapshots: bool
	proxy_find_missing_snapshots_using_cdx: bool
	proxy_max_cdx_path_components: Optional[int]
	proxy_save_missing_snapshots_that_still_exist_online: bool
	proxy_max_consecutive_save_tries: int
	proxy_max_total_save_tries: int
	proxy_cache_missing_responses: bool

	check_availability: bool
	hide_scrollbars: bool

	page_cache_wait: int
	media_cache_wait: int

	plugin_load_wait: int
	base_plugin_crash_timeout: int

	viewport_scroll_percentage: float
	base_wait_after_load: int
	wait_after_load_per_plugin_instance: int
	base_wait_per_scroll: int
	wait_after_scroll_per_plugin_instance: int
	wait_for_plugin_playback_after_load: bool
	base_media_wait_after_load: int

	media_fallback_duration: int
	media_width: str
	media_height: str
	media_background_color: str

	plugin_syncing_page_type: str
	plugin_syncing_media_type: str
	plugin_syncing_unload_delay: float
	plugin_syncing_reload_vrml_from_cache: bool

	enable_plugin_input_repeater: bool
	plugin_input_repeater_initial_wait: int
	plugin_input_repeater_wait_per_cycle: int
	plugin_input_repeater_min_window_size: int
	plugin_input_repeater_keystrokes: str
	plugin_input_repeater_debug: bool

	enable_cosmo_player_viewpoint_cycler: bool
	cosmo_player_viewpoint_wait_per_cycle: int

	min_duration: int
	max_duration: int
	save_archive_copy: bool
	screen_capture_recorder_settings: dict[str, Optional[int]]

	raw_ffmpeg_input_name: str
	raw_ffmpeg_input_args: list[Union[int, str]]
	raw_ffmpeg_output_args: list[Union[int, str]]
	archive_ffmpeg_output_args: list[Union[int, str]]
	upload_ffmpeg_output_args: list[Union[int, str]]

	enable_text_to_speech: bool
	text_to_speech_audio_format_type: Optional[str]
	text_to_speech_rate: Optional[int]
	text_to_speech_default_voice: Optional[str]
	text_to_speech_language_voices: dict[str, str]

	text_to_speech_ffmpeg_video_input_name: str
	text_to_speech_ffmpeg_video_input_args: list[Union[int, str]]
	text_to_speech_ffmpeg_audio_input_args: list[Union[int, str]]
	text_to_speech_ffmpeg_output_args: list[Union[int, str]]

	enable_media_conversion: bool
	media_conversion_extensions: frozenset[str] # Different from the config data type.
	media_conversion_ffmpeg_input_name: str
	media_conversion_ffmpeg_input_args: list[Union[int, str]]
	media_conversion_add_subtitles: bool
	media_conversion_ffmpeg_subtitles_style: str

	enable_audio_mixing: bool
	audio_mixing_ffmpeg_output_args: list[Union[int, str]]
	midi_fluidsynth_args: list[Union[float, str]]

	# Determined at runtime.
	media_template: str
	physical_screen_width: int
	physical_screen_height: int
	width_dpi_scaling: float
	height_dpi_scaling: float

	def __init__(self):

		super().__init__()
		self.load_subconfig('record')

		assert len(list(self.sound_fonts_path.glob('*.sf2'))) > 0, f'Missing at least one SoundFont in "{self.sound_fonts_path}".'

		self.scheduler = container_to_lowercase(self.scheduler)

		self.allowed_media_extensions = frozenset(extension for extension in container_to_lowercase(self.allowed_media_extensions))
		self.multi_asset_media_extensions = frozenset(extension for extension in container_to_lowercase(self.multi_asset_media_extensions))
		assert self.multi_asset_media_extensions.issubset(self.allowed_media_extensions), 'The multi-asset media extensions must be a subset of the allowed media extensions.'

		if self.proxy_max_cdx_path_components is not None:
			self.proxy_max_cdx_path_components = max(self.proxy_max_cdx_path_components, 1)

		self.plugin_syncing_page_type = self.plugin_syncing_page_type.lower()
		assert self.plugin_syncing_page_type in ['none', 'reload_before', 'reload_twice', 'unload'], f'Unknown plugin syncing page type "{self.plugin_syncing_page_type}".'

		self.plugin_syncing_media_type = self.plugin_syncing_media_type.lower()
		assert self.plugin_syncing_media_type in ['none', 'reload_before', 'reload_twice', 'unload'], f'Unknown plugin syncing media type "{self.plugin_syncing_media_type}".'

		self.screen_capture_recorder_settings = container_to_lowercase(self.screen_capture_recorder_settings)
		self.text_to_speech_language_voices = container_to_lowercase(self.text_to_speech_language_voices)

		self.media_conversion_extensions = frozenset(extension for extension in container_to_lowercase(self.media_conversion_extensions))
		assert self.media_conversion_extensions.issubset(self.allowed_media_extensions), 'The convertible media extensions must be a subset of the allowed media extensions.'

		assert self.multi_asset_media_extensions.isdisjoint(self.media_conversion_extensions), 'The multi-asset and convertible media extensions must be mutually exclusive.'

		media_template_path = self.plugins_path / 'media.html.template'
		with open(media_template_path, encoding='utf-8') as file:
			self.media_template = file.read()

		S_OK = 0

		user32 = ctypes.windll.user32
		SM_CXSCREEN = 0
		SM_CYSCREEN = 1
		MONITOR_DEFAULTTOPRIMARY = 1

		shcore = ctypes.windll.shcore
		MDT_EFFECTIVE_DPI = 0

		# Get the correct screen resolution by taking into account DPI scaling.
		# See:
		# - https://docs.microsoft.com/en-us/windows/win32/hidpi/setting-the-default-dpi-awareness-for-a-process
		# - https://docs.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getsystemmetrics
		user32.SetProcessDPIAware()
		self.physical_screen_width = user32.GetSystemMetrics(SM_CXSCREEN)
		self.physical_screen_height = user32.GetSystemMetrics(SM_CYSCREEN)

		# Get the primary monitor's DPI scaling so we can correct the window
		# dimensions returned by the PyWinAuto library.
		# See:
		# - https://devblogs.microsoft.com/oldnewthing/20141106-00/?p=43683
		# - https://docs.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-monitorfromwindow
		# - https://docs.microsoft.com/en-us/windows/win32/api/shellscalingapi/nf-shellscalingapi-getdpiformonitor
		primary_monitor = user32.MonitorFromWindow(None, MONITOR_DEFAULTTOPRIMARY)
		dpi_x = ctypes.c_uint()
		dpi_y = ctypes.c_uint()
		hresult = shcore.GetDpiForMonitor(primary_monitor, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y))

		if hresult == S_OK:
			self.width_dpi_scaling = dpi_x.value / 96
			self.height_dpi_scaling = dpi_y.value / 96
		else:
			self.width_dpi_scaling = 1.0
			self.height_dpi_scaling = 1.0

if __name__ == '__main__':

	parser = ArgumentParser(description='Records the previously scouted snapshots by opening their pages in Firefox and scrolling through them at a set pace. If the recorder script detects that any plugins crashed or that the page was redirected while capturing the screen, the recording is aborted. This script is inherently unsafe since it relies on web plugins (e.g. Flash, Shockwave, Java, etc).')
	parser.add_argument('max_iterations', nargs='?', type=int, default=-1, help='How many snapshots to record. Omit or set to %(default)s to run forever on a set schedule.')
	args = parser.parse_args()

	config = RecordConfig()
	log = setup_logger('record')

	log.info('Initializing the recorder.')
	log.info(f'Detected the physical screen resolution {config.physical_screen_width}x{config.physical_screen_height} with the DPI scaling ({config.width_dpi_scaling:.2f}, {config.height_dpi_scaling:.2f}).')

	try:
		ffmpeg('-version')
	except FfmpegException:
		log.error('Could not find the FFmpeg executable in the PATH.')
		exit(1)

	try:
		fluidsynth('--version')
	except FluidSynthException:
		log.error('Could not find the FluidSynth executable in the PATH.')
		exit(1)

	scheduler = BlockingScheduler()

	def record_snapshots(num_snapshots: int) -> None:
		""" Records a given number of snapshots in a single batch. """

		log.info(f'Recording {num_snapshots} snapshots.')

		if config.enable_proxy:
			log.info('Initializing the proxy.')
			proxy = Proxy.create(port=config.proxy_port)
		else:
			proxy = nullcontext() # type: ignore

		if config.enable_text_to_speech:
			from common.text_to_speech import TextToSpeech
			log.info('Initializing the text-to-speech engine.')
			text_to_speech = TextToSpeech(config)

		media_page_file = NamedTemporaryFile(mode='w', encoding='utf-8', prefix=CommonConfig.TEMPORARY_PATH_PREFIX, suffix='.html', delete=False)
		media_page_url = f'file:///{media_page_file.name}'
		log.debug(f'Created the temporary media page "{media_page_file.name}".')

		media_download_directory = TemporaryDirectory(prefix=CommonConfig.TEMPORARY_PATH_PREFIX, suffix='.media')
		media_download_path = Path(media_download_directory.name)
		log.debug(f'Created the temporary media download directory "{media_download_path}".')

		# The subtitles path needs to be escaped before being passed to the subtitles filter.
		# See: https://superuser.com/a/1251305
		subtitles_file = NamedTemporaryFile(mode='w', encoding='utf-8', prefix=CommonConfig.TEMPORARY_PATH_PREFIX, suffix='.srt', delete=False)
		escaped_subtitles_path = subtitles_file.name.replace('\\', r'\\').replace(':', r'\:')
		log.debug(f'Created the temporary subtitles file "{subtitles_file.name}".')

		extra_preferences: dict = {
			# Always use the cached page.
			'browser.cache.check_doc_frequency': 2,

			# Don't show a prompt or try to kill a plugin if it stops responding.
			# We want the PluginCrashTimer to handle these silently in the background.
			# See:
			# - https://wiki.mozilla.org/Electrolysis/plugins
			# - https://dblohm7.ca/blog/2012/11/22/plugin-hang-user-interface-for-firefox/
			'dom.ipc.plugins.contentTimeoutSecs': -1,
			'dom.ipc.plugins.hangUITimeoutSecs': -1,
			'dom.ipc.plugins.parentTimeoutSecs': -1,
			'dom.ipc.plugins.processLaunchTimeoutSecs': -1,
			'dom.ipc.plugins.timeoutSecs': -1,
			'dom.ipc.plugins.unloadTimeoutSecs': -1,
		}

		if config.enable_proxy:
			extra_preferences.update({
				'network.proxy.type': 1, # Manual proxy configuration (see below).
				'network.proxy.share_proxy_settings': False,
				'network.proxy.http': '127.0.0.1',
				'network.proxy.http_port': proxy.port,
				'network.proxy.ssl': '127.0.0.1',
				'network.proxy.ssl_port': proxy.port,
				'network.proxy.ftp': '127.0.0.1',
				'network.proxy.ftp_port': proxy.port,
				'network.proxy.socks': '127.0.0.1',
				'network.proxy.socks_port': 9, # Discard Protocol.
				'network.proxy.no_proxies_on': 'localhost, 127.0.0.1', # For media snapshots.
			})

		try:
			with Database() as db, Browser(extra_preferences=extra_preferences, use_extensions=True, use_plugins=True, use_autoit=True) as (browser, driver), TemporaryRegistry() as registry:

				browser.go_to_blank_page_with_text('\N{Broom} Initializing \N{Broom}')
				browser.toggle_fullscreen()

				def generate_media_page(wayback_url: str, media_extension: Optional[str] = None) -> tuple[bool, Optional[Path], str, float, Optional[str], Optional[str]]:
					""" Generates the page where a media file is embedded using both the information from the configuration as well as the file's metadata. """

					success = True
					download_path = None

					wayback_parts = parse_wayback_machine_snapshot_url(wayback_url)
					parts = urlparse(wayback_parts.url if wayback_parts is not None else wayback_url)
					url_path = Path(parts.path)

					if media_extension is None:
						media_extension = url_path.suffix.lower().removeprefix('.')

					embed_url = wayback_url
					loop = 'true'
					duration: float = config.media_fallback_duration
					title = None
					author = None

					# If a media file points to other resources (e.g. VRML worlds or RealMedia metadata), we don't
					# want to download it since other files from the Wayback Machine may be required to play it.
					# If it doesn't (i.e. audio and video formats), we'll just download and play it from disk.
					if media_extension not in config.multi_asset_media_extensions:

						try:
							global_rate_limiter.wait_for_wayback_machine_rate_limit()
							response = global_session.get(wayback_url)
							response.raise_for_status()

							# We need to keep the file extension so Firefox can choose the right plugin to play it.
							download_path = media_download_path / url_path.name
							with open(download_path, 'wb') as file:
								file.write(response.content)

							log.debug(f'Downloaded the media file "{wayback_url}" to "{download_path}".')

							embed_url = f'file:///{download_path}'
							loop = 'false'

							info = ffprobe_info(download_path)

							# See: https://wiki.multimedia.cx/index.php/FFmpeg_Metadata
							tags = info['format'].get('tags', {})
							title = tags.get('title')
							author = tags.get('author') or tags.get('artist') or tags.get('album_artist') or tags.get('composer') or tags.get('copyright')
							log.debug(f'The media file "{title}" by "{author}" has the following tags: {tags}')

							duration = float(info['format']['duration'])
							log.debug(f'The media file has a duration of {duration:.2f} seconds.')

						except RequestException as error:
							log.error(f'Failed to download the media file "{wayback_url}" with the error: {repr(error)}')
							success = False
						except (FfmpegException, KeyError, ValueError) as error:
							log.warning(f'Could not parse the media file\'s metadata with the error: {repr(error)}')

					content = config.media_template
					content = content.replace('{comment}', f'Generated by "{__file__}" on {Database.get_current_timestamp()}.')
					content = content.replace('{background_color}', config.media_background_color)
					content = content.replace('{width}', config.media_width)
					content = content.replace('{height}', config.media_height)
					content = content.replace('{url}', embed_url)
					content = content.replace('{loop}', loop)

					# Overwrite the temporary media page.
					media_page_file.seek(0)
					media_page_file.truncate(0)
					media_page_file.write(content)
					media_page_file.flush()

					return success, download_path, media_extension, duration, title, author

				def abort_snapshot(snapshot: Snapshot) -> None:
					""" Aborts a snapshot that couldn't be recorded correctly due to a WebDriver error. """

					try:
						db.execute('UPDATE Snapshot SET State = :aborted_state WHERE Id = :id;', {'aborted_state': Snapshot.ABORTED, 'id': snapshot.Id})
						db.commit()
					except sqlite3.Error as error:
						log.error(f'Failed to abort the snapshot {snapshot} with the error: {repr(error)}')
						db.rollback()
						sleep(config.database_error_wait)

				def is_media_extension_allowed(media_extension: str) -> bool:
					""" Checks if a media snapshot should be recorded. """
					return media_extension in config.allowed_media_extensions

				db.create_function('IS_MEDIA_EXTENSION_ALLOWED', 1, is_media_extension_allowed)

				registry.clear('HKEY_CURRENT_USER\\SOFTWARE\\screen-capture-recorder')

				for key, value in config.screen_capture_recorder_settings.items():

					registry_key = f'HKEY_CURRENT_USER\\SOFTWARE\\screen-capture-recorder\\{key}'
					registry_value: int

					if value is None:

						# Set the default value for a few key configurations, and delete all others.
						# Although the Screen Capture Recorder says that these other values can be
						# set to zero, doing this for some of them results in an error that says they
						# should be removed instead.
						if key == 'capture_width':
							registry_value = config.physical_screen_width
							log.info(f'Using the physical width ({config.physical_screen_width}) to capture the screen.')
						elif key == 'capture_height':
							registry_value = config.physical_screen_height
							log.info(f'Using the physical height ({config.physical_screen_height}) to capture the screen.')
						elif key == 'default_max_fps':
							try:
								idx = config.raw_ffmpeg_input_args.index('-framerate')
								framerate = int(config.raw_ffmpeg_input_args[idx + 1])
							except (ValueError, IndexError):
								framerate = 60
							finally:
								registry_value = framerate
						else:
							registry.delete(registry_key)
							continue
					else:
						registry_value = value

					registry.set(registry_key, registry_value)

				for snapshot_index in range(num_snapshots):

					if was_exit_command_entered():

						log.info('Stopping at the user\'s request.')

						try:
							scheduler.shutdown(wait=False)
						except SchedulerNotRunningError:
							pass
						finally:
							break

					try:
						cursor = db.execute('''
											SELECT 	S.*,
													S.Priority <> :no_priority AS IsHighPriority,
													RANK_SNAPSHOT_BY_POINTS(SI.Points, :ranking_offset) AS Rank,
													SI.Points,
													LCR.RecordingsSinceSameHost,
													LPR.DaysSinceLastPublished
											FROM Snapshot S
											INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
											LEFT JOIN
											(
												SELECT 	SI.UrlHost,
														(SELECT COUNT(*) FROM Recording) - MAX(RRN.RowNum) AS RecordingsSinceSameHost
												FROM Snapshot S
												INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
												INNER JOIN
												(
													SELECT 	R.SnapshotId,
															(ROW_NUMBER() OVER (ORDER BY R.CreationTime)) AS RowNum
													FROM Recording R
												) RRN ON S.Id = RRN.SnapshotId
												GROUP BY SI.UrlHost
											) LCR ON SI.UrlHost = LCR.UrlHost
											LEFT JOIN
											(
												SELECT 	S.UrlKey,
														JulianDay('now') - JulianDay(MAX(R.PublishTime)) AS DaysSinceLastPublished
												FROM Snapshot S
												INNER JOIN Recording R ON S.Id = R.SnapshotId
												GROUP BY S.UrlKey
											) LPR ON S.UrlKey = LPR.UrlKey
											WHERE
												(
													S.State = :scouted_state
													OR
													(S.State = :published_state AND (IsHighPriority OR :min_publish_days_for_same_url IS NULL OR LPR.DaysSinceLastPublished >= :min_publish_days_for_same_url))
												)
												AND (NOT S.IsMedia OR IS_MEDIA_EXTENSION_ALLOWED(S.MediaExtension))
												AND NOT S.IsExcluded
												AND (IsHighPriority OR :min_year IS NULL OR SI.OldestYear >= :min_year)
												AND (IsHighPriority OR :max_year IS NULL OR SI.OldestYear <= :max_year)
												AND (IsHighPriority OR :record_sensitive_snapshots OR NOT SI.IsSensitive)
												AND (IsHighPriority OR LCR.RecordingsSinceSameHost IS NULL OR :min_recordings_for_same_host IS NULL OR LCR.RecordingsSinceSameHost >= :min_recordings_for_same_host)
												AND (IsHighPriority OR IS_URL_KEY_ALLOWED(S.UrlKey))
											ORDER BY
												S.Priority DESC,
												Rank DESC
											LIMIT 1;
											''',
											{'no_priority': Snapshot.NO_PRIORITY, 'ranking_offset': config.ranking_offset,
											 'scouted_state': Snapshot.SCOUTED, 'published_state': Snapshot.PUBLISHED,
											 'min_publish_days_for_same_url': config.min_publish_days_for_same_url,
											 'min_year': config.min_year, 'max_year': config.max_year,
											 'record_sensitive_snapshots': config.record_sensitive_snapshots,
											 'min_recordings_for_same_host': config.min_recordings_for_same_host})

						row = cursor.fetchone()
						if row is not None:

							snapshot = Snapshot(**row)

							assert snapshot.Points is not None, 'The Points column is not being computed properly.'

							config.apply_snapshot_options(snapshot)
							browser.set_fallback_encoding_for_snapshot(snapshot)

							recordings_since_same_host = row['RecordingsSinceSameHost']

							if recordings_since_same_host is not None:
								recordings_since_same_host = round(recordings_since_same_host)

							days_since_last_published = row['DaysSinceLastPublished']

							if days_since_last_published is not None:
								days_since_last_published = round(days_since_last_published)

							# Find the next auto incremented row ID.
							cursor = db.execute("SELECT seq + 1 AS NextRecordingId FROM sqlite_sequence WHERE name = 'Recording';")
							row = cursor.fetchone()
							recording_id = row['NextRecordingId'] if row is not None else 1
						else:
							log.info('Ran out of snapshots to record.')
							break

					except sqlite3.Error as error:
						log.error(f'Failed to select the next snapshot with the error: {repr(error)}')
						sleep(config.database_error_wait)
						continue

					# Due to the way snapshots are labelled, it's possible that a web page will be
					# marked as a media file and vice versa. Let's look at both cases:
					# - If it's actually a web page, then the plugin associated with that file
					# extension won't be able to play it. In most cases, this just results in a
					# black screen. For others, like Authorware, an AutoIt script is used to close
					# the error popup.
					# - If it's actually a media file, then the scout script will catch it and
					# label it correctly since all pages have to be scouted before they can be
					# recorded.

					try:
						log.info(f'[{snapshot_index+1} of {num_snapshots}] Recording snapshot #{snapshot.Id} {snapshot} with {snapshot.Points} points (same host = {recordings_since_same_host} recordings, last published = {days_since_last_published} days).')

						for path in media_download_path.glob('*'):
							delete_file(path)

						if snapshot.IsMedia:
							media_success, media_path, media_extension, media_duration, media_title, media_author = generate_media_page(snapshot.WaybackUrl, snapshot.MediaExtension)
							content_url = media_page_url

							if not media_success:
								log.error('Failed to generate the media file\'s page.')
								abort_snapshot(snapshot)
								continue
						else:
							media_title = None
							media_author = None
							content_url = snapshot.WaybackUrl

						missing_urls: list[str] = []

						if config.enable_proxy:
							proxy.timestamp = snapshot.Timestamp

						cache_wait = config.media_cache_wait if snapshot.IsMedia else config.page_cache_wait
						proxy_wait = config.proxy_queue_timeout + config.proxy_total_timeout if config.enable_proxy else 0

						# How much we wait before killing the plugins depends on how long we expect
						# each phase (caching and recording) to last in the worst case scenario.
						plugin_crash_timeout = config.base_plugin_crash_timeout + config.page_load_timeout + config.plugin_load_wait + cache_wait + proxy_wait

						frame_text_list = []
						audio_urls = {}
						realmedia_url = None

						# Wait for the page and its resources to be cached.
						with proxy, PluginCrashTimer(browser, plugin_crash_timeout):

							try:
								browser.bring_to_front()
								pywinauto.mouse.move((0, config.physical_screen_height // 2))
							except Exception as error:
								log.error(f'Failed to focus on the browser window and move the mouse with the error: {repr(error)}')

							cdx_must_be_up = config.enable_proxy and config.proxy_find_missing_snapshots_using_cdx
							browser.go_to_wayback_url(content_url, close_windows=True, check_availability=config.check_availability, cdx_must_be_up=cdx_must_be_up)

							if snapshot.Script is not None:
								log.info(f'Running custom script: "{snapshot.Script}"')
								driver.execute_script(snapshot.Script)

							# Make sure the plugin instances had time to load.
							sleep(config.plugin_load_wait)

							# This may be less than the real value if we had to kill any plugin instances.
							num_plugin_instances = browser.count_plugin_instances()
							log.debug(f'Found {num_plugin_instances} plugin instances.')

							if snapshot.PageUsesPlugins and num_plugin_instances == 0:

								# The bgsound and app tags are used in the scout script but not here
								# because the former was already converted to an embed tag and the
								# latter isn't supported by this browser.
								for _ in browser.traverse_frames():
									for tag in ['object', 'embed', 'applet']:
										num_plugin_instances += len(driver.find_elements_by_tag_name(tag))

								num_plugin_instances = max(num_plugin_instances, 1)
								log.warning(f'Could not find any plugin instances when at least one was expected. Assuming {num_plugin_instances} instances.')

							wait_after_load: float
							wait_per_scroll: float

							if snapshot.IsMedia:
								scroll_height = 0
								scroll_step = 0.0
								num_scrolls = 0
								wait_after_load = clamp(config.base_media_wait_after_load + media_duration, config.min_duration, config.max_duration)
								wait_per_scroll = 0.0
							else:
								scroll_height = 0
								client_height = 0
								for _ in browser.traverse_frames():

									# The correct height depends on if we're on quirks or standards mode.
									# E.g. https://web.archive.org/web/20071012232916if_/http://profile.myspace.com:80/index.cfm?fuseaction=user.viewprofile&friendid=15134349
									# Where body is right and documentElement is wrong (quirks)
									# E.g. https://web.archive.org/web/20130107202832if_/http://comic.naver.com/webtoon/detail.nhn?titleId=350217&no=31&weekday=tue
									# Where documentElement is right and body is wrong (standards).
									frame_scroll_height = driver.execute_script('return (document.compatMode === "BackCompat") ? (document.body.scrollHeight) : (document.documentElement.scrollHeight);')
									frame_client_height = driver.execute_script('return (document.compatMode === "BackCompat") ? (document.body.clientHeight) : (document.documentElement.clientHeight);')

									# The second condition is for rare cases where the largest scroll height
									# has a client height of zero.
									# E.g. https://web.archive.org/web/20071012232916if_/http://profile.myspace.com:80/index.cfm?fuseaction=user.viewprofile&friendid=15134349
									if frame_scroll_height > scroll_height and frame_client_height > 0:
										scroll_height = frame_scroll_height
										client_height = frame_client_height

									if config.enable_text_to_speech:

										# Replace every image with its alt text (if it exists) so it's read
										# when generating the text-to-speech file. An extra delimiter is
										# added to prevent run-on sentences.
										# E.g. https://web.archive.org/web/20000413210520if_/http://www.geocities.com:80/Athens/Acropolis/5551/index.html
										driver.execute_script(	'''
																const image_nodes = document.querySelectorAll("img[alt]");
																for(const element of image_nodes)
																{
																	const alt_text = element.getAttribute("alt");
																	element.replaceWith(alt_text + ". ");
																}
																''')

										frame_text = driver.execute_script('return document.documentElement.innerText;')
										frame_text_list.append(frame_text)

									if config.enable_audio_mixing:

										for url, params in browser.get_playback_plugin_elements():

											try:
												extension = extract_media_extension_from_url(url)
												loop = params.get('loop') in ['true', 'infinite', '-1'] or int(params.get('loop', 0)) >= 2
											except ValueError:
												loop = False

											audio_urls[url] = (extension, loop)

											try:
												log.debug(f'Probing "{url}" for audio mixing.')
												global_rate_limiter.wait_for_wayback_machine_rate_limit()
												if extension == 'swf' or not ffprobe_is_audio_only(url):
													audio_urls.clear()
													break
											except FfmpegException as error:
												log.warning(f'Could not probe the audio file "{url}" with the error: {repr(error)}')

								# While this works for most cases, there are pages where the scroll and client
								# height have the same value even though there's a scrollbar. This happens even
								# in modern Mozilla and Chromium browsers.
								# E.g. https://web.archive.org/web/20070122030542if_/http://www.youtube.com/index.php?v=6Gwn0ARKXgE
								scroll_step = client_height * config.viewport_scroll_percentage
								num_scrolls = ceil((scroll_height - client_height) / scroll_step)

								wait_after_load = config.base_wait_after_load + num_plugin_instances * config.wait_after_load_per_plugin_instance
								wait_per_scroll = config.base_wait_per_scroll + num_plugin_instances * config.wait_after_scroll_per_plugin_instance

								# Find the maximum duration of plugin content so the recording captures most it.
								# E.g. https://web.archive.org/web/19991005002723if_/http://www.geocities.com:80/TelevisionCity/Set/1939/
								if config.wait_for_plugin_playback_after_load:

									max_plugin_duration = None

									for url, _ in browser.get_playback_plugin_elements():
										try:
											log.debug(f'Probing "{url}" for the duration.')
											global_rate_limiter.wait_for_wayback_machine_rate_limit()
											duration = ffprobe_duration(url)
											if max_plugin_duration is not None:
												max_plugin_duration = max(max_plugin_duration, duration)
											else:
												max_plugin_duration = duration
										except FfmpegException as error:
											log.warning(f'Could not determine the duration of "{url}" with the error: {repr(error)}')

									if max_plugin_duration is not None:
										log.info(f'Found the maximum plugin content duration of {max_plugin_duration:.1f} seconds.')
										wait_after_load = max(wait_after_load, max_plugin_duration)

								min_wait_after_load = max(config.min_duration, config.base_wait_after_load + min(num_plugin_instances, 1) * config.wait_after_load_per_plugin_instance)
								max_wait_after_load = max(config.max_duration - num_scrolls * wait_per_scroll, 0)
								wait_after_load = clamp(wait_after_load, min_wait_after_load, max_wait_after_load)

								max_wait_per_scroll = (max(config.max_duration - wait_after_load, 0) / num_scrolls) if num_scrolls > 0 else 0
								wait_per_scroll = clamp(wait_per_scroll, 0, max_wait_per_scroll)

							log.info(f'Waiting {cache_wait:.1f} seconds for the page to cache.')
							sleep(cache_wait)

							# Keep waiting if the page or any plugins are still requesting data.
							if config.enable_proxy:

								log.debug('Waiting for the proxy.')
								begin_proxy_time = monotonic()

								proxy_status_codes: Counter = Counter()

								try:
									while True:

										elapsed_proxy_time = monotonic() - begin_proxy_time
										if elapsed_proxy_time > config.proxy_total_timeout:
											log.debug('Timed out while reading proxy messages.')
											break

										message = proxy.get(timeout=config.proxy_queue_timeout)
										log.debug(message)

										response_match = Proxy.RESPONSE_REGEX.fullmatch(message)
										save_match = Proxy.SAVE_REGEX.fullmatch(message) if config.proxy_save_missing_snapshots_that_still_exist_online else None
										realmedia_match = Proxy.REALMEDIA_REGEX.fullmatch(message) if config.proxy_convert_realmedia_metadata_snapshots else None

										if response_match is not None:

											status_code = response_match['status_code']
											mark = response_match['mark']
											proxy_status_codes[(status_code, mark)] += 1

										elif save_match is not None:

											url = save_match['url']
											missing_urls.append(url)

										elif realmedia_match is not None:

											realmedia_url = realmedia_match['url']

										proxy.task_done()

								except queue.Empty:
									log.debug('No more proxy messages.')
								finally:
									elapsed_proxy_time = monotonic() - begin_proxy_time
									proxy_status_codes = sorted(proxy_status_codes.items()) # type: ignore
									log.info(f'Waited {elapsed_proxy_time:.1f} extra seconds for the proxy: {proxy_status_codes}')

						if snapshot.IsMedia and realmedia_url is not None:

							log.info(f'Regenerating the media page for the RealMedia file "{realmedia_url}".')
							media_success, media_path, media_extension, media_duration, media_title, media_author = generate_media_page(realmedia_url)
							wait_after_load = clamp(config.base_media_wait_after_load + media_duration, config.min_duration, config.max_duration)

							if not media_success:
								log.error('Failed to generate the RealMedia file\'s page.')
								abort_snapshot(snapshot)
								continue

						if config.debug and browser.window is not None:

							plugin_windows = browser.window.children(class_name='GeckoPluginWindow')
							for window in plugin_windows:

								rect = window.rectangle()
								width = round(rect.width() / config.width_dpi_scaling)
								height = round(rect.height() / config.height_dpi_scaling)

								log.debug(f'Found a plugin instance with a size of {width}x{height}.')

						# Prepare the recording phase.

						plugin_crash_timeout = config.base_plugin_crash_timeout + config.page_load_timeout + config.max_duration
						plugin_syncing_type = config.plugin_syncing_media_type if snapshot.IsMedia else config.plugin_syncing_page_type

						plugin_input_repeater: Union[PluginInputRepeater, AbstractContextManager[None]] = PluginInputRepeater(browser.window, config) if config.enable_plugin_input_repeater else nullcontext()
						cosmo_player_viewpoint_cycler: Union[CosmoPlayerViewpointCycler, AbstractContextManager[None]] = CosmoPlayerViewpointCycler(browser.window, config) if config.enable_cosmo_player_viewpoint_cycler else nullcontext()

						subdirectory_path = config.get_recording_subdirectory_path(recording_id)
						subdirectory_path.mkdir(parents=True, exist_ok=True)

						parts = urlparse(snapshot.Url)
						media_identifier = snapshot.MediaExtension if snapshot.IsMedia else ('p' if snapshot.PageUsesPlugins else None)
						identifiers = [str(recording_id), str(snapshot.Id), parts.hostname, str(snapshot.OldestDatetime.year), str(snapshot.OldestDatetime.month).zfill(2), str(snapshot.OldestDatetime.day).zfill(2), media_identifier]
						path_prefix = subdirectory_path / '_'.join(filter(None, identifiers))

						upload_path: Path
						archive_path: Optional[Path]
						text_to_speech_path: Optional[Path]

						# This media extension differs from the snapshot's extension when recording a RealMedia file
						# whose URL was extracted from a metadata file. We should only be converting binary media,
						# and not text files like playlists or metadata.
						if config.enable_media_conversion and snapshot.IsMedia and media_extension in config.media_conversion_extensions and media_path is not None:

							# Convert a media snapshot directly and skip capturing the screen.
							log.info(f'Converting the media file "{media_path.name}".')

							browser.close_all_windows()
							browser.go_to_blank_page_with_text('\N{DNA Double Helix} Converting Media \N{DNA Double Helix}', str(snapshot))

							upload_path = Path(str(path_prefix) + '.mp4')
							archive_path = None
							text_to_speech_path = None

							try:
								if media_extension in ['mid', 'midi']:
									# This intermediate file is deleted later like the others in the media download directory.
									converted_path = media_path.with_suffix('.wav')
									sound_font_path = random.choice(list(config.sound_fonts_path.glob('*.sf2')))
									args = config.midi_fluidsynth_args + ['--fast-render', converted_path, sound_font_path, media_path]
									log.debug(f'Converting the MIDI file with the FluidSynth arguments: {args}')
									fluidsynth(*args)
									media_path = converted_path

								input_args = [
									'-guess_layout_max', 0, '-i', media_path,
									*config.media_conversion_ffmpeg_input_args, '-i', config.media_conversion_ffmpeg_input_name,
								]

								output_args = config.upload_ffmpeg_output_args.copy()

								if config.media_conversion_add_subtitles and not ffprobe_has_video_stream(media_path):

									log.debug('Adding subtitles to the converted media file.')

									preposition = 'by' if media_author is not None else None
									subtitles = '\n'.join(filter(None, [snapshot.DisplayTitle, media_title, preposition, media_author]))

									# Set a high enough duration so the subtitles last the entire recording.
									subtitles_file.seek(0)
									subtitles_file.truncate(0)
									subtitles_file.write(f'1\n00:00:00,000 --> 99:00:00,000\n{subtitles}')
									subtitles_file.flush()

									# Take into account any previous filters from the configuration file.
									subtitles_filter = f"subtitles='{escaped_subtitles_path}':force_style='{config.media_conversion_ffmpeg_subtitles_style}'"

									try:
										idx = output_args.index('-vf')
										output_args[idx + 1] += ',' + subtitles_filter
									except (ValueError, IndexError):
										output_args.extend(['-vf', subtitles_filter])

									output_args.extend(['-map', '1:v', '-map', '0:a'])
								else:
									output_args.extend(['-map', 0])

								output_args.extend(['-t', config.max_duration, '-shortest', upload_path])

								log.debug(f'Converting the media file with the FFmpeg arguments: {input_args + output_args}')
								output, warnings = ffmpeg(*input_args, *output_args)

								log.info(f'Saved the media conversion to "{upload_path}".')
								state = Snapshot.RECORDED

								for line in output.splitlines():
									log.info(f'FFmpeg output: {line}')

								for line in warnings.splitlines():
									log.warning(f'FFmpeg warning: {line}')

							except (FluidSynthException, FfmpegException) as error:
								log.error(f'Aborted the media conversion with the error: {repr(error)}')
								state = Snapshot.ABORTED
						else:
							# Record the snapshot. The page should load faster now that its resources are cached.

							with PluginCrashTimer(browser, plugin_crash_timeout) as crash_timer:

								try:
									browser.bring_to_front()
									pywinauto.mouse.move((0, config.physical_screen_height // 2))
								except Exception as error:
									log.error(f'Failed to focus on the browser window and move the mouse with the error: {repr(error)}')

								log.info(f'Waiting {wait_after_load:.1f} seconds after loading and then {wait_per_scroll:.1f} for each of the {num_scrolls} scrolls of {scroll_step:.1f} pixels to cover {scroll_height} pixels.')
								browser.go_to_wayback_url(content_url, close_windows=True, check_availability=config.check_availability)

								if snapshot.Script is not None:
									log.info(f'Running custom script: "{snapshot.Script}"')
									driver.execute_script(snapshot.Script)

								if config.plugin_syncing_reload_vrml_from_cache:
									# Syncing VRML content in some machines can prevent the Cosmo Player from retrieving
									# any previously cached assets. We'll fix this by reloading the page from cache using
									# the F5 shortcut if the Cosmo Player is being used. This solution can cause issues
									# with other plugins like VLC, which is why it's currently limited to VRML content.
									# E.g. https://web.archive.org/web/20220616010004if_/http://disciplinas.ist.utl.pt/leic-cg/materiais/VRML/cenas_vrml/golf/golf.wrl
									num_cosmo_player_instances = browser.count_plugin_instances('CpWin32RenderWindow')
									if num_cosmo_player_instances > 0:
										log.info(f'Reloading the page from cache since {num_cosmo_player_instances} Cosmo Player instances were found.')
										browser.reload_page_from_cache()

								if plugin_syncing_type in ['reload_before', 'reload_twice']:

									log.debug('Reloading plugin content.')
									browser.reload_plugin_content()

								elif plugin_syncing_type == 'unload':

									log.debug('Unloading plugin content.')
									browser.unload_plugin_content(skip_applets=True)

									def delayed_sync_plugins() -> None:
										""" Reloads any previously unloaded plugin content after a given amount of time has passed. """
										sleep(config.plugin_syncing_unload_delay)
										log.debug(f'Reloading plugin content after {config.plugin_syncing_unload_delay:.1f} seconds.')
										browser.reload_plugin_content(skip_applets=True)

									delayed_sync_plugins_thread = Thread(target=delayed_sync_plugins, name='sync_plugins', daemon=True)

								if config.hide_scrollbars:
									css = 	'''
											* {
												overflow: hidden !important;
											}
											'''
									base64_css = b64encode(css.encode()).decode()
									css_url = f'data:text/css;base64,{base64_css}'
									for _ in browser.traverse_frames():
										driver.execute_script(	'''
																const link = document.createElement("link");
																link.setAttribute("rel", "stylesheet");
																link.setAttribute("href", arguments[0]);
																document.head.prepend(link);
																''', css_url)

								with plugin_input_repeater, cosmo_player_viewpoint_cycler, ScreenCapture(path_prefix, config) as capture:

									if plugin_syncing_type == 'reload_twice':
										log.debug('Reloading plugin content.')
										browser.reload_plugin_content(skip_applets=True)
									elif plugin_syncing_type == 'unload':
										delayed_sync_plugins_thread.start()

									sleep(wait_after_load)

									for _ in range(num_scrolls):
										for _ in browser.traverse_frames():
											driver.execute_script('window.scrollBy({top: arguments[0], left: 0, behavior: "smooth"});', scroll_step)
										sleep(wait_per_scroll)

							if plugin_syncing_type == 'unload':
								delayed_sync_plugins_thread.join()

							redirected = False

							if not snapshot.IsMedia:
								redirected, url, timestamp = browser.was_wayback_url_redirected(content_url)
								if redirected:
									log.error(f'The page was redirected to "{url}" at {timestamp} while recording.')

							browser.close_all_windows()
							browser.go_to_blank_page_with_text('\N{Film Projector} Post Processing \N{Film Projector}', str(snapshot))
							capture.perform_post_processing()

							upload_path = capture.upload_path
							archive_path = capture.archive_path

							if crash_timer.crashed or capture.failed or redirected:
								log.error(f'Aborted the recording (plugins crashed = {crash_timer.crashed}, capture failed = {capture.failed}, redirected = {redirected}).')
								state = Snapshot.ABORTED
							else:
								log.info(f'Saved the recording to "{upload_path}".')
								state = Snapshot.RECORDED

							text_to_speech_path = None

							if config.enable_text_to_speech and not snapshot.IsMedia and state == Snapshot.RECORDED:

								browser.go_to_blank_page_with_text('\N{Speech Balloon} Generating Text-to-Speech \N{Speech Balloon}', str(snapshot))

								page_text = '.\n'.join(frame_text_list)
								text_to_speech_path = text_to_speech.generate_text_to_speech_file(snapshot.DisplayTitle, snapshot.OldestDatetime, page_text, snapshot.PageLanguage, path_prefix)

								if text_to_speech_path is not None:
									log.info(f'Saved the text-to-speech file to "{text_to_speech_path}".')

							if config.enable_audio_mixing and audio_urls:

								browser.go_to_blank_page_with_text('\N{Cocktail Glass} Mixing Audio \N{Cocktail Glass}', str(snapshot))

								try:
									mix_urls = {}

									for url, (extension, _) in audio_urls.items():

										if extension in ['mid', 'midi']:

											global_rate_limiter.wait_for_wayback_machine_rate_limit()
											response = global_session.get(url)
											response.raise_for_status()

											# These intermediate files are deleted later like the others in the media download directory.
											parts = urlparse(url)
											download_path = media_download_path / Path(parts.path).name
											with open(download_path, 'wb') as file:
												file.write(response.content)

											converted_path = download_path.with_suffix('.wav')
											sound_font_path = random.choice(list(config.sound_fonts_path.glob('*.sf2')))

											args = config.midi_fluidsynth_args + ['--fast-render', converted_path, sound_font_path, download_path]
											log.debug(f'Converting the MIDI file "{url}" with the FluidSynth arguments: {args}')

											fluidsynth(*args)
											mix_urls[converted_path] = audio_urls[url]

										elif extension == 'mp3':
											# A quick-and-dirty solution to the fact that FFmpeg sometimes freezes when processing MP3 files.
		   									# E.g. https://web.archive.org/web/20070206065503if_/http://churchstalros.ytmnd.com/
											parts = urlparse(url)
											converted_path = (media_download_path / Path(parts.path).name).with_suffix('.wav')
											log.debug(f'Converting the MP3 file "{url}" to WAV.')
											ffmpeg('-i', url, converted_path)
											mix_urls[converted_path] = audio_urls[url]
										else:
											mix_urls[url] = audio_urls[url]

									input_args = ['-guess_layout_max', 0, '-i', upload_path]

									max_duration = ffprobe_duration(upload_path)
									for url, (_, loop) in mix_urls.items():
										loop_count = -1 if loop else 0
										input_args.extend([
											'-stream_loop', loop_count,
											'-t', max_duration,
											'-guess_layout_max', 0,
											'-i', url,
										])

									mix_input = '[base]' + ''.join(f'[{i}:a]' for i in range(1, len(mix_urls) + 1))
									mix_path = upload_path.with_suffix('.mix.mp4')

									output_args = config.audio_mixing_ffmpeg_output_args.copy()
									output_args.extend([
										'-c:v', 'copy',
										'-filter_complex', f'[0:a]volume=0[base];{mix_input}amix={len(mix_urls) + 1}:duration=first[mix]',
										'-map', '0:v',
										'-map', '[mix]',
										mix_path,
									])

									log.debug(f'Mixing audio with the FFmpeg arguments: {input_args + output_args}')
									ffmpeg(*input_args, *output_args)

									delete_file(upload_path)
									upload_path = mix_path

									log.info(f'Saved the mixed audio recording to "{upload_path}".')

								except RequestException as error:
									log.error(f'Failed to download a MIDI file with the error: {repr(error)}')
								except FluidSynthException as error:
									log.error(f'Failed to convert a MIDI file with the error: {repr(error)}')
								except FfmpegException as error:
									log.error(f'Failed to mix the audio files with the error: {repr(error)}')

						has_audio = False
						if state == Snapshot.RECORDED:

							browser.go_to_blank_page_with_text('\N{Speaker With Three Sound Waves} Detecting Audio \N{Speaker With Three Sound Waves}', str(snapshot))

							try:
								log.debug(f'Detecting audio in "{upload_path}".')
								has_audio = ffmpeg_detect_audio(upload_path)
								log.info(f'Has Audio = {has_audio}.')
							except FfmpegException as error:
								log.error(f'Failed to detect audio with the error: {repr(error)}')

						# If enabled, this step should be done even when converting media files directly
						# since we might need to save a RealMedia file whose URL was extracted from a
						# metadata file.
						if config.proxy_save_missing_snapshots_that_still_exist_online:

							if missing_urls:
								log.info(f'Locating files based on {len(missing_urls)} missing URLs.')

							# Remove any duplicates to minimize the amount of requests to the Save API
							# and to improve look up operations when trying to find other missing URLs.
							extra_missing_urls = set(url for url in missing_urls)

							# Find other potentially missing URLs if the filename ends in a number.
							# If a file like "level3.dat" was missing, then we should check the
							# other values, both above and below 3.
							# E.g. https://web.archive.org/cdx/search/cdx?url=disciplinas.ist.utl.pt/leic-cg/materiais/VRML/cenas_vrml/cutplane/*&fl=original,timestamp,statuscode&collapse=urlkey
							for i, url in enumerate(missing_urls, start=1):

								browser.go_to_blank_page_with_text('\N{Left-Pointing Magnifying Glass} Locating Missing URLs \N{Left-Pointing Magnifying Glass}', f'{i} of {len(missing_urls)}')

								parts = urlparse(url)
								path = Path(parts.path)

								match = Proxy.FILENAME_REGEX.fullmatch(path.name)
								if match is None:
									continue

								log.debug(f'Filename match groups for the missing URL "{url}": {match.groups()}')

								name = match['name']
								padding = len(match['num'])
								extension = match['extension']

								num_consecutive_misses = 0
								for num in range(config.proxy_max_total_save_tries):

									if num_consecutive_misses >= config.proxy_max_consecutive_save_tries:
										break

									# Increment the value between the filename and extension.
									new_num = str(num).zfill(padding)
									new_filename = name + new_num + extension
									new_path = urljoin(str(path.parent) + '/', new_filename)
									new_parts = parts._replace(path=new_path)
									new_url = urlunparse(new_parts)

									if new_url in extra_missing_urls:
										continue

									if is_url_available(new_url):
										log.info(f'Found the consecutive missing URL "{new_url}".')
										extra_missing_urls.add(new_url)
										num_consecutive_misses = 0
									else:
										num_consecutive_misses += 1

									# Avoid making too many requests at once.
									sleep(1)
								else:
									# Avoid getting stuck in an infinite loop because the original
									# domain is parked, meaning there's potentially a valid response
									# for every possible consecutive number.
									log.warning(f'Stopping the search for more missing URLs after {config.proxy_max_total_save_tries} tries.')

							missing_urls = sorted(list(extra_missing_urls))
							saved_urls = []
							num_processed = 0

							if missing_urls:
								log.info(f'Saving {len(missing_urls)} missing URLs.')

							# This index is later used to store any skipped URLs.
							for i, url in enumerate(missing_urls):

								browser.go_to_blank_page_with_text('\N{Floppy Disk} Saving Missings URLs \N{Floppy Disk}', f'{i+1} of {len(missing_urls)}', url)

								try:
									global_rate_limiter.wait_for_save_api_rate_limit()
									save = WaybackMachineSaveAPI(url)
									wayback_url = save.save()

									if save.cached_save:
										log.info(f'The missing URL was already saved to "{wayback_url}"')
									else:
										log.info(f'Saved the missing URL to "{wayback_url}".')

										wayback_parts = parse_wayback_machine_snapshot_url(wayback_url)
										if wayback_parts is not None:
											url = wayback_parts.url
											timestamp = wayback_parts.timestamp
										else:
											timestamp = None

										saved_urls.append({'snapshot_id': snapshot.Id, 'recording_id': recording_id, 'url': url, 'timestamp': timestamp, 'failed': False})

								except TooManyRequestsError as error:
									log.error(f'Reached the Save API limit while trying to save the missing URL "{url}": {repr(error)}')
									break
								except Exception as error:
									log.error(f'Failed to save the missing URL "{url}" with the error: {repr(error)}')
									saved_urls.append({'snapshot_id': snapshot.Id, 'recording_id': recording_id, 'url': url, 'timestamp': None, 'failed': True})

								num_processed += 1

							if num_processed < len(missing_urls):

								remaining_missing_urls = missing_urls[i:]
								log.warning(f'Skipping {len(remaining_missing_urls)} missing URLs.')

								for url in remaining_missing_urls:
									saved_urls.append({'snapshot_id': snapshot.Id, 'recording_id': recording_id, 'url': url, 'timestamp': None, 'failed': True})

					except SessionNotCreatedException as error:
						log.warning(f'Terminated the WebDriver session abruptly with the error: {repr(error)}')
						break
					except WebDriverException as error:
						log.error(f'Failed to record the snapshot with the WebDriver error: {repr(error)}')
						abort_snapshot(snapshot)
						continue

					try:
						db.execute('UPDATE Snapshot SET State = :state WHERE Id = :id;', {'state': state, 'id': snapshot.Id})

						if state == Snapshot.RECORDED:

							archive_filename = archive_path.name if archive_path is not None else None
							text_to_speech_filename = text_to_speech_path.name if text_to_speech_path is not None else None

							db.execute(	'''
										INSERT INTO Recording (SnapshotId, HasAudio, UploadFilename, ArchiveFilename, TextToSpeechFilename)
										VALUES (:snapshot_id, :has_audio, :upload_filename, :archive_filename, :text_to_speech_filename);
										''',
										{'snapshot_id': snapshot.Id, 'has_audio': has_audio, 'upload_filename': upload_path.name,
										 'archive_filename': archive_filename, 'text_to_speech_filename': text_to_speech_filename})

							if snapshot.PriorityName == 'Record':
								db.execute('UPDATE Snapshot SET Priority = :no_priority WHERE Id = :id;', {'no_priority': Snapshot.NO_PRIORITY, 'id': snapshot.Id})

						else:
							delete_file(upload_path)

							if archive_path is not None:
								delete_file(archive_path)

							if text_to_speech_path is not None:
								delete_file(text_to_speech_path)

						if snapshot.IsMedia and all(metadata is None for metadata in [snapshot.MediaTitle, snapshot.MediaAuthor]):
							db.execute(	'UPDATE Snapshot SET MediaTitle = :media_title, MediaAuthor = :media_author WHERE Id = :id;',
										{'media_title': media_title, 'media_author': media_author, 'id': snapshot.Id})

						# For cases where looking at the plugin tags while scouting isn't enough.
						# E.g. https://web.archive.org/web/19961221002554if_/http://www.geocities.com:80/Hollywood/Hills/5988/
						if not snapshot.IsMedia and not snapshot.PageUsesPlugins and num_plugin_instances > 0:
							log.info(f'Detected {num_plugin_instances} plugin instances while no plugin tags were found during scouting.')
							db.execute('UPDATE Snapshot SET PageUsesPlugins = :page_uses_plugins WHERE Id = :id;', {'page_uses_plugins': True, 'id': snapshot.Id})

						if config.proxy_save_missing_snapshots_that_still_exist_online:
							db.executemany(	'''
											INSERT INTO SavedUrl (SnapshotId, RecordingId, Url, Timestamp, Failed)
											VALUES (:snapshot_id, :recording_id, :url, :timestamp, :failed)
											ON CONFLICT (Url)
											DO UPDATE SET Timestamp = :timestamp, Failed = :failed;
											''', saved_urls)

						db.commit()

					except sqlite3.Error as error:
						log.error(f'Failed to update the snapshot\'s status with the error: {repr(error)}')
						db.rollback()
						sleep(config.database_error_wait)
						continue

		except sqlite3.Error as error:
			log.error(f'Failed to connect to the database with the error: {repr(error)}')
		except KeyboardInterrupt:
			log.warning('Detected a keyboard interrupt when these should not be used to terminate the recorder due to a bug when using both Windows and the Firefox WebDriver.')
		finally:
			for path in config.recordings_path.rglob('*.raw.mkv'):
				log.warning(f'Deleting the raw recording file "{path}".')
				delete_file(path)

			try:
				media_download_directory.cleanup()
			except Exception as error:
				log.error(f'Failed to delete the temporary media download directory with the error: {repr(error)}')

			media_page_file.close()
			delete_file(media_page_file.name)

			subtitles_file.close()
			delete_file(subtitles_file.name)

			if config.enable_text_to_speech:
				text_to_speech.cleanup()

			if config.enable_proxy:
				proxy.shutdown()

		log.info(f'Finished recording {num_snapshots} snapshots.')

	if args.max_iterations >= 0:
		record_snapshots(args.max_iterations)
	else:
		log.info(f'Running the recorder with the schedule: {config.scheduler}')
		scheduler.add_job(record_snapshots, args=[config.num_snapshots_per_scheduled_batch], trigger='cron', coalesce=True, misfire_grace_time=None, **config.scheduler, timezone='UTC')
		scheduler.start()

	log.info('Terminating the recorder.')