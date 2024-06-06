#!/usr/bin/env python3

import dataclasses
import json
import locale
import logging
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Optional, TYPE_CHECKING, Union

from .logger import log
from .util import container_to_lowercase

if TYPE_CHECKING:
    from .snapshot import Snapshot

@dataclass
class CommonConfig:
	""" The general purpose configuration that applies to all scripts. """

	# From the config file.
	json_config: dict

	debug: bool
	locale: str

	database_path: Path
	database_error_wait: int

	gui_webdriver_path: Path
	headless_webdriver_path: Path
	page_load_timeout: int

	gui_firefox_path: Path
	headless_firefox_path: Path

	profile_path: Path
	preferences: dict[str, Union[bool, int, str]]

	extensions_path: Path
	extensions_before_running: dict[str, bool]
	extensions_after_running: dict[str, bool]
	user_scripts: dict[str, bool]

	plugins_path: Path
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

	autoit_path: Path
	autoit_poll_frequency: int
	autoit_scripts: dict[str, bool]

	fonts_path: Path
	sound_fonts_path: Path

	recordings_path: Path
	max_recordings_per_directory: int
	compilations_path: Path

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

	ffmpeg_path: Optional[Path]
	fluidsynth_path: Optional[Path]

	user_agent: str

	language_names: dict[str, str]

	# Determined at runtime.
	default_options: dict

	# Constants.
	TEMPORARY_PATH_PREFIX = 'wanderer.'

	MUTABLE_OPTIONS = [
		# For the recorder script.
		'hide_scrollbars'

		'page_cache_wait',
		'media_cache_wait',

		'plugin_load_wait',
		'base_plugin_crash_timeout',

		'viewport_scroll_percentage',
		'base_wait_after_load',
		'wait_after_load_per_plugin_instance',
		'base_wait_per_scroll',
		'wait_after_scroll_per_plugin_instance',
		'wait_for_plugin_playback_after_load',
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

		'enable_media_conversion',
		'enable_audio_mixing',
	]

	def __init__(self):

		with open('config.json', encoding='utf-8') as file:
			self.json_config = json.load(file)

		self.load_subconfig('common')

		self.database_path = Path(self.database_path).absolute()
		self.gui_webdriver_path = Path(self.gui_webdriver_path).absolute()
		self.headless_webdriver_path = Path(self.headless_webdriver_path).absolute()
		self.gui_firefox_path = Path(self.gui_firefox_path).absolute()
		self.headless_firefox_path = Path(self.headless_firefox_path).absolute()

		self.profile_path = Path(self.profile_path).absolute()
		self.extensions_path = Path(self.extensions_path).absolute()
		self.plugins_path = Path(self.plugins_path).absolute()
		self.autoit_path = Path(self.autoit_path).absolute()
		self.fonts_path = Path(self.fonts_path).absolute()
		self.sound_fonts_path = Path(self.sound_fonts_path).absolute()
		self.recordings_path = Path(self.recordings_path).absolute()
		self.compilations_path = Path(self.compilations_path).absolute()

		if self.ffmpeg_path is not None:
			self.ffmpeg_path = Path(self.ffmpeg_path).absolute()

		if self.fluidsynth_path is not None:
			self.fluidsynth_path = Path(self.fluidsynth_path).absolute()

		self.extensions_before_running = container_to_lowercase(self.extensions_before_running)
		self.extensions_after_running = container_to_lowercase(self.extensions_after_running)
		self.user_scripts = container_to_lowercase(self.user_scripts)
		self.plugins = container_to_lowercase(self.plugins)
		self.autoit_scripts = container_to_lowercase(self.autoit_scripts)

		self.shockwave_renderer = self.shockwave_renderer.lower()
		self.cosmo_player_renderer = self.cosmo_player_renderer.lower()
		self._3dvia_renderer = self._3dvia_renderer.lower()

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

		self.language_names = container_to_lowercase(self.language_names)

		self.default_options = {}

	def load_subconfig(self, name: str) -> None:
		""" Loads a specific JSON object from the configuration file. """

		config = self.json_config[name]
		self.__dict__.update(config)

		config_names = set(config)
		field_names = set(field.name for field in dataclasses.fields(self))
		assert config_names.issubset(field_names), f'The subconfig "{name}" contains options that are not defined in the class {type(self).__name__}: {config_names.difference(field_names)}'

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

	def get_recording_subdirectory_path(self, id_: int) -> Path:
		""" Retrieves the absolute path of a snapshot recording from its ID. """
		bucket = ceil(id_ / self.max_recordings_per_directory) * self.max_recordings_per_directory
		return self.recordings_path / str(bucket)

for option in ['emojis', 'encoding', 'media_extension_override', 'notes', 'script', 'tags', 'title_override']:
	assert option not in CommonConfig.MUTABLE_OPTIONS, f'The mutable option name "{option}" is reserved.'

del option

config = CommonConfig()

log.setLevel(logging.DEBUG if config.debug else logging.INFO)
log.debug('Running in debug mode.')

locale.setlocale(locale.LC_ALL, config.locale)