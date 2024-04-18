#!/usr/bin/env python3

import dataclasses
import itertools
import os
import re
import shutil
import tempfile
from base64 import b64encode
from collections.abc import Iterator
from pathlib import Path
from subprocess import Popen
from time import sleep
from typing import Optional, Union
from urllib.parse import unquote, urlparse

from pywinauto.application import ( # type: ignore
	Application as WindowsApplication,
	ProcessNotFoundError as WindowProcessNotFoundError,
	TimeoutError as WindowTimeoutError,
	WindowSpecification,
)
from requests import RequestException
from selenium import webdriver # type: ignore
from selenium.common.exceptions import ( # type: ignore
	NoSuchWindowException, TimeoutException, WebDriverException,
)
from selenium.webdriver.firefox.firefox_binary import FirefoxBinary # type: ignore
from selenium.webdriver.firefox.firefox_profile import FirefoxProfile # type: ignore
from selenium.webdriver.firefox.webdriver import WebDriver # type: ignore
from selenium.webdriver.support import expected_conditions as webdriver_conditions # type: ignore
from selenium.webdriver.support.ui import WebDriverWait # type: ignore
from xml.etree import ElementTree

from .config import config
from .database import Database
from .logger import log
from .net import global_session, is_url_available
from .rate_limiter import global_rate_limiter
from .snapshot import Snapshot
from .temporary_registry import TemporaryRegistry
from .util import (
	container_to_lowercase, delete_directory, delete_file,
	kill_processes_by_path, kill_process_by_pid,
)
from .wayback import (
	are_wayback_machine_services_available, compose_wayback_machine_snapshot_url,
	is_wayback_machine_available, parse_wayback_machine_snapshot_url,
)

