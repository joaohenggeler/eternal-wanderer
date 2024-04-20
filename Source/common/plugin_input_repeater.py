#!/usr/bin/env python3

from threading import Thread
from time import sleep
from typing import Optional

from pywinauto.application import WindowSpecification # type: ignore
from pywinauto.base_wrapper import ElementNotEnabled, ElementNotVisible # type: ignore

from .config import CommonConfig
from .logger import log

class PluginInputRepeater(Thread):
	""" A thread that periodically interacts with any plugin instance running in Firefox. """

	firefox_window: Optional[WindowSpecification]
	config: CommonConfig

	running: bool

	def __init__(self, firefox_window: Optional[WindowSpecification], config: CommonConfig, thread_name='plugin_input_repeater'):

		super().__init__(name=thread_name, daemon=True)

		self.firefox_window = firefox_window
		self.config = config
		self.running = False

	def run(self):
		""" Runs the input repeater on a loop, sending a series of keystrokes periodically to any Firefox plugin windows. """

		if self.firefox_window is None:
			return

		first = True

		while self.running:

			if first:
				sleep(self.config.plugin_input_repeater_initial_wait)
			else:
				sleep(self.config.plugin_input_repeater_wait_per_cycle)

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
						width = round(rect.width() / self.config.width_dpi_scaling)
						height = round(rect.height() / self.config.height_dpi_scaling)
						interactable = width >= self.config.plugin_input_repeater_min_window_size and height >= self.config.plugin_input_repeater_min_window_size

						if interactable:
							window.click()
							window.send_keystrokes(self.config.plugin_input_repeater_keystrokes)

						if self.config.debug and self.config.plugin_input_repeater_debug:
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

	def __init__(self, firefox_window: Optional[WindowSpecification], config: CommonConfig):
		super().__init__(firefox_window, config, thread_name='cosmo_player_viewpoint_cycler')

	def run(self):
		""" Runs the viewpoint cycler on a loop, sending the "Next Viewpoint" hotkey periodically to any Cosmo Player windows. """

		if self.firefox_window is None:
			return

		while self.running:

			sleep(self.config.cosmo_player_viewpoint_wait_per_cycle)

			try:
				# E.g. https://web.archive.org/web/19970713113545if_/http://www.hedges.org:80/thehedges/Ver21.wrl
				# E.g. https://web.archive.org/web/20220616010004if_/http://disciplinas.ist.utl.pt/leic-cg/materiais/VRML/cenas_vrml/golf/golf.wrl
				cosmo_player_windows = self.firefox_window.children(class_name='CpWin32RenderWindow')
				for window in cosmo_player_windows:
					window.send_keystrokes('{PGDN}')
			except Exception as error:
				log.error(f'Failed to send the input to the Cosmo Player windows with the error: {repr(error)}')