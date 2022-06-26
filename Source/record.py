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
from contextlib import nullcontext
from math import ceil
from queue import Queue
from random import random
from subprocess import PIPE, STDOUT
from subprocess import Popen, TimeoutExpired
from tempfile import NamedTemporaryFile
from threading import Thread, Timer
from typing import Dict, List, Optional, Pattern, Union, cast
from urllib.parse import urlparse

import ffmpeg # type: ignore
import pywinauto # type: ignore
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

	ranking_constant: float
	min_year: Optional[int]
	max_year: Optional[int]
	record_sensitive_snapshots: bool
	min_publish_days_for_new_recording: int

	allowed_standalone_media_file_extensions: Dict[str, bool] # Different from the config data type.

	use_proxy: bool
	proxy_port: Optional[int]
	proxy_queue_timeout: int
	proxy_total_timeout: int
	# Used in the Wayback Proxy Addon script.
	block_proxy_requests_outside_archive_org: bool
	find_missing_proxy_snapshots_using_cdx: bool
	max_missing_proxy_snapshot_path_components: Optional[int]
	# Used in both scripts.
	save_missing_proxy_snapshots_that_still_exist_online: bool 

	viewport_scroll_percentage: float
	page_cache_wait: int

	base_wait_after_load: int
	extra_wait_after_load: int

	base_wait_per_scroll: int
	extra_wait_per_scroll: int

	max_wait_after_load_video_percentage: float
	points_per_extra_wait: int

	standalone_media_fallback_duration: int
	standalone_media_width: str
	standalone_media_height: str
	standalone_media_background_color: str

	fullscreen_browser: bool
	reload_plugin_media_before_recording: bool
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
	plugin_crash_timeout: int
	standalone_media_template: str
	physical_screen_width: int
	physical_screen_height: int

	def __init__(self):
		super().__init__()
		self.load_subconfig('record')

		self.allowed_standalone_media_file_extensions = {extension: True for extension in container_to_lowercase(self.allowed_standalone_media_file_extensions)}

		if self.max_missing_proxy_snapshot_path_components is not None:
			self.max_missing_proxy_snapshot_path_components = max(self.max_missing_proxy_snapshot_path_components, 1)

		self.screen_capture_recorder_settings = container_to_lowercase(self.screen_capture_recorder_settings)

		self.ffmpeg_global = container_to_lowercase(self.ffmpeg_global)
		self.ffmpeg_recording_input = container_to_lowercase(self.ffmpeg_recording_input)
		self.ffmpeg_recording_output = container_to_lowercase(self.ffmpeg_recording_output)
		self.ffmpeg_archive_output = container_to_lowercase(self.ffmpeg_archive_output)
		self.ffmpeg_upload_output = container_to_lowercase(self.ffmpeg_upload_output)

		# This should be enough to work in both the caching and recordings phases.
		# We'll add some extra time to make sure that the recording isn't aborted
		# even when the video has the maximum duration.
		self.plugin_crash_timeout = max(self.page_cache_wait, self.max_video_duration) + 20

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

		SAVE_REGEX: Pattern = re.compile(r'\[SAVE\] \[(?P<url>.+)\]')

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
			log.info(f'Killing all plugin containers since {self.timeout:.1f} seconds have passed without the timer being reset.')
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
			log.debug(f'Created the temporary standalone media page file "{standalone_media_page_file.name}".')

			extra_preferences: dict = {
				'browser.cache.check_doc_frequency': 2, # Always use cached page.
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

				def rank_snapshot_by_points(points: int) -> float:
					""" Ranks a snapshot using a weighted random sampling algorithm. """
					# See:
					# - https://stackoverflow.com/a/56006340/18442724
					# - http://utopia.duth.gr/~pefraimi/research/data/2007EncOfAlg.pdf
					return random() ** (config.ranking_constant / (points + config.ranking_constant))

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
						if snapshot.IsStandaloneMedia:
							try:
								# For standalone media, we can potentially find out the duration of the audio or video file.
								probe = ffmpeg.probe(snapshot.WaybackUrl)
								standalone_media_duration = float(probe['format']['duration'])
								loop = 'false'
							except (ffmpeg.Error, KeyError, ValueError) as error:
								log.warning(f'Could not determine the standalone media\'s duration with the error: {repr(error)}')
								standalone_media_duration = config.standalone_media_fallback_duration
								loop = 'true'

							content = config.standalone_media_template
							content = content.replace('{comment}', f'Generated by "{__file__}" on {get_current_timestamp()}.')
							content = content.replace('{background_color}', config.standalone_media_background_color)
							content = content.replace('{width}', config.standalone_media_width)
							content = content.replace('{height}', config.standalone_media_height)
							content = content.replace('{url}', snapshot.WaybackUrl)
							content = content.replace('{loop}', loop)
							
							# Overwrite the temporary standalone media page.
							standalone_media_page_file.seek(0)
							standalone_media_page_file.truncate(0)
							standalone_media_page_file.write(content)
							standalone_media_page_file.flush()

							content_url = f'file:///{standalone_media_page_file.name}'
						else:
							content_url = snapshot.WaybackUrl

						log.info(f'[{snapshot_index+1} of {num_snapshots}] Recording snapshot #{snapshot.Id} {snapshot} ranked at {rank:.2f}% with {snapshot.Points} points (last published = {days_since_last_published}).')
						
						original_window = driver.current_window_handle
						browser.bring_to_front()
						pywinauto.mouse.move(coords=(0, config.physical_screen_height // 2))
						
						missing_urls: List[str] = []

						if config.use_proxy:
							proxy.timestamp = snapshot.Timestamp

						# Wait for the page and its resources to be cached.
						with proxy, PluginCrashTimer(browser.firefox_directory_path, config.plugin_crash_timeout) as crash_timer:

							browser.go_to_wayback_url(content_url)

							wait_after_load: float
							wait_per_scroll: float

							if snapshot.IsStandaloneMedia:
								cache_wait = standalone_media_duration
								scroll_step = 0.0
								num_scrolls_to_bottom = 0
								wait_after_load = max(config.min_video_duration, min(standalone_media_duration + 2, config.max_video_duration))
								wait_per_scroll = 0.0
							else:
								cache_wait = config.page_cache_wait

								scroll_height = 0
								for _ in browser.switch_through_frames():
									
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
								log.info('Waiting for the proxy.')
								begin_proxy_time = time.time()
								
								try:
									while True:
										
										elapsed_proxy_time = time.time() - begin_proxy_time
										if elapsed_proxy_time > config.proxy_total_timeout:
											log.debug('Timed out while reading proxy messages.')
											break

										message = proxy.get(timeout=config.proxy_queue_timeout)
										log.debug(message)
										
										if config.save_missing_proxy_snapshots_that_still_exist_online:
											
											match = Proxy.SAVE_REGEX.fullmatch(message)
											if match is not None:				
												url = match.group('url')
												missing_urls.append(url)
														
										proxy.task_done()

								except queue.Empty:
									log.debug('No more proxy messages.')
								finally:
									elapsed_proxy_time = time.time() - begin_proxy_time
									log.info(f'Waited {elapsed_proxy_time:.1f} extra seconds for the proxy.')

						subdirectory_path = config.get_recording_subdirectory_path(recording_id)
						os.makedirs(subdirectory_path, exist_ok=True)
						
						parts = urlparse(snapshot.Url)
						media_identifier = 's' if snapshot.IsStandaloneMedia else ('p' if snapshot.UsesPlugins else '')
						recording_identifiers = [str(recording_id), str(snapshot.Id), parts.hostname, snapshot.FileExtension, snapshot.Timestamp[:4], snapshot.Timestamp[4:6], snapshot.Timestamp[6:8], media_identifier]
						recording_path_prefix = os.path.join(subdirectory_path, '_'.join(filter(None, recording_identifiers)))

						browser.bring_to_front()
						pywinauto.mouse.move(coords=(0, config.physical_screen_height // 2))

						cosmo_player_viewpoint_cycler = CosmoPlayerViewpointCycler(browser.application, config.cosmo_player_viewpoint_wait_per_cycle) if config.cosmo_player_viewpoint_wait_per_cycle is not None else nullcontext()

						log.info(f'Waiting {wait_after_load:.1f} seconds after loading and then {wait_per_scroll:.1f} for each of the {num_scrolls_to_bottom} scrolls of {scroll_step:.1f} pixels.')
						browser.go_to_wayback_url(content_url)

						# Reloading the object, embed, and applet tags can yield good results when a page
						# uses various plugins that can potentially start playing at different times.
						if config.reload_plugin_media_before_recording:
							browser.reload_plugin_media()
				
						# Record the snapshot. The page should load faster now that its resources are cached.
						with cosmo_player_viewpoint_cycler, PluginCrashTimer(browser.firefox_directory_path, config.plugin_crash_timeout) as crash_timer, ScreenCapture(recording_path_prefix) as capture:
						
							time.sleep(wait_after_load)

							for i in range(num_scrolls_to_bottom):
								for _ in browser.switch_through_frames():
									driver.execute_script('window.scrollBy({top: arguments[0], left: 0, behavior: "smooth"});', scroll_step)
								time.sleep(wait_per_scroll)

						was_redirected = not snapshot.IsStandaloneMedia and content_url != driver.current_url
						driver.get(Browser.BLANK_URL)
						browser.close_all_windows_except(original_window)

						capture.perform_post_processing()
						
						if crash_timer.crashed or capture.failed or was_redirected:
							log.error(f'Aborted the recording (plugins crashed = {crash_timer.crashed}, capture failed = {capture.failed}, redirected = {was_redirected}).')
							state = Snapshot.ABORTED
						else:
							log.info(f'Saved the recording to "{capture.archive_recording_path}".')
							state = Snapshot.RECORDED

						if config.save_missing_proxy_snapshots_that_still_exist_online:
							
							# Remove any duplicates to minimize the amount of requests to the Save API.
							missing_urls = list(dict.fromkeys(missing_urls))
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
						continue

					try:
						db.execute('UPDATE Snapshot SET State = :state WHERE Id = :id;', {'state': state, 'id': snapshot.Id})

						if state == Snapshot.RECORDED:
							
							archive_filename = os.path.basename(capture.archive_recording_path) if config.keep_archive_copy else None
							upload_filename = os.path.basename(capture.upload_recording_path)

							db.execute(	'''
										INSERT INTO Recording (SnapshotId, IsProcessed, ArchiveFilename, UploadFilename, CreationTime)
										VALUES (:snapshot_id, :is_processed, :archive_filename, :upload_filename, :creation_time);
										''', {'snapshot_id': snapshot.Id, 'is_processed': False, 'archive_filename': archive_filename,
											  'upload_filename': upload_filename, 'creation_time': get_current_timestamp()})
						else:
							delete_file(capture.archive_recording_path)
							delete_file(capture.upload_recording_path)

						if snapshot.Priority == Snapshot.RECORD_PRIORITY:
							db.execute('UPDATE Snapshot SET Priority = :no_priority WHERE Id = :id;', {'no_priority': Snapshot.NO_PRIORITY, 'id': snapshot.Id})

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

			if config.use_proxy:
				proxy.shutdown()

	####################################################################################################

	if args.max_iterations >= 0:
		record_snapshots(args.max_iterations)
	else:
		scheduler.add_job(record_snapshots, args=[config.num_snapshots_per_scheduled_batch], trigger='cron', coalesce=True, **config.scheduler, timezone='UTC')
		scheduler.start()

	log.info('Terminating the recorder.')