class Browser:
	""" A Firefox browser instance created by Selenium. """

	use_plugins: bool
	use_autoit: bool

	firefox_path: Path
	webdriver_path: Path

	registry: TemporaryRegistry
	java_deployment_path: Path
	java_bin_path: Optional[Path]
	autoit_processes: list[Popen]

	driver: WebDriver
	version: str
	profile_path: Path
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
		self.webdriver_path = config.headless_webdriver_path if headless else config.gui_webdriver_path

		self.registry = TemporaryRegistry()
		self.java_deployment_path = Path(os.environ['USERPROFILE'], 'AppData', 'LocalLow', 'Sun', 'Java', 'Deployment')
		self.java_bin_path = None
		self.autoit_processes = []

		profile = FirefoxProfile(str(config.profile_path))
		log.info(f'Created the temporary Firefox profile at "{profile.profile_dir}".')

		if not config.use_master_plugin_registry:
			plugin_reg_path = Path(profile.profile_dir, 'pluginreg.dat')
			delete_file(plugin_reg_path)

		try:
			scripts_path = Path(profile.profile_dir, 'gm_scripts')
			scripts_config_path = scripts_path / 'config.xml'

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
			log.info(f'Setting additional Firefox preferences: {extra_preferences}')
			for key, value in extra_preferences.items():
				profile.set_preference(key, value)

		if self.use_plugins:

			plugin_paths = [''] * len(config.plugins)
			plugin_precedence = {key: i for i, key in enumerate(config.plugins)}

			for path in config.plugins_path.rglob('np*.dll'):

				filename = path.name.lower()
				if filename in config.plugins:

					if config.plugins[filename]:
						log.info(f'Using the plugin "{filename}".')
						index = plugin_precedence[filename]
						plugin_paths[index] = str(path.parent)
					else:
						log.info(f'Skipping the plugin "{filename}" at the user\'s request.')

				else:
					log.info(f'The plugin file "{path}" was found but is not specified in the configuration.')

			os.environ['MOZ_PLUGIN_PATH'] = ';'.join(plugin_paths)

			plugin_extender_source_path = config.plugins_path / 'BrowserPluginExtender' / 'BrowserPluginExtender.dll'
			shutil.copy(plugin_extender_source_path, self.firefox_path.parent)

			self.configure_shockwave_player()
			self.configure_java_plugin()
			self.configure_cosmo_player()
			self.configure_3dvia_player()
		else:
			os.environ['MOZ_PLUGIN_PATH'] = ''

		if use_extensions:

			for filename, enabled in config.extensions_before_running.items():

				filtered = extension_filter is not None and filename not in extension_filter

				if enabled and not filtered:
					log.info(f'Installing the extension "{filename}".')
					extension_path = config.extensions_path / filename
					profile.add_extension(str(extension_path))
				else:
					log.info(f'Skipping the extension "{filename}" at the user\'s request.')

		options = webdriver.FirefoxOptions()
		options.binary = FirefoxBinary(str(self.firefox_path))
		options.profile = profile
		options.headless = headless

		if multiprocess:
			os.environ.pop('MOZ_FORCE_DISABLE_E10S', None)
		else:
			log.warning('Disabling multiprocess Firefox.')
			os.environ['MOZ_FORCE_DISABLE_E10S'] = '1'

		if not headless:
			# E.g. https://web.archive.org/web/19990221053308if_/http://www.geocities.com:80/Eureka/Park/5977/hallow/index.html
			# Which uses the Halloween font.
			firefox_fonts_path = self.firefox_path.parent / 'fonts'
			for path in config.fonts_path.glob('*.ttf'):
				log.info(f'Adding the font "{path.name}".')
				shutil.copy(path, firefox_fonts_path)

		# Disable DPI scaling to fix potential display issues in Firefox.
		# See:
		# - https://stackoverflow.com/a/37881453/18442724
		# - https://ss64.com/nt/syntax-compatibility.html
		os.environ['__COMPAT_LAYER'] = 'GDIDPISCALING DPIUNAWARE'

		log.info('Creating the Firefox WebDriver.')

		while True:
			try:
				self.driver = webdriver.Firefox(executable_path=self.webdriver_path, options=options, service_log_path=None)
				break
			except WebDriverException as error:
				log.warning(f'Retrying the Firefox WebDriver creation after failing with the error: {repr(error)}')
				kill_processes_by_path(self.firefox_path)
				sleep(10)

		self.driver.set_page_load_timeout(config.page_load_timeout)
		self.driver.maximize_window()

		# See: https://web.archive.org/web/20220602183757if_/https://www.selenium.dev/documentation/webdriver/capabilities/shared/
		assert self.driver.capabilities['pageLoadStrategy'] == 'normal', 'The page load strategy must be "normal".'

		self.version =  self.driver.capabilities['browserVersion']
		self.profile_path = Path(self.driver.capabilities['moz:profile'])
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
					extension_path = config.extensions_path / filename
					self.driver.install_addon(extension_path)
				else:
					log.info(f'Skipping the extension "{filename}" at the user\'s request.')

		self.driver.get(Browser.BLANK_URL)

		if self.use_autoit:

			for filename, enabled in config.autoit_scripts.items():

				if enabled:
					try:
						# If we enable the AutoIt scripts twice, this will kill any existing ones.
						# This is fine in practice since we only do this for the recorder script and
						# since we only want one of each running at the same time anyways.

						log.info(f'Running the AutoIt script "{filename}".')
						script_path = config.autoit_path / filename

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

		java_plugin_path = next(config.plugins_path.rglob('jre*/bin/plugin2'), None)
		if java_plugin_path is None:
			log.error('Could not find the path to the Java Runtime Environment. The Java Plugin was not set up correctly.')
			return

		java_jre_path = java_plugin_path.parent.parent
		log.info(f'Configuring the Java Plugin using the runtime environment located at "{java_jre_path}".')

		java_lib_path = java_jre_path / 'lib'
		self.java_bin_path = java_jre_path / 'bin'

		if config.java_add_to_path:
			os.environ['PATH'] = str(self.java_bin_path) + ';' + os.environ.get('PATH', '')

		java_config_path = java_lib_path / 'deployment.config'
		java_properties_path = java_lib_path / 'deployment.properties'

		java_config_template_path = config.plugins_path / 'Java' / 'deployment.config.template'
		java_properties_template_path = config.plugins_path / 'Java' / 'deployment.properties.template'

		with open(java_config_template_path, encoding='utf-8') as file:
			content = file.read()

		content = content.replace('{comment}', f'Generated by "{__file__}" on {Database.get_current_timestamp()}.')
		content = content.replace('{system_config_path}', str(java_properties_path).replace('\\', '/').replace(' ', '\\u0020'))

		with open(java_config_path, 'w', encoding='utf-8') as file:
			file.write(content)

		with open(java_properties_template_path, encoding='utf-8') as file:
			content = file.read()

		# E.g. "1.8.0" or "1.8.0_11"
		java_product = re.findall(r'(?:jdk|jre)((?:\d+\.\d+\.\d+)(?:_\d+)?)', str(java_jre_path), re.IGNORECASE)[-1]
		java_platform, *_ = java_product.rpartition('.') # E.g. "1.8"
		*_, java_version = java_platform.partition('.') # E.g. "8"

		java_web_start_path = self.java_bin_path / 'javaws.exe'

		java_exception_sites_template_path = config.plugins_path / 'Java' / 'exception.sites.template'
		java_exception_sites_path = java_lib_path / 'exception.sites'
		shutil.copy(java_exception_sites_template_path, java_exception_sites_path)

		def escape_java_deployment_properties_path(path: Path) -> str:
			return str(path).replace('\\', '\\\\').replace(':', '\\:').replace(' ', '\\u0020')

		content = content.replace('{comment}', f'Generated by "{__file__}" on {Database.get_current_timestamp()}.')
		content = content.replace('{jre_platform}', java_platform)
		content = content.replace('{jre_product}', java_product)
		content = content.replace('{jre_path}', escape_java_deployment_properties_path(java_web_start_path))
		content = content.replace('{jre_version}', java_version)
		content = content.replace('{security_level}', 'LOW' if java_product <= '1.7.0_17' else 'MEDIUM')
		content = content.replace('{exception_sites_path}', escape_java_deployment_properties_path(java_exception_sites_path))
		content = content.replace('{console_startup}', 'SHOW' if config.java_show_console else 'NEVER')

		with open(java_properties_path, 'w', encoding='utf-8') as file:
			file.write(content)

		java_policy_template_path = config.plugins_path / 'Java' / 'java.policy.template'
		java_policy_path = java_lib_path / 'security' / 'java.policy'
		shutil.copy(java_policy_template_path, java_policy_path)

		# Override any security properties from other locally installed Java versions in order to allow applets
		# to run even if they use a disabled cryptographic algorithm.
		java_security_path = java_lib_path / 'security' / 'java.security'

		with open(java_security_path, encoding='utf-8') as file:
			content = file.read()

		content = re.sub(r'^jdk\.certpath\.disabledAlgorithms=.*', 'jdk.certpath.disabledAlgorithms=', content, re.MULTILINE)

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
		escaped_java_security_path = str(java_security_path).replace('\\', '/')
		required_java_arguments = [f'-Djava.security.properties=="file:///{escaped_java_security_path}"', '-Xverify:none']
		os.environ['JAVA_TOOL_OPTIONS'] = ' '.join(required_java_arguments + config.java_arguments)
		os.environ['_JAVA_OPTIONS'] = ''
		os.environ['deployment.expiration.check.enabled'] = 'false'

		self.delete_user_level_java_properties()
		self.delete_java_plugin_cache()

	def configure_cosmo_player(self) -> None:
		""" Configures the Cosmo Player by setting the appropriate registry keys. """

		cosmo_player_path = next(config.plugins_path.rglob('npcosmop211.dll'), None)
		if cosmo_player_path is None:
			log.error('Could not find the path to the Cosmo Player plugin files. The Cosmo Player was not be set up correctly.')
			return

		cosmo_player_path = cosmo_player_path.parent
		log.info(f'Configuring the Cosmo Player using the plugin files located at "{cosmo_player_path}".')

		cosmo_player_system32_path = cosmo_player_path / 'System32'
		os.environ['PATH'] = str(cosmo_player_system32_path) + ';' + os.environ.get('PATH', '')

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

		required_registry_keys: dict[str, Union[int, str, Path]] = {
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646731-BCF3-11D0-9518-00C04FC2DD79}\\': 'CosmoMedia AudioRenderer3',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646731-BCF3-11D0-9518-00C04FC2DD79}\\INPROCSERVER32\\': cosmo_player_system32_path / 'cm12_dshow.dll',
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
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\INPROCSERVER32\\': cosmo_player_system32_path / 'cm12_dshow.dll',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\INPROCSERVER32\\THREADINGMODEL': 'Both',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\MERIT': 2097152,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\INPUT\\DIRECTION': 0,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\INPUT\\ISRENDERED': 0,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\INPUT\\ALLOWEDZERO': 0,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\INPUT\\ALLOWEDMANY': 0,
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\PINS\\INPUT\\CONNECTSTOPIN': 'Output',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\INS\\INPUT\\TYPES\\{73646976-0000-0010-8000-00AA00389B71}\\{00000000-0000-0000-0000-000000000000}\\': '',

			'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\FILTER\\{06646732-BCF3-11D0-9518-00C04FC2DD79}\\': 'CosmoMedia VideoRenderer3',

			'HKEY_LOCAL_MACHINE\\SOFTWARE\\COSMOSOFTWARE\\ROBRENDERER\\1.0\\D3D\\PATH': cosmo_player_system32_path / 'rob10_d3d.dll',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\COSMOSOFTWARE\\ROBRENDERER\\1.0\\D3D\\UINAME': 'Direct3D Renderer',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\COSMOSOFTWARE\\ROBRENDERER\\1.0\\NORENDER\\PATH': cosmo_player_system32_path / 'rob10_none.dll',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\COSMOSOFTWARE\\ROBRENDERER\\1.0\\NORENDER\\UINAME': 'NonRendering Renderer',
			'HKEY_LOCAL_MACHINE\\SOFTWARE\\COSMOSOFTWARE\\ROBRENDERER\\1.0\\OPENGL\\PATH': cosmo_player_system32_path / 'rob10_gl.dll',
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

		settings_registry_keys: dict[str, Union[int, str, Path]] = {
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
		user_level_java_properties_path = self.java_deployment_path / 'deployment.properties'
		delete_file(user_level_java_properties_path)

	def delete_java_plugin_cache(self) -> None:
		""" Deletes the Java Plugin cache directory. """
		java_cache_path = self.java_deployment_path / 'cache'
		delete_directory(java_cache_path)

	def shutdown(self):
		""" Closes the browser, quits the WebDriver, and performs any other cleanup operations. """

		try:
			self.driver.quit()
		except WebDriverException as error:
			log.error(f'Failed to quit the WebDriver with the error: {repr(error)}')

		temporary_path = Path(tempfile.gettempdir())

		# Delete the temporary files directories from previous executions. Remeber that there's a bug
		# when running the Firefox WebDriver on Windows that prevents it from shutting down properly
		# if Ctrl-C is used.

		for path in temporary_path.glob('rust_mozprofile*'):
			try:
				log.info(f'Deleting the temporary directory "{path}".')
				delete_directory(path)
			except PermissionError as error:
				log.error(f'Failed to delete the temporary directory with the error: "{repr(error)}".')

		for path in temporary_path.glob('*/webdriver-py-profilecopy'):
			try:
				log.info(f'Deleting the temporary directory "{path.parent}".')
				delete_directory(path.parent)
			except PermissionError as error:
				log.error(f'Failed to delete the temporary directory with the error: "{repr(error)}".')

		for path in temporary_path.glob('tmpaddon-*'):
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

	def go_to_wayback_url(self, wayback_url: str, close_windows: bool = False, check_availability: bool = True, cdx_must_be_up: bool = False) -> None:
		""" Navigates to a Wayback Machine URL, taking into account any rate limiting and retrying if the service is unavailable. """

		is_available = are_wayback_machine_services_available if cdx_must_be_up else is_wayback_machine_available

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
				retry = not wayback_url.startswith('file:') and not is_available()

			except TimeoutException:
				log.warning(f'Timed out after waiting {config.page_load_timeout} seconds for the page to load: "{wayback_url}".')
				# This covers the same case as the next exception without passing the error
				# along to the caller if a regular page took too long to load.
				retry = not is_available()

			except WebDriverException:
				# For cases where the Wayback Machine is unreachable (unexpected downtime)
				# and an error is raised because we were redirected to "about:neterror".
				# If this was some other error and the service is available, then it should
				# be handled by the caller.
				if is_available():
					raise
				else:
					retry = True

			finally:
				if check_availability and retry:
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
			return True, current_url, expected_wayback_parts.timestamp

		# Catches examples #2 and #5.
		redirect_count = self.driver.execute_script('return window.performance.navigation.redirectCount;')
		if redirect_count > 0:
			log.debug(f'Passed the redirection test with the redirect count at {redirect_count}: "{expected_wayback_url}" -> "{current_url}".')
			return True, current_wayback_parts.url, current_wayback_parts.timestamp

		# Catches example #1.
		if current_wayback_parts.modifier != expected_wayback_parts.modifier:
			log.debug(f'Passed the redirection test since the modifiers changed: "{expected_wayback_url}" -> "{current_url}".')
			return True, current_wayback_parts.url, current_wayback_parts.timestamp

		# Catches examples #2 and #5 if they weren't detected before.
		if current_wayback_parts.timestamp != expected_wayback_parts.timestamp:
			log.debug(f'Passed the redirection test since the timestamps changed: "{expected_wayback_url}" -> "{current_url}".')
			return True, current_wayback_parts.url, current_wayback_parts.timestamp

		# Catches example #3 but lets #6 through.
		if current_wayback_parts.url.lower() not in [expected_wayback_parts.url.lower(), unquote(expected_wayback_parts.url.lower())]:
			log.debug(f'Passed the redirection test since the URLs changed: "{expected_wayback_url}" -> "{current_url}".')
			return True, current_wayback_parts.url, current_wayback_parts.timestamp

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
					wayback_parts.modifier = root_wayback_parts.modifier
				else:
					wayback_parts = dataclasses.replace(root_wayback_parts, url=current_url)

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
				if parts.hostname == 'archive.org':
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

	def get_playback_plugin_sources(self) -> list[str]:
		""" Retrieves the source URLs of any content embedded using the object/embed tags in the current web page and its frames. """

		sources = []

		try:
			for _ in self.traverse_frames():
				frame_sources = self.driver.execute_script(	'''
															const SOURCE_ATTRIBUTES = ["data", "src", "movie", "code", "object", "target", "mrl", "filename"];

															const plugin_nodes = document.querySelectorAll("object, embed");
															const plugin_sources = [];

															for(const element of plugin_nodes)
															{
																for(const source_attribute of SOURCE_ATTRIBUTES)
																{
																	const source = element.getAttribute(source_attribute);
																	if(source) plugin_sources.push(source);
																}
															}

															return plugin_sources;
															''')
				sources.extend(frame_sources)

		except WebDriverException as error:
			log.error(f'Failed to get the playback plugin sources with the error: {repr(error)}')

		return sources

	def unload_plugin_content(self, skip_applets: bool = False) -> None:
		""" Unloads any content embedded using the object/embed/applet tags in the current web page and its frames.
		This function should not be called more than once if any of this content is being played by the VLC plugin. """

		selectors = 'object, embed' if skip_applets else 'object, embed, applet'

		try:
			for _ in self.traverse_frames():
				self.driver.execute_script(	'''
											const SOURCE_ATTRIBUTES = ["data", "src", "movie", "code", "object", "target", "mrl", "filename"];

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
											const SOURCE_ATTRIBUTES = ["data", "src", "movie", "code", "object", "target", "mrl", "filename"];

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

# Allow Selenium to set any Firefox preference. Otherwise, certain preferences couldn't be changed.
# See: https://github.com/SeleniumHQ/selenium/blob/selenium-3.141.0/py/selenium/webdriver/firefox/firefox_profile.py
FirefoxProfile.DEFAULT_PREFERENCES = {}
FirefoxProfile.DEFAULT_PREFERENCES['frozen'] = {}
FirefoxProfile.DEFAULT_PREFERENCES['mutable'] = config.preferences