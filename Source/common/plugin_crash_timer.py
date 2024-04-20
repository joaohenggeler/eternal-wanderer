#!/usr/bin/env python3

from pathlib import Path
from threading import Timer
from typing import Optional

from .browser import Browser
from .logger import log
from .util import kill_processes_by_path

class PluginCrashTimer:
	""" A special timer that kills Firefox's plugin container child processes after a given time has elapsed (e.g. the recording duration). """

	timeout: float

	plugin_container_path: Path
	java_plugin_launcher_path: Optional[Path]
	timer: Timer
	crashed: bool

	def __init__(self, browser: Browser, timeout: float):

		self.timeout = timeout

		self.plugin_container_path = browser.firefox_path.parent / 'plugin-container.exe'
		self.java_plugin_launcher_path = browser.java_bin_path / 'jp2launcher.exe' if browser.java_bin_path is not None else None

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