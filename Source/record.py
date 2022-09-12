#!/usr/bin/env python3

import ctypes
import itertools
import os
import queue
import re
import sqlite3
import subprocess
import sys
import time
from argparse import ArgumentParser
from collections import Counter, defaultdict
from contextlib import nullcontext
from glob import iglob
from math import ceil
from queue import Queue
from subprocess import CalledProcessError, DEVNULL, PIPE, Popen, STDOUT, TimeoutExpired
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Thread, Timer
from typing import BinaryIO, Dict, Iterator, List, Optional, Tuple, Union, cast
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import ffmpeg # type: ignore
import pywinauto # type: ignore
import requests
from apscheduler.schedulers import SchedulerNotRunningError # type: ignore
from apscheduler.schedulers.blocking import BlockingScheduler # type: ignore
from pywinauto.application import WindowSpecification # type: ignore
from pywinauto.base_wrapper import ElementNotEnabled, ElementNotVisible # type: ignore
from selenium.common.exceptions import SessionNotCreatedException, WebDriverException # type: ignore
from selenium.webdriver.common.utils import free_port # type: ignore
from waybackpy import WaybackMachineSaveAPI
from waybackpy.exceptions import TooManyRequestsError

from common import TEMPORARY_PATH_PREFIX, Browser, CommonConfig, Database, Snapshot, TemporaryRegistry, clamp, container_to_lowercase, delete_file, get_current_timestamp, global_rate_limiter, is_url_available, kill_processes_by_path, parse_wayback_machine_snapshot_url, setup_logger, was_exit_command_entered

