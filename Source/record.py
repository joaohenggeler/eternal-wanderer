#!/usr/bin/env python3

"""
	This script records the previously scouted snapshots by opening their pages in Firefox and scrolling through them at a set pace.
	If the recorder script detects that any plugins crashed or that the page was redirected while capturing the screen, the recording is aborted.
	This script is inherently unsafe since it relies on web plugins (e.g. Flash, Shockwave, Java, etc).
"""

import ctypes
import os
import queue
import re
import sqlite3
import time
from argparse import ArgumentParser
from collections import Counter
from contextlib import nullcontext
from glob import iglob
from math import ceil
from queue import Queue
from random import random
from subprocess import PIPE, STDOUT
from subprocess import Popen, TimeoutExpired
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Thread, Timer
from typing import Dict, List, Optional, Tuple, Union, cast
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import ffmpeg # type: ignore
import pywinauto # type: ignore
import requests
from apscheduler.schedulers import SchedulerNotRunningError # type: ignore
from apscheduler.schedulers.blocking import BlockingScheduler # type: ignore
from pywinauto.application import Application as WindowsApplication # type: ignore
from selenium.common.exceptions import SessionNotCreatedException, WebDriverException # type: ignore
from selenium.webdriver.common.utils import free_port # type: ignore
from waybackpy import WaybackMachineSaveAPI
from waybackpy.exceptions import TooManyRequestsError

from common import Browser, CommonConfig, Database, Snapshot, TemporaryRegistry, container_to_lowercase, delete_file, get_current_timestamp, is_url_available, kill_processes_by_path, parse_wayback_machine_snapshot_url, setup_logger, was_exit_command_entered

####################################################################################################

class RecordConfig(CommonConfig):
	""" The configuration that applies to the recorder script. """

	# From the config file.
	scheduler: Dict[str, Union[int, str]]
	num_snapshots_per_scheduled_batch: int

	ranking_constant: int
	min_year: Optional[int]
	max_year: Optional[int]
	record_sensitive_snapshots: bool
	min_publish_days_for_new_recording: int

	allowed_standalone_media_file_extensions: Dict[str, bool] # Different from the config data type.
	nondownloadable_standalone_media_file_extensions: Dict[str, bool] # Different from the config data type.

	use_proxy: bool
	proxy_port: Optional[int]
	proxy_queue_timeout: int
	proxy_total_timeout: int
	block_proxy_requests_outside_archive_org: bool
	convert_realaudio_metadata_proxy_snapshots: bool
	find_missing_proxy_snapshots_using_cdx: bool
	max_missing_proxy_snapshot_path_components: Optional[int]
	save_missing_proxy_snapshots_that_still_exist_online: bool 
	max_consecutive_extra_missing_proxy_snapshot_tries: int
	max_total_extra_missing_proxy_snapshot_tries: int

	viewport_scroll_percentage: float
	page_cache_wait: int
	standalone_media_cache_wait: int

	base_wait_after_load: int
	extra_wait_after_load: int

	base_wait_per_scroll: int
	extra_wait_per_scroll: int

	max_wait_after_load_video_percentage: float
	points_per_extra_wait: int
	extra_standalone_media_wait_after_load: int

	standalone_media_fallback_duration: int
	standalone_media_width: str
	standalone_media_height: str
	standalone_media_background_color: str

	fullscreen_browser: bool
	reload_plugin_media_before_recording: bool
	base_plugin_crash_timeout: int
	cosmo_player_viewpoint_wait_per_cycle: Optional[int]

	min_video_duration: int
	max_video_duration: int
	keep_archive_copy: bool
	screen_capture_recorder_settings: Dict[str, Optional[int]]

	ffmpeg_global: List[str]
	ffmpeg_recording_input: Dict[str, Union[int, str]]
	ffmpeg_recording_output: Dict[str, Union[int, str]]
	ffmpeg_archive_output: Dict[str, Union[int, str]]
	ffmpeg_upload_output: Dict[str, Union[int, str]]

	# Determined at runtime.
	standalone_media_template: str
	physical_screen_width: int
	physical_screen_height: int

	def __init__(self):
		super().__init__()
		self.load_subconfig('record')

		self.allowed_standalone_media_file_extensions = {extension: True for extension in container_to_lowercase(self.allowed_standalone_media_file_extensions)}
		self.nondownloadable_standalone_media_file_extensions = {extension: True for extension in container_to_lowercase(self.nondownloadable_standalone_media_file_extensions)}

		if self.max_missing_proxy_snapshot_path_components is not None:
			self.max_missing_proxy_snapshot_path_components = max(self.max_missing_proxy_snapshot_path_components, 1)

		self.screen_capture_recorder_settings = container_to_lowercase(self.screen_capture_recorder_settings)

		self.ffmpeg_global = container_to_lowercase(self.ffmpeg_global)
		self.ffmpeg_recording_input = container_to_lowercase(self.ffmpeg_recording_input)
		self.ffmpeg_recording_output = container_to_lowercase(self.ffmpeg_recording_output)
		self.ffmpeg_archive_output = container_to_lowercase(self.ffmpeg_archive_output)
		self.ffmpeg_upload_output = container_to_lowercase(self.ffmpeg_upload_output)

		template_path = os.path.join(self.plugins_path, 'standalone_media.html.template')
		with open(template_path, 'r', encoding='utf-8') as file:
			self.standalone_media_template = file.read()

		# Get the correct screen resolution by taking into account DPI scaling.
		user32 = ctypes.windll.user32
		user32.SetProcessDPIAware()
		self.physical_screen_width = user32.GetSystemMetrics(0)
		self.physical_screen_height = user32.GetSystemMetrics(1)

