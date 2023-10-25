#!/usr/bin/env python3

import ctypes
import os
import queue
import re
import sqlite3
import subprocess
import sys
from argparse import ArgumentParser
from collections import Counter, defaultdict
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import datetime
from glob import iglob
from math import ceil
from queue import Queue
from subprocess import CalledProcessError, DEVNULL, PIPE, Popen, STDOUT, TimeoutExpired
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Thread, Timer
from time import monotonic, sleep
from typing import BinaryIO, Optional, Union, cast
from urllib.parse import urljoin, urlparse, urlunparse

import ffmpeg # type: ignore
import pywinauto # type: ignore
import requests
from apscheduler.schedulers import SchedulerNotRunningError # type: ignore
from apscheduler.schedulers.blocking import BlockingScheduler # type: ignore
from pywinauto.application import WindowSpecification # type: ignore
from pywinauto.base_wrapper import ElementNotEnabled, ElementNotVisible # type: ignore
from requests import RequestException
from selenium.common.exceptions import SessionNotCreatedException, WebDriverException # type: ignore
from selenium.webdriver.common.utils import free_port # type: ignore
from waybackpy import WaybackMachineSaveAPI
from waybackpy.exceptions import TooManyRequestsError

from common import (
	Browser, CommonConfig, Database, Snapshot, TemporaryRegistry,
	clamp, container_to_lowercase, delete_file, global_rate_limiter,
	global_session, is_url_available, kill_processes_by_path,
	parse_wayback_machine_snapshot_url, setup_logger,
	was_exit_command_entered,
)

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
	raw_ffmpeg_input_args: dict[str, Union[None, int, str]]
	raw_ffmpeg_output_args: dict[str, Union[None, int, str]]
	archive_ffmpeg_output_args: dict[str, Union[None, int, str]]
	upload_ffmpeg_output_args: dict[str, Union[None, int, str]]

	enable_text_to_speech: bool
	text_to_speech_audio_format_type: Optional[str]
	text_to_speech_rate: Optional[int]
	text_to_speech_default_voice: Optional[str]
	text_to_speech_language_voices: dict[str, str]

	text_to_speech_ffmpeg_video_input_name: str
	text_to_speech_ffmpeg_video_input_args: dict[str, Union[None, int, str]]
	text_to_speech_ffmpeg_audio_input_args: dict[str, Union[None, int, str]]
	text_to_speech_ffmpeg_output_args: dict[str, Union[None, int, str]]

	enable_media_conversion: bool
	convertible_media_extensions: frozenset[str] # Different from the config data type.
	media_conversion_ffmpeg_input_name: str
	media_conversion_ffmpeg_input_args: dict[str, Union[None, int, str]]
	media_conversion_add_subtitles: bool
	media_conversion_ffmpeg_subtitles_style: str

	# Determined at runtime.
	media_template: str
	physical_screen_width: int
	physical_screen_height: int
	width_dpi_scaling: float
	height_dpi_scaling: float

	def __init__(self):
		
		super().__init__()
		self.load_subconfig('record')

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
		
		self.raw_ffmpeg_input_args = container_to_lowercase(self.raw_ffmpeg_input_args)
		self.raw_ffmpeg_output_args = container_to_lowercase(self.raw_ffmpeg_output_args)
		self.archive_ffmpeg_output_args = container_to_lowercase(self.archive_ffmpeg_output_args)
		self.upload_ffmpeg_output_args = container_to_lowercase(self.upload_ffmpeg_output_args)

		self.text_to_speech_language_voices = container_to_lowercase(self.text_to_speech_language_voices)

		self.text_to_speech_ffmpeg_video_input_args = container_to_lowercase(self.text_to_speech_ffmpeg_video_input_args)
		self.text_to_speech_ffmpeg_audio_input_args = container_to_lowercase(self.text_to_speech_ffmpeg_audio_input_args)
		self.text_to_speech_ffmpeg_output_args = container_to_lowercase(self.text_to_speech_ffmpeg_output_args)

		self.convertible_media_extensions = frozenset(extension for extension in container_to_lowercase(self.convertible_media_extensions))
		assert self.convertible_media_extensions.issubset(self.allowed_media_extensions), 'The convertible media extensions must be a subset of the allowed media extensions.'

		assert self.multi_asset_media_extensions.isdisjoint(self.convertible_media_extensions), 'The multi-asset and convertible media extensions must be mutually exclusive.'

		self.media_conversion_ffmpeg_input_args = container_to_lowercase(self.media_conversion_ffmpeg_input_args)

		media_template_path = os.path.join(self.plugins_path, 'media.html.template')
		with open(media_template_path, 'r', encoding='utf-8') as file:
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
		subprocess.run(['ffmpeg', '-version'], check=True, stdout=DEVNULL)
	except CalledProcessError:
		log.error('Could not find the FFmpeg executable in the PATH.')
		sys.exit(1)

	class Proxy(Thread):
		""" A proxy thread that intercepts all HTTP/HTTPS requests made by Firefox and its plugins. Used to locate missing resources
		in other subdomains via the CDX API while also allowing plugin media that loads slowly to finish requesting assets. """

		port: int
		
		process: Popen
		queue: Queue
		timestamp: Optional[str]

		RESPONSE_REGEX = re.compile(r'\[RESPONSE\] \[(?P<status_code>.+)\] \[(?P<mark>.+)\] \[(?P<content_type>.+)\] \[(?P<url>.+)\] \[(?P<id>.+)\]')
		SAVE_REGEX  = re.compile(r'\[SAVE\] \[(?P<url>.+)\]')
		REALMEDIA_REGEX  = re.compile(r'\[RAM\] \[(?P<url>.+)\]')
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
			""" Creates the proxy while handling any errors at startup (e.g. Python errors or already used ports). """

			port = free_port() if config.proxy_port is None else config.proxy_port

			while True:
				try:
					log.info(f'Creating the proxy on port {port}.')
					proxy = Proxy(port)

					error = proxy.get(timeout=10)
					log.error(f'Failed to create the proxy with the error: {error}')
					proxy.task_done()
					
					proxy.process.kill()
					sleep(5)
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

	class PluginCrashTimer:
		""" A special timer that kills Firefox's plugin container child processes after a given time has elapsed (e.g. the recording duration). """

		timeout: float
		
		plugin_container_path: str
		java_plugin_launcher_path: Optional[str]
		timer: Timer
		crashed: bool

		def __init__(self, browser: Browser, timeout: float):
			
			self.timeout = timeout
			
			self.plugin_container_path = os.path.join(browser.firefox_directory_path, 'plugin-container.exe')
			self.java_plugin_launcher_path = os.path.join(browser.java_bin_path, 'jp2launcher.exe') if browser.java_bin_path is not None else None

			self.timer = Timer(self.timeout, self.kill_plugin_containers)

			log.debug(f'Created a plugin crash timer with {self.timeout:.1f} seconds.')

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
			
			if self.java_plugin_launcher_path is not None:
				kill_processes_by_path(self.java_plugin_launcher_path)
			
			kill_processes_by_path(self.plugin_container_path)

	class ScreenCapture:
		""" A process that captures the screen and stores the recording on disk using FFmpeg. """

		raw_path: str
		upload_path: str
		archive_path: Optional[str]
		
		stream: ffmpeg.Stream
		process: Popen
		failed: bool

		def __init__(self, output_path_prefix: str):
			
			self.raw_path = output_path_prefix + '.raw.mkv'
			self.upload_path = output_path_prefix + '.mp4'
			self.archive_path = output_path_prefix + '.mkv' if config.save_archive_copy else None

			stream = ffmpeg.input(config.raw_ffmpeg_input_name, t=config.max_duration, **config.raw_ffmpeg_input_args)
			stream = stream.output(self.raw_path, **config.raw_ffmpeg_output_args)
			stream = stream.global_args(*config.ffmpeg_global_args)
			stream = stream.overwrite_output()
			self.stream = stream

		def start(self) -> None:
			""" Starts the FFmpeg screen capture process asynchronously. """

			log.debug(f'Recording with the FFmpeg arguments: {self.stream.get_args()}')
			self.failed = False
			
			# Connecting a pipe to stdin is required to stop the recording by pressing Q.
			# See: https://github.com/kkroening/ffmpeg-python/issues/162
			# Connecting a pipe to stdout and stderr is useful to check for any FFmpeg error messages.
			self.process = self.stream.run_async(pipe_stdin=True, pipe_stdout=True, pipe_stderr=True)		

		def stop(self) -> None:
			""" Stops the FFmpeg screen capture process gracefully or kills it doesn't respond. """

			try:
				output, errors = self.process.communicate(b'q', timeout=10)
				
				for line in output.decode(errors='ignore').splitlines():
					log.info(f'FFmpeg output: {line}')
				
				for line in errors.decode(errors='ignore').splitlines():
					log.warning(f'FFmpeg warning/error: {line}')
			
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

				output_types = [(self.upload_path, config.upload_ffmpeg_output_args)]
				
				if self.archive_path is not None:
					output_types.append((self.archive_path, config.archive_ffmpeg_output_args))

				for output_path, output_args in output_types:

					stream = ffmpeg.input(self.raw_path)
					stream = stream.output(output_path, **output_args)
					stream = stream.global_args(*config.ffmpeg_global_args)
					stream = stream.overwrite_output()

					try:
						log.debug(f'Processing the recording with the FFmpeg arguments: {stream.get_args()}')
						stream.run()
					except ffmpeg.Error as error:
						log.error(f'Failed to process "{self.raw_path}" into "{output_path}" with the error: {repr(error)}')
						self.failed = True
						break
			
			delete_file(self.raw_path)

	if config.enable_text_to_speech:
		
		from comtypes import COMError # type: ignore
		from comtypes.client import CreateObject # type: ignore
		
		# We need to create a speech engine at least once before importing SpeechLib. Otherwise, we'd get an ImportError.
		CreateObject('SAPI.SpVoice')
		from comtypes.gen import SpeechLib # type: ignore

		class TextToSpeech:
			""" A wrapper for the Microsoft Speech API and FFmpeg that generates a text-to-speech recording. """

			engine: SpeechLib.ISpeechVoice
			stream: SpeechLib.ISpeechFileStream
			temporary_file: BinaryIO

			language_to_voice: dict[Optional[str], SpeechLib.ISpeechObjectToken]

			def __init__(self):
				
				# See:
				# - https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ms723602(v=vs.85)
				# - https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ms722561(v=vs.85)
				self.engine = CreateObject('SAPI.SpVoice')
				self.stream = CreateObject('SAPI.SpFileStream')
				
				# We have to close the temporary file so SpFileStream.Open() doesn't fail.
				self.temporary_file = NamedTemporaryFile(mode='wb', prefix=CommonConfig.TEMPORARY_PATH_PREFIX, suffix='.wav', delete=False)
				self.temporary_file.close()

				try:
					if config.text_to_speech_audio_format_type is not None:
						self.engine.AllowOutputFormatChangesOnNextSet = False
						self.stream.Format.Type = getattr(SpeechLib, config.text_to_speech_audio_format_type)
				except AttributeError:
					log.error(f'Could not find the audio format type "{config.text_to_speech_audio_format_type}".')

				if config.text_to_speech_rate is not None:
					self.engine.Rate = config.text_to_speech_rate

				voices = {}
				for voice in self.engine.GetVoices():
					name = voice.GetAttribute('Name')
					voices[name] = voice

					language = voice.GetAttribute('Language')
					gender = voice.GetAttribute('Gender')
					age = voice.GetAttribute('Age')
					vendor = voice.GetAttribute('Vendor')
					description = voice.GetDescription()
					log.info(f'Found the text-to-speech voice ({name}, {language}, {gender}, {age}, {vendor}): "{description}".')
					
				default_voice = self.engine.Voice
				if config.text_to_speech_default_voice is not None:
					default_voice = next((voice for name, voice in voices.items() if config.text_to_speech_default_voice.lower() in name.lower()), default_voice)
					
				self.language_to_voice = defaultdict(lambda: default_voice)
				
				for language, voice_name in config.text_to_speech_language_voices.items():
					voice = next((voice for name, voice in voices.items() if voice_name.lower() in name.lower()), None)
					if voice is not None:
						self.language_to_voice[language] = voice

			def generate_text_to_speech_file(self, title: str, date: datetime, text: str, language: Optional[str], output_path_prefix: str) -> Optional[str]:
				""" Generates a video file that contains the text-to-speech in the audio track and a blank screen on the video one.
				The voice used by the Speech API is specified in the configuration file and depends on the page's language.
				The correct voice packages have been installed on Windows, otherwise a default voice is used instead. """
				
				# Add some context XML so the date is spoken correctly no matter the language.
				# See: https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ee125665(v=vs.85)
				date_xml = f'<context id="date_ymd">{date.year}/{date.month}/{date.day}</context>'
				
				output_path: Optional[str] = output_path_prefix + (f'.tts.{language}.mp4' if language is not None else '.tts.mp4')

				try:
					# See:
					# - https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ms720858(v=vs.85)
					# - https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ms720892(v=vs.85)
					self.stream.Open(self.temporary_file.name, SpeechLib.SSFMCreateForWrite)
					self.engine.AudioOutputStream = self.stream
					self.engine.Voice = self.language_to_voice[language]
					self.engine.Speak(title, SpeechLib.SVSFPurgeBeforeSpeak | SpeechLib.SVSFIsNotXML)
					self.engine.Speak(date_xml, SpeechLib.SVSFPurgeBeforeSpeak | SpeechLib.SVSFIsXML)
					self.engine.Speak(text, SpeechLib.SVSFPurgeBeforeSpeak | SpeechLib.SVSFIsNotXML)
					self.stream.Close()

					video_stream = ffmpeg.input(config.text_to_speech_ffmpeg_video_input_name, **config.text_to_speech_ffmpeg_video_input_args)
					audio_stream = ffmpeg.input(self.temporary_file.name, **config.text_to_speech_ffmpeg_audio_input_args)

					target_stream = ffmpeg.output(video_stream, audio_stream, output_path, **config.text_to_speech_ffmpeg_output_args)
					target_stream = target_stream.global_args(*config.ffmpeg_global_args)
					target_stream = target_stream.overwrite_output()
					
					log.debug(f'Generating the text-to-speech file with the FFmpeg arguments: {target_stream.get_args()}')
					target_stream.run()

				except (COMError, ffmpeg.Error) as error:
					log.error(f'Failed to generate the text-to-speech file "{output_path}" with the error: {repr(error)}')
					output_path = None

				return output_path

			def cleanup(self) -> None:
				""" Deletes the temporary WAV file created by the Speech API. """
				delete_file(self.temporary_file.name)

	class PluginInputRepeater(Thread):
		""" A thread that periodically interacts with any plugin instance running in Firefox. """

		firefox_window: Optional[WindowSpecification]
		running: bool

		def __init__(self, firefox_window: Optional[WindowSpecification], thread_name='plugin_input_repeater'):

			super().__init__(name=thread_name, daemon=True)
			
			self.firefox_window = firefox_window
			self.running = False

		def run(self):
			""" Runs the input repeater on a loop, sending a series of keystrokes periodically to any Firefox plugin windows. """

			if self.firefox_window is None:
				return

			first = True

			while self.running:
				
				if first:
					sleep(config.plugin_input_repeater_initial_wait)
				else:
					sleep(config.plugin_input_repeater_wait_per_cycle)

				first = False

				try:
					# For the Flash Player and any other plugins that use the generic window.
					# Note that this feature might not work when running in a remote machine that was connected via VNC.
					# Interacting with Java applets and VRML worlds should still work though.
					# E.g. https://web.archive.org/web/20010306033409if_/http://www.big.or.jp/~frog/others/button1.html
					# E.g. https://web.archive.org/web/20030117223552if_/http://www.miniclip.com:80/dancingbush.htm
					plugin_windows = self.firefox_window.children(class_name='GeckoPluginWindow')
					
					# For the Shockwave Player.
					# No known examples at the time of writing.
					plugin_windows += self.firefox_window.children(class_name='ImlWinCls')
					plugin_windows += self.firefox_window.children(class_name='ImlWinClsSw10')
					
					# For the Java Plugin.
					# E.g. https://web.archive.org/web/20050901064800if_/http://www.javaonthebrain.com/java/iceblox/
					# E.g. https://web.archive.org/web/19970606032004if_/http://www.brown.edu:80/Students/Japanese_Cultural_Association/java/
					plugin_windows += self.firefox_window.children(class_name='SunAwtCanvas')
					plugin_windows += self.firefox_window.children(class_name='SunAwtFrame')

					for window in plugin_windows:
						try:
							rect = window.rectangle()
							width = round(rect.width() / config.width_dpi_scaling)
							height = round(rect.height() / config.height_dpi_scaling)
							interactable = width >= config.plugin_input_repeater_min_window_size and height >= config.plugin_input_repeater_min_window_size

							if interactable:
								window.click()
								window.send_keystrokes(config.plugin_input_repeater_keystrokes)

							if config.debug and config.plugin_input_repeater_debug:
								color = 'green' if interactable else 'red'
								window.draw_outline(color)
								window.debug_message(f'{width}x{height}')

						except (ElementNotEnabled, ElementNotVisible):
							log.debug('Skipping a disabled or hidden window.')

				except Exception as error:
					log.error(f'Failed to send the input to the plugin windows with the error: {repr(error)}')

		def startup(self) -> None:
			""" Starts the input repeater thread. """
			self.running = True
			self.start()

		def shutdown(self) -> None:
			""" Stops the input repeater thread. """
			self.running = False
			self.join()

		def __enter__(self):
			self.startup()
			return self

		def __exit__(self, exception_type, exception_value, traceback):
			self.shutdown()

	class CosmoPlayerViewpointCycler(PluginInputRepeater):
		""" A thread that periodically tells any VRML world running in the Cosmo Player to move to the next viewpoint. """

		def __init__(self, firefox_window: Optional[WindowSpecification]):
			super().__init__(firefox_window, thread_name='cosmo_player_viewpoint_cycler')

		def run(self):
			""" Runs the viewpoint cycler on a loop, sending the "Next Viewpoint" hotkey periodically to any Cosmo Player windows. """
			
			if self.firefox_window is None:
				return

			while self.running:
				
				sleep(config.cosmo_player_viewpoint_wait_per_cycle)

				try:
					# E.g. https://web.archive.org/web/19970713113545if_/http://www.hedges.org:80/thehedges/Ver21.wrl
					# E.g. https://web.archive.org/web/20220616010004if_/http://disciplinas.ist.utl.pt/leic-cg/materiais/VRML/cenas_vrml/golf/golf.wrl
					cosmo_player_windows = self.firefox_window.children(class_name='CpWin32RenderWindow')
					for window in cosmo_player_windows:
						window.send_keystrokes('{PGDN}')
				except Exception as error:
					log.error(f'Failed to send the input to the Cosmo Player windows with the error: {repr(error)}')				

	scheduler = BlockingScheduler()

	def record_snapshots(num_snapshots: int) -> None:
		""" Records a given number of snapshots in a single batch. """
		
		log.info(f'Recording {num_snapshots} snapshots.')

		if config.enable_proxy:
			log.info('Initializing the proxy.')
			proxy = Proxy.create()
		else:
			proxy = nullcontext() # type: ignore

		if config.enable_text_to_speech:
			log.info('Initializing the text-to-speech engine.')
			text_to_speech = TextToSpeech()

		media_page_file = NamedTemporaryFile(mode='w', encoding='utf-8', prefix=CommonConfig.TEMPORARY_PATH_PREFIX, suffix='.html', delete=False)
		media_page_url = f'file:///{media_page_file.name}'
		log.debug(f'Created the temporary media page "{media_page_file.name}".')

		media_download_directory = TemporaryDirectory(prefix=CommonConfig.TEMPORARY_PATH_PREFIX, suffix='.media')
		media_download_search_path = os.path.join(media_download_directory.name, '*')
		log.debug(f'Created the temporary media download directory "{media_download_directory.name}".')

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

				def generate_media_page(wayback_url: str, media_extension: Optional[str] = None) -> tuple[bool, Optional[str], str, float, Optional[str], Optional[str]]:
					""" Generates the page where a media file is embedded using both the information from the configuration as well as the file's metadata. """

					success = True
					download_path = None

					wayback_parts = parse_wayback_machine_snapshot_url(wayback_url)
					parts = urlparse(wayback_parts.url if wayback_parts is not None else wayback_url)
					filename = os.path.basename(parts.path)
						
					if media_extension is None:
						_, media_extension = os.path.splitext(filename)
						media_extension = media_extension.lower().removeprefix('.')

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
							download_path = os.path.join(media_download_directory.name, filename)
							with open(download_path, 'wb') as file:
								file.write(response.content)
						
							log.debug(f'Downloaded the media file "{wayback_url}" to "{download_path}".')
							
							embed_url = f'file:///{download_path}'
							loop = 'false'

							probe = ffmpeg.probe(download_path)

							# See: https://wiki.multimedia.cx/index.php/FFmpeg_Metadata
							tags = probe['format'].get('tags', {})
							title = tags.get('title')
							author = tags.get('author') or tags.get('artist') or tags.get('album_artist') or tags.get('composer') or tags.get('copyright')
							log.debug(f'The media file "{title}" by "{author}" has the following tags: {tags}')

							duration = float(probe['format']['duration'])
							log.debug(f'The media file has a duration of {duration:.2f} seconds.')
						
						except RequestException as error:
							log.error(f'Failed to download the media file "{wayback_url}" with the error: {repr(error)}')
							success = False
						except (ffmpeg.Error, KeyError, ValueError) as error:
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
							framerate = config.raw_ffmpeg_input_args.get('framerate', 60)
							registry_value = cast(int, framerate)
						else:
							registry.delete(registry_key)
							continue
					else:
						registry_value = value
					
					registry.set(registry_key, registry_value)
				
				# E.g. "[silencedetect @ 0000022c2f32bf40] silence_end: 4.54283 | silence_duration: 0.377167"
				SILENCE_DURATION_REGEX = re.compile(r'^\[silencedetect.*silence_duration: (?P<duration>\d+\.\d+)', re.MULTILINE)

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

						for path in iglob(media_download_search_path):
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
						realmedia_url = None

						# Wait for the page and its resources to be cached.
						with proxy, PluginCrashTimer(browser, plugin_crash_timeout):

							try:
								browser.bring_to_front()
								pywinauto.mouse.move((0, config.physical_screen_height // 2))
							except Exception as error:
								log.error(f'Failed to focus on the browser window and move the mouse with the error: {repr(error)}')

							browser.go_to_wayback_url(content_url, close_windows=True)

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

									for url in browser.get_playback_plugin_sources():
										try:
											probe = ffmpeg.probe(url)
											duration = float(probe['format']['duration'])
											if max_plugin_duration is not None:
												max_plugin_duration = max(max_plugin_duration, duration)
											else:
												max_plugin_duration = duration
										except (ffmpeg.Error, KeyError, ValueError) as error:
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

						plugin_input_repeater: Union[PluginInputRepeater, AbstractContextManager[None]] = PluginInputRepeater(browser.window) if config.enable_plugin_input_repeater else nullcontext()
						cosmo_player_viewpoint_cycler: Union[CosmoPlayerViewpointCycler, AbstractContextManager[None]] = CosmoPlayerViewpointCycler(browser.window) if config.enable_cosmo_player_viewpoint_cycler else nullcontext()

						subdirectory_path = config.get_recording_subdirectory_path(recording_id)
						os.makedirs(subdirectory_path, exist_ok=True)

						parts = urlparse(snapshot.Url)
						media_identifier = snapshot.MediaExtension if snapshot.IsMedia else ('p' if snapshot.PageUsesPlugins else None)
						recording_identifiers = [str(recording_id), str(snapshot.Id), parts.hostname, str(snapshot.OldestDatetime.year), str(snapshot.OldestDatetime.month).zfill(2), str(snapshot.OldestDatetime.day).zfill(2), media_identifier]
						recording_path_prefix = os.path.join(subdirectory_path, '_'.join(filter(None, recording_identifiers)))

						upload_path: str
						archive_path: Optional[str]
						text_to_speech_path: Optional[str]

						# This media extension differs from the snapshot's extension when recording a RealMedia file
						# whose URL was extracted from a metadata file. We should only be converting binary media,
						# and not text files like playlists or metadata.
						if config.enable_media_conversion and snapshot.IsMedia and media_extension in config.convertible_media_extensions and media_path is not None:

							# Convert a media snapshot directly and skip capturing the screen.

							log.info(f'Converting the media file "{os.path.basename(media_path)}".')

							browser.close_all_windows()
							browser.go_to_blank_page_with_text('\N{DNA Double Helix} Converting Media \N{DNA Double Helix}', str(snapshot))

							upload_path = recording_path_prefix + '.mp4'
							archive_path = None
							text_to_speech_path = None

							try:
								probe = ffmpeg.probe(media_path)
								has_video_stream = any(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
							
								# Add a video stream to the recording if the media file doesn't have one.
								media_stream = ffmpeg.input(media_path, guess_layout_max=0)
								video_stream = None if has_video_stream else ffmpeg.input(config.media_conversion_ffmpeg_input_name, **config.media_conversion_ffmpeg_input_args)
								input_streams: list[ffmpeg.Stream] = list(filter(None, [media_stream, video_stream]))
								
								if config.media_conversion_add_subtitles and not has_video_stream:

									log.debug('Adding subtitles to the converted media file.')

									preposition = 'by' if media_author is not None else None
									subtitles = '\n'.join(filter(None, [snapshot.DisplayTitle, media_title, preposition, media_author]))

									# Set a high enough duration so the subtitles last the entire recording.
									subtitles_file.seek(0)
									subtitles_file.truncate(0)
									subtitles_file.write(f'1\n00:00:00,000 --> 99:00:00,000\n{subtitles}')
									subtitles_file.flush()

									# Take into account any previous filters from the configuration file.
									output_args = config.upload_ffmpeg_output_args.copy()
									subtitles_filter = f"subtitles='{escaped_subtitles_path}':force_style='{config.media_conversion_ffmpeg_subtitles_style}'"

									if 'vf' in output_args:
										output_args['vf'] += ',' + subtitles_filter # type: ignore
									else:
										output_args['vf'] = subtitles_filter
								else:
									output_args = config.upload_ffmpeg_output_args

								stream = ffmpeg.output(*input_streams, upload_path, t=config.max_duration, shortest=None, **output_args)
								stream = stream.global_args(*config.ffmpeg_global_args)
								stream = stream.overwrite_output()

								log.debug(f'Converting the media file with the FFmpeg arguments: {stream.get_args()}')
								output, errors = stream.run(capture_stdout=True, capture_stderr=True)

								log.info(f'Saved the media conversion to "{upload_path}".')
								state = Snapshot.RECORDED

								for line in output.decode(errors='ignore').splitlines():
									log.info(f'FFmpeg output: {line}')
								
								for line in errors.decode(errors='ignore').splitlines():
									log.warning(f'FFmpeg warning/error: {line}')

							except ffmpeg.Error as error:
								log.error(f'Aborted the media conversion with the error: {repr(error)}.')
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
								browser.go_to_wayback_url(content_url, close_windows=True)

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

								with plugin_input_repeater, cosmo_player_viewpoint_cycler, ScreenCapture(recording_path_prefix) as capture:

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
								text_to_speech_path = text_to_speech.generate_text_to_speech_file(snapshot.DisplayTitle, snapshot.OldestDatetime, page_text, snapshot.PageLanguage, recording_path_prefix)

								if text_to_speech_path is not None:
									log.info(f'Saved the text-to-speech file to "{text_to_speech_path}".')
						
						has_audio = False

						if state == Snapshot.RECORDED:

							browser.go_to_blank_page_with_text('\N{Speaker With Cancellation Stroke} Detecting Silence \N{Speaker With Cancellation Stroke}', str(snapshot))

							try:
								probe = ffmpeg.probe(upload_path)
								recording_duration = float(probe['format']['duration'])
								has_audio_stream = any(stream for stream in probe['streams'] if stream['codec_type'] == 'audio')

								# We'll use our own global arguments since we need the log level set to
								# info in order to get the filter's output. The minimum silence duration
								# should be under one second so we can detect audio in short media files.
								# E.g. https://web.archive.org/web/19961106150353if_/http://www.dnai.com:80/~sharrow/wav/frog.wav
								stream = ffmpeg.input(upload_path)
								stream = stream.output('-', f='null', af='silencedetect=duration=0.1')
								stream = stream.global_args('-hide_banner', '-nostats')

								# The filter's output goes to stderr.
								log.debug(f'Detecting silence with the FFmpeg arguments: {stream.get_args()}')
								_, errors = stream.run(capture_stderr=True)
								
								output = errors.decode(errors='ignore')
								match = SILENCE_DURATION_REGEX.search(output)

								# From testing, the difference between the durations in silent recordings is
								# usually under 0.1 seconds, so we'll increase this threshold for good measure.
								# If FFmpeg couldn't detect any silence, we still have to check if the recording
								# has an audio stream because we might have converted a video-only media file.
								# E.g. https://web.archive.org/web/19970119195540if_/http://www.gwha.com:80/dynimg/lapse.mpeg
								if match is not None:
									silence_duration = float(match['duration'])
									has_audio = abs(recording_duration - silence_duration) > 0.2
									log.debug(f'Detected {silence_duration:.2f} seconds of silence out of {recording_duration:.2f}.')
								else:
									has_audio = has_audio_stream
									log.debug(f'No silence detected (audio stream = {has_audio_stream}).')

							except (ffmpeg.Error, KeyError, ValueError) as error:
								log.error(f'Could not detect silence with the error: {repr(error)}')

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
								directory_path, filename = os.path.split(parts.path)

								match = Proxy.FILENAME_REGEX.fullmatch(filename)
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
									new_path = urljoin(directory_path + '/', new_filename)
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
							
							upload_filename = os.path.basename(upload_path)
							archive_filename = os.path.basename(archive_path) if archive_path is not None else None
							text_to_speech_filename = os.path.basename(text_to_speech_path) if text_to_speech_path is not None else None

							db.execute(	'''
										INSERT INTO Recording (SnapshotId, HasAudio, UploadFilename, ArchiveFilename, TextToSpeechFilename)
										VALUES (:snapshot_id, :has_audio, :upload_filename, :archive_filename, :text_to_speech_filename);
										''',
										{'snapshot_id': snapshot.Id, 'has_audio': has_audio, 'upload_filename': upload_filename,
										 'archive_filename': archive_filename, 'text_to_speech_filename': text_to_speech_filename})

							if snapshot.Priority == Snapshot.RECORD_PRIORITY:
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
			raw_search_path = os.path.join(config.recordings_path, '**', '*.raw.mkv')
			for path in iglob(raw_search_path, recursive=True):
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