class RecordConfig(CommonConfig):
	""" The configuration that applies to the recorder script. """

	# From the config file.
	scheduler: Dict[str, Union[int, str]]
	num_snapshots_per_scheduled_batch: int

	ranking_offset: Optional[int]
	min_year: Optional[int]
	max_year: Optional[int]
	record_sensitive_snapshots: bool
	min_publish_days_for_new_recording: int

	allowed_standalone_media_extensions: Dict[str, bool] # Different from the config data type.
	nondownloadable_standalone_media_extensions: Dict[str, bool] # Different from the config data type.

	enable_proxy: bool
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
	cache_missing_proxy_responses: bool

	page_plugin_wait: int
	page_cache_wait: int
	standalone_media_cache_wait: int

	viewport_scroll_percentage: float
	base_wait_after_load: int
	wait_after_load_per_plugin_instance: int
	base_wait_per_scroll: int
	wait_after_scroll_per_plugin_instance: int
	base_standalone_media_wait_after_load: int

	standalone_media_fallback_duration: int
	standalone_media_width: str
	standalone_media_height: str
	standalone_media_background_color: str

	fullscreen_browser: bool
	reload_plugin_media_before_recording: bool
	base_plugin_crash_timeout: int
	
	enable_plugin_input_repeater: bool
	plugin_input_repeater_initial_wait: int
	plugin_input_repeater_wait_per_cycle: int
	min_plugin_input_repeater_window_size: int
	plugin_input_repeater_keystrokes: str
	plugin_input_repeater_debug_highlight: bool

	enable_cosmo_player_viewpoint_cycler: bool
	cosmo_player_viewpoint_wait_per_cycle: int

	min_video_duration: int
	max_video_duration: int
	keep_archive_copy: bool
	screen_capture_recorder_settings: Dict[str, Optional[int]]
	
	ffmpeg_recording_input_name: str
	ffmpeg_recording_input_args: Dict[str, Union[int, str]]
	ffmpeg_recording_output_args: Dict[str, Union[int, str]]
	ffmpeg_archive_output_args: Dict[str, Union[int, str]]
	ffmpeg_upload_output_args: Dict[str, Union[int, str]]

	enable_text_to_speech: bool
	text_to_speech_audio_format_type: Optional[str]
	text_to_speech_rate: Optional[int]
	text_to_speech_default_voice: Optional[str]
	text_to_speech_language_voices: Dict[str, str]

	ffmpeg_text_to_speech_video_input_name: str
	ffmpeg_text_to_speech_video_input_args: Dict[str, Union[int, str]]
	ffmpeg_text_to_speech_audio_input_args: Dict[str, Union[int, str]]
	ffmpeg_text_to_speech_output_args: Dict[str, Union[int, str]]

	# Determined at runtime.
	standalone_media_template: str
	physical_screen_width: int
	physical_screen_height: int
	width_dpi_scaling: float
	height_dpi_scaling: float

	def __init__(self):
		super().__init__()
		self.load_subconfig('record')

		self.scheduler = container_to_lowercase(self.scheduler)

		self.allowed_standalone_media_extensions = {extension: True for extension in container_to_lowercase(self.allowed_standalone_media_extensions)}
		self.nondownloadable_standalone_media_extensions = {extension: True for extension in container_to_lowercase(self.nondownloadable_standalone_media_extensions)}

		if self.max_missing_proxy_snapshot_path_components is not None:
			self.max_missing_proxy_snapshot_path_components = max(self.max_missing_proxy_snapshot_path_components, 1)

		self.screen_capture_recorder_settings = container_to_lowercase(self.screen_capture_recorder_settings)
		
		self.ffmpeg_recording_input_args = container_to_lowercase(self.ffmpeg_recording_input_args)
		self.ffmpeg_recording_output_args = container_to_lowercase(self.ffmpeg_recording_output_args)
		self.ffmpeg_archive_output_args = container_to_lowercase(self.ffmpeg_archive_output_args)
		self.ffmpeg_upload_output_args = container_to_lowercase(self.ffmpeg_upload_output_args)

		self.text_to_speech_language_voices = container_to_lowercase(self.text_to_speech_language_voices)

		self.ffmpeg_text_to_speech_video_input_args = container_to_lowercase(self.ffmpeg_text_to_speech_video_input_args)
		self.ffmpeg_text_to_speech_audio_input_args = container_to_lowercase(self.ffmpeg_text_to_speech_audio_input_args)
		self.ffmpeg_text_to_speech_output_args = container_to_lowercase(self.ffmpeg_text_to_speech_output_args)

		template_path = os.path.join(self.plugins_path, 'standalone_media.html.template')
		with open(template_path, 'r', encoding='utf-8') as file:
			self.standalone_media_template = file.read()

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

	try:
		subprocess.run(['ffmpeg', '-version'], check=True, stdout=DEVNULL)
	except CalledProcessError:
		log.error('Could not find the ffmpeg executable in the PATH.')
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
					time.sleep(5)
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
			kill_processes_by_path(self.plugin_container_path)

	class ScreenCapture():
		""" A process that captures the screen and stores the recording on disk using ffmpeg. """

		raw_recording_path: str
		upload_recording_path: str
		archive_recording_path: str
		
		stream: ffmpeg.Stream
		process: Popen
		failed: bool

		def __init__(self, output_path_prefix: str):
			
			self.raw_recording_path = output_path_prefix + '.raw.mkv'
			self.upload_recording_path = output_path_prefix + '.mp4'
			self.archive_recording_path = output_path_prefix + '.mkv'

			stream = ffmpeg.input(config.ffmpeg_recording_input_name, t=config.max_video_duration, **config.ffmpeg_recording_input_args)
			stream = stream.output(self.raw_recording_path, **config.ffmpeg_recording_output_args)
			stream = stream.global_args(*config.ffmpeg_global_args)
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

				output_types = [(self.upload_recording_path, config.ffmpeg_upload_output_args)]
				
				if config.keep_archive_copy:
					output_types.append((self.archive_recording_path, config.ffmpeg_archive_output_args))

				for output_path, output_arguments in output_types:

					stream = ffmpeg.input(self.raw_recording_path)
					stream = stream.output(output_path, **output_arguments)
					stream = stream.global_args(*config.ffmpeg_global_args)
					stream = stream.overwrite_output()

					try:
						log.debug(f'Processing the recording with the ffmpeg arguments: {stream.get_args()}')
						stream.run()
					except ffmpeg.Error as error:
						log.error(f'Failed to process "{self.raw_recording_path}" into "{output_path}" with the error: {repr(error)}')
						self.failed = True
						break
			
			delete_file(self.raw_recording_path)

	if config.enable_text_to_speech:
		
		from comtypes import COMError # type: ignore
		from comtypes.client import CreateObject # type: ignore
		
		# We need to create a speech engine at least once before importing SpeechLib. Otherwise, we'd get an ImportError.
		CreateObject('SAPI.SpVoice')
		from comtypes.gen import SpeechLib # type: ignore

		class TextToSpeech():
			""" A wrapper for the Microsoft Speech API and ffmpeg that generates a text-to-speech recording. """

			engine: SpeechLib.ISpeechVoice
			stream: SpeechLib.ISpeechFileStream
			temporary_file: BinaryIO

			language_to_voice: Dict[Optional[str], SpeechLib.ISpeechObjectToken]

			def __init__(self):
				
				# See:
				# - https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ms723602(v=vs.85)
				# - https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ms722561(v=vs.85)
				self.engine = CreateObject('SAPI.SpVoice')
				self.stream = CreateObject('SAPI.SpFileStream')
				
				# We have to close the temporary file so SpFileStream.Open() doesn't fail.
				self.temporary_file = NamedTemporaryFile(mode='wb', prefix=TEMPORARY_PATH_PREFIX, suffix='.wav', delete=False)
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

			def generate_text_to_speech_file(self, intro: str, text: str, language: Optional[str], output_path_prefix: str) -> Optional[str]:
				""" Generates a video file that contains the text-to-speech in the audio track and a blank screen on the video one.
				The voice used by the Speech API is specified in the configuration file and depends on the page's language.
				The correct voice packages have been installed on Windows, otherwise a default voice is used instead. """
				
				output_path: Optional[str] = output_path_prefix + (f'.tts.{language}.mp4' if language is not None else '.tts.mp4')

				try:
					# See:
					# - https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ms720858(v=vs.85)
					# - https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ms720892(v=vs.85)
					self.stream.Open(self.temporary_file.name, SpeechLib.SSFMCreateForWrite)
					self.engine.AudioOutputStream = self.stream
					self.engine.Voice = self.language_to_voice[language]
					self.engine.Speak(intro, SpeechLib.SVSFPurgeBeforeSpeak | SpeechLib.SVSFIsXML)
					self.engine.Speak(text, SpeechLib.SVSFPurgeBeforeSpeak | SpeechLib.SVSFIsNotXML)
					self.stream.Close()

					video_stream = ffmpeg.input(config.ffmpeg_text_to_speech_video_input_name, **config.ffmpeg_text_to_speech_video_input_args)
					audio_stream = ffmpeg.input(self.temporary_file.name, **config.ffmpeg_text_to_speech_audio_input_args)

					target_stream = ffmpeg.output(video_stream, audio_stream, output_path, **config.ffmpeg_text_to_speech_output_args)
					target_stream = target_stream.global_args(*config.ffmpeg_global_args)
					target_stream = target_stream.overwrite_output()
					
					log.debug(f'Generating the text-to-speech file with the ffmpeg arguments: {target_stream.get_args()}')
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
		debug_highlight_colors: Iterator[str]

		def __init__(self, firefox_window: Optional[WindowSpecification], thread_name='plugin_input_repeater'):

			super().__init__(name=thread_name, daemon=True)
			
			self.firefox_window = firefox_window
			self.running = False
			self.debug_highlight_colors = itertools.cycle(['red', 'green', 'blue'])

		def run(self):
			""" Runs the input repeater on a loop, sending a series of keystrokes periodically to any Firefox plugin windows. """

			if self.firefox_window is None:
				return

			first = True

			while self.running:
				
				if first:
					time.sleep(config.plugin_input_repeater_initial_wait)
				else:
					time.sleep(config.plugin_input_repeater_wait_per_cycle)

				first = False

				try:
					plugin_windows = self.firefox_window.children(class_name='GeckoPluginWindow')
					
					if config.debug and config.plugin_input_repeater_debug_highlight:
						for window in plugin_windows:
							window.draw_outline(next(self.debug_highlight_colors))

					plugin_windows += self.firefox_window.children(class_name='ImlWinCls')
					plugin_windows += self.firefox_window.children(class_name='ImlWinClsSw10')
					plugin_windows += self.firefox_window.children(class_name='SunAwtCanvas')
					plugin_windows += self.firefox_window.children(class_name='SunAwtFrame')

					for window in plugin_windows:
						try:
							rect = window.rectangle()
							width = round(rect.width() / config.width_dpi_scaling)
							height = round(rect.height() / config.height_dpi_scaling)
							
							if width >= config.min_plugin_input_repeater_window_size and height >= config.min_plugin_input_repeater_window_size:
								window.click()
								window.send_keystrokes(config.plugin_input_repeater_keystrokes)

						except (ElementNotEnabled, ElementNotVisible):
							pass

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

	class CosmoPlayerViewpointCycler(PluginInputRepeater):
		""" A thread that periodically tells any VRML world running in the Cosmo Player to move to the next viewpoint. """

		def __init__(self, firefox_window: Optional[WindowSpecification]):
			super().__init__(firefox_window, thread_name='cosmo_player_viewpoint_cycler')

		def run(self):
			""" Runs the viewpoint cycler on a loop, sending the "Next Viewpoint" hotkey periodically to any Cosmo Player windows. """
			
			if self.firefox_window is None:
				return

			while self.running:
				
				time.sleep(config.cosmo_player_viewpoint_wait_per_cycle)

				try:
					cosmo_player_windows = self.firefox_window.children(class_name='CpWin32RenderWindow')
					for window in cosmo_player_windows:
						window.send_keystrokes('{PGDN}')
				except Exception:
					pass				

	log.info('Initializing the recorder.')
	log.info(f'Detected the physical screen resolution {config.physical_screen_width}x{config.physical_screen_height} with the DPI scaling ({config.width_dpi_scaling:.2f}, {config.height_dpi_scaling:.2f}).')

	scheduler = BlockingScheduler()

	def record_snapshots(num_snapshots: int) -> None:
		""" Records a given number of snapshots in a single batch. """
		
		log.info(f'Recording {num_snapshots} snapshots.')

		try:
			if config.enable_proxy:
				log.info('Initializing the proxy.')
				proxy = Proxy.create()
			else:
				proxy = nullcontext() # type: ignore

			if config.enable_text_to_speech:
				log.info('Initializing text-to-speech.')
				text_to_speech = TextToSpeech()

			standalone_media_page_file = NamedTemporaryFile(mode='w', encoding='utf-8', prefix=TEMPORARY_PATH_PREFIX, suffix='.html', delete=False)
			standalone_media_page_url = f'file:///{standalone_media_page_file.name}'
			log.debug(f'Created the temporary standalone media page file "{standalone_media_page_file.name}".')

			standalone_media_download_directory = TemporaryDirectory(prefix=TEMPORARY_PATH_PREFIX, suffix='.media')
			standalone_media_download_search_path = os.path.join(standalone_media_download_directory.name, '*')
			log.debug(f'Created the temporary standalone media download directory "{standalone_media_download_directory.name}".')

			extra_preferences: dict = {
				# Always use cached page.
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
					'network.proxy.no_proxies_on': 'localhost, 127.0.0.1', # For standalone media.
				})

			with Database() as db, Browser(extra_preferences=extra_preferences, use_extensions=True, use_plugins=True, use_autoit=True) as (browser, driver), TemporaryRegistry() as registry:

				browser.go_to_blank_page_with_text('\N{Broom} Initializing \N{Broom}')

				def generate_standalone_media_page(wayback_url: str, media_extension: Optional[str] = None) -> Tuple[float, Optional[str], Optional[str], bool]:
					""" Generates the page where a standalone media file is embedded using both the information
					from the configuration as well as the file's metadata. """

					success = True

					wayback_parts = parse_wayback_machine_snapshot_url(wayback_url)
					parts = urlparse(wayback_parts.Url if wayback_parts is not None else wayback_url)
					filename = os.path.basename(parts.path)
						
					if media_extension is None:
						_, media_extension = os.path.splitext(filename)
						media_extension = media_extension.lower().strip('.')

					embed_url = wayback_url
					loop = 'true'
					duration: float = config.standalone_media_fallback_duration
					title = None
					author = None
					
					# If a media file points to other resources (e.g. VRML worlds or RealAudio metadata), we don't
					# want to download it since other files from the Wayback Machine may be required to play it.
					# If it doesn't (i.e. audio and video formats), we'll just download and play it from disk.
					if media_extension not in config.nondownloadable_standalone_media_extensions:
						
						width = config.standalone_media_width
						height = config.standalone_media_height

						try:
							global_rate_limiter.wait_for_wayback_machine_rate_limit()
							response = requests.get(wayback_url)
							response.raise_for_status()
							
							# We need to keep the file extension so Firefox can choose the right plugin to play it.
							downloaded_file_path = os.path.join(standalone_media_download_directory.name, filename)
							with open(downloaded_file_path, 'wb') as file:
								file.write(response.content)
						
							log.debug(f'Downloaded the standalone media "{wayback_url}" to "{downloaded_file_path}".')
							
							# Two separate URLs because ffmpeg uses "file:path" instead of "file://path".
							# See: https://superuser.com/questions/718027/ffmpeg-concat-doesnt-work-with-absolute-path/1551017#1551017
							embed_url = f'file:///{downloaded_file_path}'
							probe_url = f'file:{downloaded_file_path}'
							loop = 'false'

							probe = ffmpeg.probe(probe_url)
							format = probe['format']
							tags = format.get('tags', {})
							
							# See: https://wiki.multimedia.cx/index.php/FFmpeg_Metadata
							title = tags.get('title')
							author = tags.get('author') or tags.get('artist') or tags.get('album_artist') or tags.get('composer') or tags.get('copyright')
							log.debug(f'The standalone media "{title}" by "{author}" has the following tags: {tags}')

							duration = float(format['duration'])
							log.debug(f'The standalone media has a duration of {duration} seconds.')
						
						except requests.RequestException as error:
							log.error(f'Failed to download the standalone media file "{wayback_url}" with the error: {repr(error)}')
							success = False
						except (ffmpeg.Error, KeyError, ValueError) as error:
							log.warning(f'Could not parse the standalone media\'s metadata with the error: {repr(error)}')

					else:
						width = '100%'
						height = '100%'
							
					content = config.standalone_media_template
					content = content.replace('{comment}', f'Generated by "{__file__}" on {get_current_timestamp()}.')
					content = content.replace('{background_color}', config.standalone_media_background_color)
					content = content.replace('{width}', width)
					content = content.replace('{height}', height)
					content = content.replace('{url}', embed_url)
					content = content.replace('{loop}', loop)
					
					# Overwrite the temporary standalone media page.
					standalone_media_page_file.seek(0)
					standalone_media_page_file.truncate(0)
					standalone_media_page_file.write(content)
					standalone_media_page_file.flush()

					return duration, title, author, success

				def abort_snapshot(snapshot: Snapshot) -> None:
					""" Aborts a snapshot that couldn't be recorded correctly due to a WebDriver error. """

					try:
						db.execute('UPDATE Snapshot SET State = :aborted_state WHERE Id = :id;', {'aborted_state': Snapshot.ABORTED, 'id': snapshot.Id})
						db.commit()
					except sqlite3.Error as error:
						log.error(f'Failed to abort the snapshot {snapshot} with the error: {repr(error)}')
						db.rollback()
						time.sleep(config.database_error_wait)

				def is_standalone_media_extension_allowed(media_extension: str) -> bool:
					""" Checks if a standalone media snapshot should be recorded. """
					return bool(config.allowed_standalone_media_extensions) and media_extension in config.allowed_standalone_media_extensions

				db.create_function('IS_STANDALONE_MEDIA_EXTENSION_ALLOWED', 1, is_standalone_media_extension_allowed)

				if config.fullscreen_browser:
					browser.toggle_fullscreen()

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
							framerate = config.ffmpeg_recording_input_args.get('framerate', 60)
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
													RANK_SNAPSHOT_BY_POINTS(SI.Points, :ranking_offset) AS Rank,
													SI.Points,
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
												AND (NOT S.IsStandaloneMedia OR IS_STANDALONE_MEDIA_EXTENSION_ALLOWED(S.MediaExtension))
												AND IS_URL_KEY_ALLOWED(S.UrlKey)
											ORDER BY
												S.Priority DESC,
												Rank DESC
											LIMIT 1;
											''', {'ranking_offset': config.ranking_offset, 'scouted_state': Snapshot.SCOUTED, 'published_state': Snapshot.PUBLISHED,
												  'min_publish_days_for_new_recording': config.min_publish_days_for_new_recording, 'min_year': config.min_year,
												  'max_year': config.max_year, 'record_sensitive_snapshots': config.record_sensitive_snapshots})
						
						row = cursor.fetchone()
						if row is not None:
							snapshot = Snapshot(**dict(row))

							assert snapshot.Points is not None, 'The Points column is not being computed properly.'
							
							config.apply_snapshot_options(snapshot)
							browser.set_fallback_encoding_for_snapshot(snapshot)

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
							media_duration, media_title, media_author, _ = generate_standalone_media_page(snapshot.WaybackUrl, snapshot.MediaExtension)
							content_url = standalone_media_page_url
						else:
							content_url = snapshot.WaybackUrl

						log.info(f'[{snapshot_index+1} of {num_snapshots}] Recording snapshot #{snapshot.Id} {snapshot} with {snapshot.Points} points (last published = {days_since_last_published} days ago).')
						
						browser.bring_to_front()
						pywinauto.mouse.move((0, config.physical_screen_height // 2))
						
						missing_urls: List[str] = []

						if config.enable_proxy:
							proxy.timestamp = snapshot.Timestamp
						
						cache_wait = config.standalone_media_cache_wait if snapshot.IsStandaloneMedia else config.page_cache_wait
						proxy_wait = config.proxy_queue_timeout + config.proxy_total_timeout if config.enable_proxy else 0

						# How much we wait before killing the plugins depends on how long we expect
						# each phase (caching and recording) to last in the worst case scenario.
						plugin_crash_timeout = config.base_plugin_crash_timeout + config.page_load_timeout + config.page_plugin_wait + cache_wait + proxy_wait

						frame_text_list = []
						realaudio_url = None

						# Wait for the page and its resources to be cached.
						with proxy, PluginCrashTimer(browser.firefox_directory_path, plugin_crash_timeout):

							browser.go_to_wayback_url(content_url)

							# Make sure the plugin instances had time to load.
							time.sleep(config.page_plugin_wait)

							# This may be less than the real value if we had to kill any plugin instances.
							num_plugin_instances = browser.count_plugin_instances()
							log.debug(f'Found {num_plugin_instances} plugin instances.')

							if snapshot.PageUsesPlugins and num_plugin_instances == 0:
								
								for _ in browser.traverse_frames():
									for tag in ['object', 'embed', 'applet']:
										num_plugin_instances += len(driver.find_elements_by_tag_name(tag))

								log.warning(f'Could not find any plugin instances when at least one was expected. Assuming {num_plugin_instances} instances.')
							
							wait_after_load: float
							wait_per_scroll: float

							if snapshot.IsStandaloneMedia:
								scroll_height = 0
								scroll_step = 0.0
								num_scrolls_to_bottom = 0
								wait_after_load = clamp(config.base_standalone_media_wait_after_load + media_duration, config.min_video_duration, config.max_video_duration)
								wait_per_scroll = 0.0
							else:
								scroll_height = 0
								for _ in browser.traverse_frames():
									
									frame_scroll_height = driver.execute_script('return document.body.scrollHeight;')
									if frame_scroll_height > scroll_height:

										scroll_height = frame_scroll_height
										client_height = driver.execute_script('return document.body.clientHeight;')

									if config.enable_text_to_speech:
										frame_text = driver.execute_script('return document.documentElement.innerText;')
										frame_text_list.append(frame_text)

								# While this works for most cases, there are pages where the scroll and client
								# height have the same value even though there's a scrollbar. This happens even
								# in modern Mozilla and Chromium browsers.
								# E.g. https://web.archive.org/web/20070122030542if_/http://www.youtube.com/index.php?v=6Gwn0ARKXgE
								scroll_step = client_height * config.viewport_scroll_percentage
								num_scrolls_to_bottom = ceil((scroll_height - client_height) / scroll_step)

								wait_after_load = config.base_wait_after_load + num_plugin_instances * config.wait_after_load_per_plugin_instance
								wait_per_scroll = config.base_wait_per_scroll + num_plugin_instances * config.wait_after_scroll_per_plugin_instance

								min_wait_after_load = max(config.min_video_duration, config.base_wait_after_load + min(num_plugin_instances, 1) * config.wait_after_load_per_plugin_instance)
								max_wait_after_load = max(config.max_video_duration - num_scrolls_to_bottom * wait_per_scroll, 0)
								wait_after_load = clamp(wait_after_load, min_wait_after_load, max_wait_after_load)
						
								max_wait_per_scroll = (max(config.max_video_duration - wait_after_load, 0) / num_scrolls_to_bottom) if num_scrolls_to_bottom > 0 else 0
								wait_per_scroll = clamp(wait_per_scroll, 0, max_wait_per_scroll)

							log.info(f'Waiting {cache_wait:.1f} seconds for the page to cache.')
							time.sleep(cache_wait)

							# Keep waiting if the page or any plugins are still requesting data.
							if config.enable_proxy:
								
								log.debug('Waiting for the proxy.')
								begin_proxy_time = time.time()
								
								proxy_status_codes: Counter = Counter()

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
											
											url = save_match.group('url')
											missing_urls.append(url)

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
							media_duration, media_title, media_author, success = generate_standalone_media_page(realaudio_url)
							wait_after_load = clamp(config.base_standalone_media_wait_after_load + media_duration, config.min_video_duration, config.max_video_duration)

							if not success:
								log.error(f'Failed to download the RealAudio file.')
								abort_snapshot(snapshot)
								continue

						if config.debug and browser.window is not None:
							
							plugin_windows = browser.window.children(class_name='GeckoPluginWindow')
							for window in plugin_windows:
								
								rect = window.rectangle()
								width = round(rect.width() / config.width_dpi_scaling)
								height = round(rect.height() / config.height_dpi_scaling)
								
								log.debug(f'Found a plugin instance with the size: {width}x{height}.')

						# Prepare the recording phase.

						subdirectory_path = config.get_recording_subdirectory_path(recording_id)
						os.makedirs(subdirectory_path, exist_ok=True)
						
						parts = urlparse(snapshot.Url)
						media_identifier = snapshot.MediaExtension if snapshot.IsStandaloneMedia else ('p' if snapshot.PageUsesPlugins else '')
						recording_identifiers = [str(recording_id), str(snapshot.Id), parts.hostname, str(snapshot.OldestDatetime.year), str(snapshot.OldestDatetime.month).zfill(2), str(snapshot.OldestDatetime.day).zfill(2), media_identifier]
						recording_path_prefix = os.path.join(subdirectory_path, '_'.join(filter(None, recording_identifiers)))

						browser.bring_to_front()
						pywinauto.mouse.move((0, config.physical_screen_height // 2))
						browser.close_all_windows()

						plugin_input_repeater = PluginInputRepeater(browser.window) if config.enable_plugin_input_repeater else nullcontext()
						cosmo_player_viewpoint_cycler = CosmoPlayerViewpointCycler(browser.window) if config.enable_cosmo_player_viewpoint_cycler else nullcontext()

						plugin_crash_timeout = config.base_plugin_crash_timeout + config.page_load_timeout + config.max_video_duration
						with PluginCrashTimer(browser.firefox_directory_path, plugin_crash_timeout) as crash_timer:
							
							log.info(f'Waiting {wait_after_load:.1f} seconds after loading and then {wait_per_scroll:.1f} for each of the {num_scrolls_to_bottom} scrolls of {scroll_step:.1f} pixels to cover {scroll_height} pixels.')
							browser.go_to_wayback_url(content_url)

							# Reloading the object, embed, and applet tags can yield good results when a page
							# uses various plugins that can potentially start playing at different times.
							if config.reload_plugin_media_before_recording:
								browser.reload_plugin_media()
					
							# Record the snapshot. The page should load faster now that its resources are cached.
							with plugin_input_repeater, cosmo_player_viewpoint_cycler, ScreenCapture(recording_path_prefix) as capture:
							
								time.sleep(wait_after_load)

								for _ in range(num_scrolls_to_bottom):
									for _ in browser.traverse_frames():
										driver.execute_script('window.scrollBy({top: arguments[0], left: 0, behavior: "smooth"});', scroll_step)
									time.sleep(wait_per_scroll)
					
						redirected = False
						if not snapshot.IsStandaloneMedia:
							redirected, url, timestamp = browser.was_wayback_url_redirected(content_url)
							if redirected:
								log.error(f'The page was redirected to "{url}" at {timestamp} while recording.')

						browser.close_all_windows()
						browser.go_to_blank_page_with_text('\N{Film Projector} Post Processing \N{Film Projector}', str(snapshot))
						capture.perform_post_processing()
						
						if crash_timer.crashed or capture.failed or redirected:
							log.error(f'Aborted the recording (plugins crashed = {crash_timer.crashed}, capture failed = {capture.failed}, redirected = {redirected}).')
							state = Snapshot.ABORTED
						elif days_since_last_published is not None:
							log.info(f'Saved the new recording after {days_since_last_published} days to "{capture.upload_recording_path}".')
							state = Snapshot.APPROVED
						else:
							log.info(f'Saved the recording to "{capture.upload_recording_path}".')
							state = Snapshot.RECORDED

						if config.save_missing_proxy_snapshots_that_still_exist_online:

							if missing_urls:
								log.info(f'Locating files based on {len(missing_urls)} missing URLs.')
							
							# Remove any duplicates to minimize the amount of requests to the Save API
							# and to improve look up operations when trying to find other missing URLs.
							extra_missing_urls = {url: True for url in missing_urls}

							# Find other potentially missing URLs if the filename ends in a number.
							# If a file like "level3.dat" was missing, then we should check the
							# other values, both above and below 3.
							# E.g. https://web.archive.org/cdx/search/cdx?url=disciplinas.ist.utl.pt/leic-cg/materiais/VRML/cenas_vrml/cutplane/*&fl=original,timestamp,statuscode&collapse=urlkey
							for i, url in enumerate(missing_urls):
								
								browser.go_to_blank_page_with_text('\N{Left-Pointing Magnifying Glass} Locating Missing URLs \N{Left-Pointing Magnifying Glass}', f'{i+1} of {len(missing_urls)}')

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
								for num in range(config.max_total_extra_missing_proxy_snapshot_tries):

									if num_consecutive_misses >= config.max_consecutive_extra_missing_proxy_snapshot_tries:
										break

									# Increment the value between the filename and extension.
									new_num = str(num).zfill(padding)
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

							if missing_urls:
								log.info(f'Saving {len(missing_urls)} missing URLs.')

							for i, url in enumerate(missing_urls):

								browser.go_to_blank_page_with_text('\N{Floppy Disk} Saving Missings URLs \N{Floppy Disk}', f'{i+1} of {len(missing_urls)}', f'{url}')

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
											url = wayback_parts.Url
											timestamp = wayback_parts.Timestamp
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

						text_to_speech_file_path = None
						if config.enable_text_to_speech and not snapshot.IsStandaloneMedia:
							
							browser.go_to_blank_page_with_text('\N{Speech Balloon} Generating Text-to-Speech \N{Speech Balloon}', str(snapshot))
							
							# Add some context XML so the date is spoken correctly no matter the language.
							# See: https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ee125665(v=vs.85)
							title = f'Page Title: "{snapshot.PageTitle}"' if snapshot.PageTitle else 'Untitled Page'
							year, month, day = snapshot.OldestDatetime.year, snapshot.OldestDatetime.month, snapshot.OldestDatetime.day
							date = f'<context id="date_ymd">{year}/{month}/{day}</context>'
							
							page_intro = f'{title} ({date})'
							page_text = '.\n'.join(frame_text_list)
							text_to_speech_file_path = text_to_speech.generate_text_to_speech_file(page_intro, page_text, snapshot.PageLanguage, recording_path_prefix)

							if text_to_speech_file_path is not None:
								log.info(f'Saved the text-to-speech file to "{text_to_speech_file_path}".')

					except SessionNotCreatedException as error:
						log.warning(f'Terminated the WebDriver session abruptly with the error: {repr(error)}')
						break
					except WebDriverException as error:
						log.error(f'Failed to record the snapshot with the WebDriver error: {repr(error)}')
						abort_snapshot(snapshot)
						continue

					try:
						db.execute('UPDATE Snapshot SET State = :state WHERE Id = :id;', {'state': state, 'id': snapshot.Id})

						if state != Snapshot.ABORTED:
							
							upload_filename = os.path.basename(capture.upload_recording_path)
							archive_filename = os.path.basename(capture.archive_recording_path) if config.keep_archive_copy else None
							text_to_speech_filename = os.path.basename(text_to_speech_file_path) if text_to_speech_file_path is not None else None

							db.execute(	'''
										INSERT INTO Recording (SnapshotId, IsProcessed, UploadFilename, ArchiveFilename, TextToSpeechFilename, CreationTime)
										VALUES (:snapshot_id, :is_processed, :upload_filename, :archive_filename, :text_to_speech_filename, :creation_time);
										''', {'snapshot_id': snapshot.Id, 'is_processed': False, 'upload_filename': upload_filename,
											  'archive_filename': archive_filename, 'text_to_speech_filename': text_to_speech_filename,
											  'creation_time': get_current_timestamp()})

							if snapshot.Priority == Snapshot.RECORD_PRIORITY:
								db.execute('UPDATE Snapshot SET Priority = :no_priority WHERE Id = :id;', {'no_priority': Snapshot.NO_PRIORITY, 'id': snapshot.Id})

						else:
							delete_file(capture.upload_recording_path)
							delete_file(capture.archive_recording_path)
							if text_to_speech_file_path is not None:
								delete_file(text_to_speech_file_path)

						if snapshot.IsStandaloneMedia and all(metadata is None for metadata in [snapshot.MediaTitle, snapshot.MediaAuthor]):
							db.execute(	'UPDATE Snapshot SET MediaTitle = :media_title, MediaAuthor = :media_author WHERE Id = :id;',
										{'media_title': media_title, 'media_author': media_author, 'id': snapshot.Id})
						
						# For cases where looking at the embed tags while scouting isn't enough.
						# E.g. https://web.archive.org/web/19961221002554if_/http://www.geocities.com:80/Hollywood/Hills/5988/
						elif not snapshot.PageUsesPlugins and num_plugin_instances > 0:
							log.info(f'Detected {num_plugin_instances} plugin instances while no embed tags were found during scouting.')
							db.execute('UPDATE Snapshot SET PageUsesPlugins = :page_uses_plugins WHERE Id = :id;', {'page_uses_plugins': True, 'id': snapshot.Id})

						if config.save_missing_proxy_snapshots_that_still_exist_online:
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