if __name__ == '__main__':

	config = RecordConfig()
	log = setup_logger('record')

	parser = ArgumentParser(description='Records the previously scouted snapshots by opening their pages in Firefox and scrolling through them at a set pace. If the recorder script detects that any plugins crashed or that the page was redirected while capturing the screen, the recording is aborted. This script is inherently unsafe since it relies on web plugins (e.g. Flash, Shockwave, Java, etc).')
	parser.add_argument('max_iterations', nargs='?', type=int, default=-1, help='How many snapshots to record. Omit or set to %(default)s to run forever on a set schedule.')
	args = parser.parse_args()

	####################################################################################################

	class Proxy(Thread):
		""" A proxy thread that intercepts all HTTP/HTTPS requests made by Firefox and its plugins. Used to locate missing resources
		in other subdomains via the CDX API while also allowing plugin media that loads slowly to finish requesting assets. """

		port: int
		process: Popen
		queue: Queue
		timestamp: Optional[str]

		RESPONSE_REGEX = re.compile(r'\[RESPONSE\] \[(?P<status_code>.+)\] \[(?P<mark>.+)\] \[(?P<content_type>.+)\] \[(?P<url>.+)\] \[(?P<id>.+)\]')
		SAVE_REGEX  = re.compile(r'\[SAVE\] \[(?P<url>.+)\]')
		REALAUDIO_REGEX  = re.compile(r'\[RAM\] \[(?P<url>.+)\]')
		FILENAME_REGEX  = re.compile(r'(?P<name>.*?)(?P<num>\d+)(?P<extension>\..*)')

		def __init__(self, port: int):

			super().__init__(name='proxy', daemon=True)

			self.port = port
			os.environ['PYTHONUNBUFFERED'] = '1'
			self.process = Popen(['mitmdump', '--quiet', '--listen-port', str(self.port), '--script', 'wayback_proxy_addon.py'], stdin=PIPE, stdout=PIPE, stderr=STDOUT, bufsize=1, encoding='utf-8')
			self.queue = Queue()
			self.timestamp = None

			self.start()

		@staticmethod
		def create() -> 'Proxy':
			""" Starts the proxy while handling any errors at startup (e.g. Python errors or already used ports). """

			port = free_port() if config.proxy_port is None else config.proxy_port

			while True:
				try:
					log.info(f'Starting the proxy on port {port}.')
					proxy = Proxy(port)

					error = proxy.get(timeout=10)
					log.error(f'Failed to create the proxy with the error: {error}')
					proxy.task_done()
					
					proxy.process.kill()
					port = free_port()

				except queue.Empty:
					break

			return proxy

		def run(self):
			""" Runs the proxy thread on a loop, enqueuing any messages received from the mitmproxy script. """
			for line in iter(self.process.stdout.readline, ''):
				self.queue.put(line.rstrip('\n'))
			self.process.stdout.close()

		def get(self, **kwargs) -> str:
			""" Retrieves a message from the queue. """
			return self.queue.get(**kwargs)

		def task_done(self) -> None:
			""" Signals that a retrieved message was handled. """
			self.queue.task_done()

		def clear(self) -> None:
			""" Clears the message queue. """
			while not self.queue.empty():
				try:
					self.get(block=False)
					self.task_done()
				except queue.Empty:
					pass

		def exec(self, command: str) -> None:
			""" Passes a command that is then executed in the mitmproxy script. """
			self.process.stdin.write(command + '\n') # type: ignore
			self.get()
			self.task_done()

		def shutdown(self) -> None:
			""" Stops the mitmproxy script and proxy thread. """
			try:
				self.process.terminate()
				self.join()
			except OSError as error:
				log.error(f'Failed to terminate the proxy process with the error: {repr(error)}')

		def __enter__(self):
			self.clear()
			self.exec(f'current_timestamp = "{self.timestamp}"')

		def __exit__(self, exception_type, exception_value, traceback):
			self.exec('current_timestamp = None')

	class PluginCrashTimer():
		""" A special timer that kills Firefox's plugin container child processes after a given time has elapsed (e.g. the recording duration). """

		firefox_directory_path: str
		timeout: float
		timer: Timer
		plugin_container_path: str
		crashed: bool

		def __init__(self, firefox_directory_path: str, timeout: float):
			self.firefox_directory_path = firefox_directory_path
			self.timeout = timeout
			self.timer = Timer(self.timeout, self.kill_plugin_containers)
			self.plugin_container_path = os.path.join(self.firefox_directory_path, 'plugin-container.exe')

		def start(self) -> None:
			""" Starts the timer. """
			self.crashed = False
			self.timer.start()

		def stop(self) -> None:
			""" Stops the timer and checks if it had to kill the plugin container processes at any point. """
			self.crashed = not self.timer.is_alive()
			self.timer.cancel()

		def __enter__(self):
			self.start()
			return self

		def __exit__(self, exception_type, exception_value, traceback):
			self.stop()

		def kill_plugin_containers(self) -> None:
			log.warning(f'Killing all plugin containers since {self.timeout:.1f} seconds have passed without the timer being reset.')
			kill_processes_by_path(self.plugin_container_path)

	class ScreenCapture():
		""" A process that captures the screen and stores the recording on disk using ffmpeg. """

		raw_recording_path: str
		archive_recording_path: str
		upload_recording_path: str
		
		stream: ffmpeg.Stream
		process: Popen
		failed: bool

		def __init__(self, output_path_prefix: str):
			
			self.raw_recording_path = output_path_prefix + '.raw.mkv'
			self.archive_recording_path = output_path_prefix + '.mkv'
			self.upload_recording_path = output_path_prefix + '.mp4'

			stream = ffmpeg.input('video=screen-capture-recorder:audio=virtual-audio-capturer', t=config.max_video_duration, **config.ffmpeg_recording_input)
			stream = stream.output(self.raw_recording_path, **config.ffmpeg_recording_output)
			stream = stream.global_args(*config.ffmpeg_global)
			stream = stream.overwrite_output()
			self.stream = stream

		def start(self) -> None:
			""" Starts the ffmpeg screen capture process asynchronously. """

			log.debug(f'Recording with the ffmpeg arguments: {self.stream.get_args()}')
			self.failed = False
			# Connecting a pipe to stdin is required to stop the recording by pressing Q.
			# See: https://github.com/kkroening/ffmpeg-python/issues/162
			self.process = self.stream.run_async(pipe_stdin=True)		

		def stop(self) -> None:
			""" Stops the ffmpeg screen capture process gracefully or kills it doesn't respond. """

			try:
				self.process.communicate(b'q', timeout=10)
			except TimeoutExpired:
				log.error('Failed to stop the recording gracefully.')
				self.failed = True
				self.process.kill()

		def __enter__(self):
			self.start()
			return self

		def __exit__(self, exception_type, exception_value, traceback):
			self.stop()
			
		def perform_post_processing(self) -> None:
			""" Converts the lossless MKV recording into a lossy MP4 video, and optionally reduces the size of the lossless copy for archival. """

			if not self.failed:

				output_types = [(self.upload_recording_path, config.ffmpeg_upload_output)]
				
				if config.keep_archive_copy:
					output_types.append((self.archive_recording_path, config.ffmpeg_archive_output))

				for output_path, output_arguments in output_types:

					stream = ffmpeg.input(self.raw_recording_path)
					stream = stream.output(output_path, **output_arguments)
					stream = stream.global_args(*config.ffmpeg_global)
					stream = stream.overwrite_output()

					try:
						log.debug(f'Processing the recording with the ffmpeg arguments: {stream.get_args()}')
						stream.run()
					except ffmpeg.Error as error:
						log.error(f'Failed to process "{self.raw_recording_path}" into "{output_path}" with the error: {repr(error)}')
						self.failed = True
						break
			
			delete_file(self.raw_recording_path)

	class CosmoPlayerViewpointCycler(Thread):
		""" A thread that periodically tells any VRML world running in the Cosmo Player to move to the next viewpoint. """

		firefox_application: Optional[WindowsApplication]
		wait_per_cycle: int
		running: bool

		def __init__(self, firefox_application: Optional[WindowsApplication], wait_per_cycle: int):

			super().__init__(name='cosmo_player_viewpoint_cycler', daemon=True)
			
			self.firefox_application = firefox_application
			self.wait_per_cycle = wait_per_cycle
			self.running = False

		def run(self):
			""" Runs the viewpoint cycler on a loop, sending the "Next Viewpoint" hotkey periodically to any Cosmo Player windows. """

			if self.firefox_application is None:
				return

			while self.running:
				
				time.sleep(self.wait_per_cycle)
				
				try:
					cosmo_player_windows = self.firefox_application.MozillaWindowClass.children(class_name='CpWin32RenderWindow')
					for window in cosmo_player_windows:
						window.send_keystrokes('{PGDN}')
				except Exception:
					pass

		def startup(self) -> None:
			""" Starts the viewpoint cycler thread. """
			self.running = True
			self.start()

		def shutdown(self) -> None:
			""" Stops the viewpoint cycler thread. """
			self.running = False
			self.join()

		def __enter__(self):
			self.startup()
			return self

		def __exit__(self, exception_type, exception_value, traceback):
			self.shutdown()

	####################################################################################################

	log.info('Initializing the recorder.')

	scheduler = BlockingScheduler()

	def record_snapshots(num_snapshots: int) -> None:
		""" Records a given number of snapshots in a single batch. """
		
		try:
			if config.use_proxy:
				proxy = Proxy.create()
			else:
				proxy = nullcontext() # type: ignore

			standalone_media_page_file = NamedTemporaryFile(mode='w', encoding='utf-8', prefix='wanderer.', suffix='.html', delete=False)
			standalone_media_page_url = f'file:///{standalone_media_page_file.name}'
			log.debug(f'Created the temporary standalone media page file "{standalone_media_page_file.name}".')

			standalone_media_download_directory = TemporaryDirectory(prefix='wanderer.', suffix='.media')
			standalone_media_download_search_path = os.path.join(standalone_media_download_directory.name, '*')
			log.debug(f'Created the temporary standalone media download directory "{standalone_media_download_directory.name}".')

			extra_preferences: dict = {
				'browser.cache.check_doc_frequency': 2, # Always use cached page.
				# Don't show prompt if a plugin stops responding. We want the PluginCrashTimer to handle these silently in the background.
				'dom.ipc.plugins.timeoutSecs': -1, 
			} 

			if config.use_proxy:
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
					'network.proxy.no_proxies_on': 'localhost, 127.0.0.1', # For standalone media.
				})

			with Database() as db, Browser(extra_preferences=extra_preferences, use_extensions=True, use_plugins=True, use_autoit=True) as (browser, driver), TemporaryRegistry() as registry:

				def generate_standalone_media_page(wayback_url: str) -> Tuple[float, Optional[str], Optional[str]]:
					""" Generates the page where a standalone media file is embedded using both the information
					from the configuration as well as the file's metadata. """

					wayback_parts = parse_wayback_machine_snapshot_url(wayback_url)
					parts = urlparse(wayback_parts.Url if wayback_parts is not None else wayback_url)
					filename = os.path.basename(parts.path)
					_, file_extension = os.path.splitext(filename)
					file_extension = file_extension.lower().strip('.')

					# Two separate URLs because ffmpeg uses "file:path" instead of "file://path".
					# See: https://superuser.com/questions/718027/ffmpeg-concat-doesnt-work-with-absolute-path/1551017#1551017
					embed_url = wayback_url
					probe_url = wayback_url

					# If a media file points to other resources (e.g. VRML worlds or RealAudio metadata), we don't
					# want to download it since other files from the Wayback Machine may be required to play it.
					# If it doesn't (e.g. most audio and video formats), just download and play it from disk.
					if file_extension not in config.nondownloadable_standalone_media_file_extensions:
						try:
							response = requests.get(wayback_url)
							response.raise_for_status()
							
							# We need to keep the file extension so Firefox can choose the right plugin to play it.
							downloaded_file_path = os.path.join(standalone_media_download_directory.name, filename)
							with open(downloaded_file_path, 'wb') as file:
								file.write(response.content)
						
							log.debug(f'Downloaded the standalone media "{wayback_url}" to "{downloaded_file_path}".')
							embed_url = f'file:///{downloaded_file_path}'
							probe_url = f'file:{downloaded_file_path}'

						except requests.RequestException as error:
							log.error(f'Failed to download the standalone media file "{wayback_url}" with the error: {repr(error)}')

					try:
						probe = ffmpeg.probe(probe_url)
						format = probe['format']
						tags = format.get('tags', {})
						
						title = tags.get('title')
						author = tags.get('author') or tags.get('artist') or tags.get('album_artist') or tags.get('composer') or tags.get('copyright')
						log.debug(f'The standalone media "{title}" by "{author}" has the following tags: {tags}')

						duration = float(format['duration'])
						log.debug(f'The standalone media has a duration of {duration} seconds.')
						loop = 'false'
					except (ffmpeg.Error, KeyError, ValueError) as error:
						log.warning(f'Could not parse the standalone media\'s metadata with the error: {repr(error)}')
						duration = config.standalone_media_fallback_duration
						title = None
						author = None
						loop = 'true'

					content = config.standalone_media_template
					content = content.replace('{comment}', f'Generated by "{__file__}" on {get_current_timestamp()}.')
					content = content.replace('{background_color}', config.standalone_media_background_color)
					content = content.replace('{width}', config.standalone_media_width)
					content = content.replace('{height}', config.standalone_media_height)
					content = content.replace('{url}', embed_url)
					content = content.replace('{loop}', loop)
					
					# Overwrite the temporary standalone media page.
					standalone_media_page_file.seek(0)
					standalone_media_page_file.truncate(0)
					standalone_media_page_file.write(content)
					standalone_media_page_file.flush()

					return duration, title, author

				def abort_snapshot(snapshot: Snapshot) -> None:
					""" Aborts a snapshot that couldn't be recorded correctly due to a WebDriver error. """

					try:
						db.execute('UPDATE Snapshot SET State = :aborted_state WHERE Id = :id;', {'aborted_state': Snapshot.ABORTED, 'id': snapshot.Id})
						db.commit()
					except sqlite3.Error as error:
						log.error(f'Failed to abort the snapshot {snapshot} with the error: {repr(error)}')
						db.rollback()
						time.sleep(config.database_error_wait)

				def rank_snapshot_by_points(points: int) -> float:
					""" Ranks a snapshot using a weighted random sampling algorithm. """
					# See:
					# - https://stackoverflow.com/a/56006340/18442724
					# - http://utopia.duth.gr/~pefraimi/research/data/2007EncOfAlg.pdf
					# For negative points, the ranking is inverted.
					sign = 1 if points >= 0 else -1
					return sign * random() ** (config.ranking_constant / (abs(points) + 1))

				def is_standalone_media_file_extension_allowed(file_extension: str) -> bool:
					""" Checks if a standalone media snapshot should be recorded. """
					return bool(config.allowed_standalone_media_file_extensions) and file_extension in config.allowed_standalone_media_file_extensions

				db.create_function('RANK_SNAPSHOT_BY_POINTS', 1, rank_snapshot_by_points)
				db.create_function('IS_STANDALONE_MEDIA_FILE_EXTENSION_ALLOWED', 1, is_standalone_media_file_extension_allowed)

				if config.fullscreen_browser:
					browser.toggle_fullscreen()

				for key, value in TemporaryRegistry.traverse('HKEY_CURRENT_USER\\SOFTWARE\\screen-capture-recorder'):
					registry.delete(key)

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
							framerate = config.ffmpeg_recording_input.get('framerate', 60)
							registry_value = cast(int, framerate)
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

						break

					try:
						cursor = db.execute('''
											SELECT 	S.*,
													CAST(MIN(SUBSTR(S.Timestamp, 1, 4), IFNULL(SUBSTR(S.LastModifiedTime, 1, 4), '9999')) AS INTEGER) AS OldestYear,
													SI.Points,
													RANK_SNAPSHOT_BY_POINTS(SI.Points) AS Rank,
													LR.DaysSinceLastPublished
											FROM Snapshot S
											INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
											LEFT JOIN
											(
												SELECT R.SnapshotId, JulianDay('now') - JulianDay(MAX(R.PublishTime)) AS DaysSinceLastPublished
												FROM Recording R
												GROUP BY R.SnapshotId
											) LR ON S.Id = LR.SnapshotId
											WHERE
												(
													S.State = :scouted_state
													OR
													(S.State = :published_state AND LR.DaysSinceLastPublished >= :min_publish_days_for_new_recording)
												)
												AND NOT S.IsExcluded
												AND (:min_year IS NULL OR OldestYear >= :min_year)
												AND (:max_year IS NULL OR OldestYear <= :max_year)
												AND (:record_sensitive_snapshots OR NOT SI.IsSensitive)
												AND (NOT S.IsStandaloneMedia OR IS_STANDALONE_MEDIA_FILE_EXTENSION_ALLOWED(S.FileExtension))
												AND IS_URL_KEY_ALLOWED(S.UrlKey)
											ORDER BY
												S.Priority DESC,
												Rank DESC
											LIMIT 1;
											''', {'scouted_state': Snapshot.SCOUTED, 'published_state': Snapshot.PUBLISHED,
												  'min_publish_days_for_new_recording': config.min_publish_days_for_new_recording,
												  'min_year': config.min_year, 'max_year': config.max_year,
												  'record_sensitive_snapshots': config.record_sensitive_snapshots})
						
						row = cursor.fetchone()
						if row is not None:
							snapshot = Snapshot(**dict(row))
							
							assert snapshot.Points is not None, 'The Points column is not being computed properly.'

							rank = row['Rank'] * 100
							days_since_last_published = row['DaysSinceLastPublished']

							if days_since_last_published is not None:
								days_since_last_published = round(days_since_last_published)

							# Find the next auto incremented row ID.
							cursor = db.execute('''SELECT seq + 1 AS NextRecordingId FROM sqlite_sequence WHERE name = 'Recording';''')
							row = cursor.fetchone()
							recording_id = row['NextRecordingId'] if row is not None else 1
						else:
							log.info('Ran out of snapshots to record.')
							break

					except sqlite3.Error as error:
						log.error(f'Failed to select the next snapshot with the error: {repr(error)}')
						time.sleep(config.database_error_wait)
						continue

					# Due to the way snapshots are labelled, it's possible that a regular page
					# will be marked as standalone media and vice versa. Let's look at both cases:
					# - If it's actually a regular page, then the plugin associated with that file
					# extension won't be able to play it. In most cases, this just results in a
					# black screen. For others, like Authorware, an AutoIt script is used to close
					# the error popup.
					# - If it's actually standalone media, then the scout script will catch it and
					# label it correctly since all pages have to be scouted before they can be
					# recorded.

					try:
						for path in iglob(standalone_media_download_search_path):
							delete_file(path)

						media_title = None
						media_author = None

						if snapshot.IsStandaloneMedia:
							media_duration, media_title, media_author = generate_standalone_media_page(snapshot.WaybackUrl)
							content_url = standalone_media_page_url
						else:
							content_url = snapshot.WaybackUrl

						log.info(f'[{snapshot_index+1} of {num_snapshots}] Recording snapshot #{snapshot.Id} {snapshot} ranked at {rank:.2f}% with {snapshot.Points} points (last published = {days_since_last_published} days ago).')
						
						original_window = driver.current_window_handle
						browser.bring_to_front()
						pywinauto.mouse.move(coords=(0, config.physical_screen_height // 2))
						
						missing_urls: List[str] = []

						if config.use_proxy:
							proxy.timestamp = snapshot.Timestamp
						
						cache_wait = config.standalone_media_cache_wait if snapshot.IsStandaloneMedia else config.page_cache_wait

						# How much we wait before killing the plugins depends on how long we expect
						# each phase (caching and recording) to last in the worst case scenario.
						plugin_crash_timeout = config.page_load_timeout + cache_wait + (config.proxy_total_timeout if config.use_proxy else 0) + config.base_plugin_crash_timeout

						realaudio_url = None

						# Wait for the page and its resources to be cached.
						with proxy, PluginCrashTimer(browser.firefox_directory_path, plugin_crash_timeout) as crash_timer:

							browser.go_to_wayback_url(content_url)

							wait_after_load: float
							wait_per_scroll: float

							if snapshot.IsStandaloneMedia:
								scroll_step = 0.0
								num_scrolls_to_bottom = 0
								wait_after_load = max(config.min_video_duration, min(media_duration + config.extra_standalone_media_wait_after_load, config.max_video_duration))
								wait_per_scroll = 0.0
							else:
								scroll_height = 0
								for _ in browser.traverse_frames():
									
									frame_scroll_height = driver.execute_script('return document.body.scrollHeight;')
									if frame_scroll_height > scroll_height:

										scroll_height = frame_scroll_height
										client_height = driver.execute_script('return document.body.clientHeight;')

								# While this works for most cases, there are pages where the scroll and client
								# height have the same value even though there's a scrollbar. This happens even
								# in modern Mozilla and Chromium browsers.
								# E.g. https://web.archive.org/web/20070122030542if_/http://www.youtube.com/index.php?v=6Gwn0ARKXgE
								scroll_step = client_height * config.viewport_scroll_percentage
								num_scrolls_to_bottom = ceil((scroll_height - client_height) / scroll_step)

								wait_after_load = config.base_wait_after_load + config.extra_wait_after_load * cast(int, snapshot.Points) / config.points_per_extra_wait
								wait_after_load = max(config.min_video_duration, min(wait_after_load, config.max_video_duration * config.max_wait_after_load_video_percentage))
								
								wait_per_scroll = config.base_wait_per_scroll + config.extra_wait_per_scroll * cast(int, snapshot.Points) / config.points_per_extra_wait
								wait_per_scroll = min(wait_per_scroll, (config.max_video_duration - wait_after_load) / max(num_scrolls_to_bottom, 1))

							log.info(f'Waiting {cache_wait:.1f} seconds for the page to cache.')
							time.sleep(cache_wait)

							# Keep waiting if the page or its plugins are still requesting data.
							if config.use_proxy:
								
								log.debug('Waiting for the proxy.')
								begin_proxy_time = time.time()
								
								proxy_status_codes: Counter = Counter()
								skip_proxy_save = snapshot.Options.get('skip_proxy_save', False)

								try:
									while True:
										
										elapsed_proxy_time = time.time() - begin_proxy_time
										if elapsed_proxy_time > config.proxy_total_timeout:
											log.debug('Timed out while reading proxy messages.')
											break

										message = proxy.get(timeout=config.proxy_queue_timeout)
										log.debug(message)
										
										response_match = Proxy.RESPONSE_REGEX.fullmatch(message)
										save_match = Proxy.SAVE_REGEX.fullmatch(message) if config.save_missing_proxy_snapshots_that_still_exist_online else None
										realaudio_match = Proxy.REALAUDIO_REGEX.fullmatch(message) if config.convert_realaudio_metadata_proxy_snapshots else None

										if response_match is not None:
											
											status_code = response_match.group('status_code')
											mark = response_match.group('mark')
											proxy_status_codes[(status_code, mark)] += 1

										elif save_match is not None:
											
											if not skip_proxy_save:					
												url = save_match.group('url')
												missing_urls.append(url)
											else:
												log.info(f'Skipping the missing proxy snapshot save process at the user\'s request.')

										elif realaudio_match is not None:
											
											realaudio_url = realaudio_match.group('url')

										proxy.task_done()

								except queue.Empty:
									log.debug('No more proxy messages.')
								finally:
									elapsed_proxy_time = time.time() - begin_proxy_time
									proxy_status_codes = sorted(proxy_status_codes.items()) # type: ignore
									log.info(f'Waited {elapsed_proxy_time:.1f} extra seconds for the proxy: {proxy_status_codes}')

						if snapshot.IsStandaloneMedia and realaudio_url is not None:
							log.info(f'Regenerating the standalone media page for the RealAudio file "{realaudio_url}".')
							media_duration, media_title, media_author = generate_standalone_media_page(realaudio_url)
							wait_after_load = max(config.min_video_duration, min(media_duration + config.extra_standalone_media_wait_after_load, config.max_video_duration))

						user_wait_after_load = snapshot.Options.get('wait_after_load')
						if user_wait_after_load is not None:
							wait_after_load = max(config.min_video_duration, min(user_wait_after_load, config.max_video_duration))
							log.info(f'Setting the wait after load duration to {wait_after_load:.1f} at the user\'s request.')

						# Prepare the recording phase.

						subdirectory_path = config.get_recording_subdirectory_path(recording_id)
						os.makedirs(subdirectory_path, exist_ok=True)
						
						parts = urlparse(snapshot.Url)
						media_identifier = 's' if snapshot.IsStandaloneMedia else ('p' if snapshot.UsesPlugins else '')
						recording_identifiers = [str(recording_id), str(snapshot.Id), parts.hostname, snapshot.FileExtension, snapshot.Timestamp[:4], snapshot.Timestamp[4:6], snapshot.Timestamp[6:8], media_identifier]
						recording_path_prefix = os.path.join(subdirectory_path, '_'.join(filter(None, recording_identifiers)))

						browser.bring_to_front()
						pywinauto.mouse.move(coords=(0, config.physical_screen_height // 2))

						cosmo_player_viewpoint_cycler = CosmoPlayerViewpointCycler(browser.application, config.cosmo_player_viewpoint_wait_per_cycle) if config.cosmo_player_viewpoint_wait_per_cycle is not None else nullcontext()
						plugin_crash_timeout = config.max_video_duration + config.base_plugin_crash_timeout

						log.info(f'Waiting {wait_after_load:.1f} seconds after loading and then {wait_per_scroll:.1f} for each of the {num_scrolls_to_bottom} scrolls of {scroll_step:.1f} pixels.')
						browser.go_to_wayback_url(content_url)

						# Reloading the object, embed, and applet tags can yield good results when a page
						# uses various plugins that can potentially start playing at different times.
						if config.reload_plugin_media_before_recording:
							browser.reload_plugin_media()
				
						# Record the snapshot. The page should load faster now that its resources are cached.
						with cosmo_player_viewpoint_cycler, PluginCrashTimer(browser.firefox_directory_path, plugin_crash_timeout) as crash_timer, ScreenCapture(recording_path_prefix) as capture:
						
							time.sleep(wait_after_load)

							for i in range(num_scrolls_to_bottom):
								for _ in browser.traverse_frames():
									driver.execute_script('window.scrollBy({top: arguments[0], left: 0, behavior: "smooth"});', scroll_step)
								time.sleep(wait_per_scroll)

						# Check if the snapshot was redirected. See check_snapshot_redirection() in the
						# scout script for more details. This is good enough for the recorder script
						# because we're already filtering redirects in the scout, and because we're not
						# extracting any information from the page.
						redirected = not snapshot.IsStandaloneMedia and driver.current_url.lower() not in [content_url.lower(), unquote(content_url.lower())]
						driver.get(Browser.BLANK_URL)
						browser.close_all_windows_except(original_window)

						capture.perform_post_processing()
						
						if crash_timer.crashed or capture.failed or redirected:
							log.error(f'Aborted the recording (plugins crashed = {crash_timer.crashed}, capture failed = {capture.failed}, redirected = {redirected}).')
							state = Snapshot.ABORTED
						elif days_since_last_published is not None:
							log.info(f'Saved the new recording after {days_since_last_published} days to "{capture.archive_recording_path}".')
							state = Snapshot.APPROVED
						else:
							log.info(f'Saved the recording to "{capture.archive_recording_path}".')
							state = Snapshot.RECORDED

						if config.save_missing_proxy_snapshots_that_still_exist_online:
							
							# Remove any duplicates to minimize the amount of requests to the Save API
							# and to improve look up operations when trying to find other missing URLs.
							extra_missing_urls = {url: True for url in missing_urls}

							# Find other potentially missing URLs if the filename ends in a number.
							# If a file like "level3.dat" was missing, then we should check the
							# other values, both above and below 3.
							# E.g. https://web.archive.org/cdx/search/cdx?url=disciplinas.ist.utl.pt/leic-cg/materiais/VRML/cenas_vrml/cutplane/*&fl=original,timestamp,statuscode&collapse=urlkey
							for url in missing_urls:
								
								parts = urlparse(url)
								directory_path, filename = os.path.split(parts.path)

								match = Proxy.FILENAME_REGEX.fullmatch(filename)
								if match is None:
									continue

								log.debug(f'Filename match groups for the missing URL "{url}": {match.groups()}')

								name = match.group('name')
								padding = len(match.group('num'))
								extension = match.group('extension')

								num_consecutive_misses = 0
								for i in range(config.max_total_extra_missing_proxy_snapshot_tries):

									if num_consecutive_misses >= config.max_consecutive_extra_missing_proxy_snapshot_tries:
										break

									# Increment the value between the filename and extension.
									new_num = str(i).zfill(padding)
									new_filename = f'{name}{new_num}{extension}'
									new_path = urljoin(directory_path + '/', new_filename)
									new_parts = parts._replace(path=new_path)
									new_url = urlunparse(new_parts)

									if new_url in extra_missing_urls:
										continue

									if is_url_available(new_url):
										log.info(f'Found the consecutive missing URL "{new_url}".')
										extra_missing_urls[new_url] = True
										num_consecutive_misses = 0
									else:
										num_consecutive_misses += 1

									# Avoid making too many requests at once.
									time.sleep(1)
								else:
									# Avoid getting stuck in an infinite loop because the original
									# domain is parked, meaning there's potentially a valid response
									# for every possible consecutive number.
									log.warning(f'Stopping the search for more missing URLs after {config.max_total_extra_missing_proxy_snapshot_tries} tries.')

							missing_urls = list(extra_missing_urls)
							saved_urls = []
							num_processed = 0

							for i, url in enumerate(missing_urls):

								try:
									config.wait_for_save_api_rate_limit()
									save = WaybackMachineSaveAPI(url)
									wayback_url = save.save()

									if save.cached_save:
										log.info(f'The missing URL was already saved to "{wayback_url}"')
									else:
										log.info(f'Saved the missing URL to "{wayback_url}".')

										wayback_parts = parse_wayback_machine_snapshot_url(wayback_url)
										if wayback_parts is not None:
											url = wayback_parts.Url
											timestamp = wayback_parts.Timestamp
										else:
											timestamp = None

										saved_urls.append({'snapshot_id': snapshot.Id, 'url': url, 'timestamp': timestamp, 'failed': False})

								except TooManyRequestsError as error:
									log.error(f'Reached the Save API limit while trying to save the missing URL "{url}": {repr(error)}')
									break
								except Exception as error:
									log.error(f'Failed to save the missing URL "{url}" with the error: {repr(error)}')
									saved_urls.append({'snapshot_id': snapshot.Id, 'url': url, 'timestamp': None, 'failed': True})

								num_processed += 1

							if num_processed < len(missing_urls):

								remaining_missing_urls = missing_urls[i:]
								log.warning(f'Skipping {len(remaining_missing_urls)} missing URLs.')

								for url in remaining_missing_urls:
									saved_urls.append({'snapshot_id': snapshot.Id, 'url': url, 'timestamp': None, 'failed': True})

					except SessionNotCreatedException:
						log.warning('Terminated the WebDriver session abruptly.')
						break
					except WebDriverException as error:
						log.error(f'Failed to record the snapshot with the WebDriver error: {repr(error)}')
						abort_snapshot(snapshot)
						continue

					try:
						db.execute('UPDATE Snapshot SET State = :state WHERE Id = :id;', {'state': state, 'id': snapshot.Id})

						if state != Snapshot.ABORTED:
							
							archive_filename = os.path.basename(capture.archive_recording_path) if config.keep_archive_copy else None
							upload_filename = os.path.basename(capture.upload_recording_path)

							db.execute(	'''
										INSERT INTO Recording (SnapshotId, IsProcessed, ArchiveFilename, UploadFilename, CreationTime)
										VALUES (:snapshot_id, :is_processed, :archive_filename, :upload_filename, :creation_time);
										''', {'snapshot_id': snapshot.Id, 'is_processed': False, 'archive_filename': archive_filename,
											  'upload_filename': upload_filename, 'creation_time': get_current_timestamp()})

							if snapshot.Priority == Snapshot.RECORD_PRIORITY:
								db.execute('UPDATE Snapshot SET Priority = :no_priority WHERE Id = :id;', {'no_priority': Snapshot.NO_PRIORITY, 'id': snapshot.Id})

						else:
							delete_file(capture.archive_recording_path)
							delete_file(capture.upload_recording_path)

						if snapshot.IsStandaloneMedia:
							db.execute(	'UPDATE Snapshot SET MediaTitle = :media_title, MediaAuthor = :media_author WHERE Id = :id;',
										{'media_title': media_title, 'media_author': media_author, 'id': snapshot.Id})

						if config.save_missing_proxy_snapshots_that_still_exist_online:
							db.executemany(	'''
											INSERT INTO SavedSnapshotUrl (SnapshotId, Url, Timestamp, Failed)
											VALUES (:snapshot_id, :url, :timestamp, :failed)
											ON CONFLICT (Url)
											DO UPDATE SET Timestamp = :timestamp, Failed = :failed;
											''', saved_urls)

						db.commit()

					except sqlite3.Error as error:
						log.error(f'Failed to update the snapshot\'s status with the error: {repr(error)}')
						db.rollback()
						time.sleep(config.database_error_wait)
						continue
		
		except sqlite3.Error as error:
			log.error(f'Failed to connect to the database with the error: {repr(error)}')
		except KeyboardInterrupt:
			log.warning('Detected a keyboard interrupt when these should not be used to terminate the recorder due to a bug when using both Windows and the Firefox WebDriver.')
		finally:
			standalone_media_page_file.close()
			delete_file(standalone_media_page_file.name)
			
			try:
				standalone_media_download_directory.cleanup()
			except Exception as error:
				log.error(f'Failed to delete the temporary standalone media download directory with the error: {repr(error)}')

			if config.use_proxy:
				proxy.shutdown()

	####################################################################################################

	if args.max_iterations >= 0:
		record_snapshots(args.max_iterations)
	else:
		scheduler.add_job(record_snapshots, args=[config.num_snapshots_per_scheduled_batch], trigger='cron', coalesce=True, **config.scheduler, timezone='UTC')
		scheduler.start()

	log.info('Terminating the recorder.')