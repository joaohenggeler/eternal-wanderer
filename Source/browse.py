#!/usr/bin/env python3

import os
import shutil
from argparse import ArgumentParser

from selenium.common.exceptions import WebDriverException # type: ignore

from common import CommonConfig, Browser

if __name__ == '__main__':

	config = CommonConfig()

	parser = ArgumentParser(description='Opens a URL in a Firefox version equipped with various plugins and extensions. Avoid using this version to browse live websites.')
	parser.add_argument('url', nargs='?', default='about:support', help='The URL of the page to open. If omitted, it defaults to "%(default)s".')
	parser.add_argument('-pluginreg', action='store_true', help='Generate the pluginreg.dat file inside the profile template directory.')
	parser.add_argument('-disable_multiprocess', action='store_false', dest='multiprocess', help='Disable multiprocess Firefox. This should only be used when running the Classic Add-ons Archive extension since disabling this mode may crash some plugins.')
	args = parser.parse_args()

	if args.pluginreg and config.use_master_plugin_registry:
		parser.error('The "use_master_plugin_registry" configuration option must be set to false in order to generate the pluginreg.dat file.')

	try:
		with Browser(multiprocess=args.multiprocess, use_extensions=True, use_plugins=True) as (browser, driver):
			
			if args.pluginreg:
				
				try:
					plugin_reg_source_path = os.path.join(browser.profile_path, 'pluginreg.dat')
					plugin_reg_destination_path = os.path.join(config.profile_path, 'pluginreg.dat')
					shutil.copy(plugin_reg_source_path, plugin_reg_destination_path)

					with open(plugin_reg_destination_path, encoding='utf-8', newline='') as file:
						content = file.read()

					# A very quick and dirty way of editing the autogenerated pluginreg.dat file.

					# Add the QuickTime media type to VLC and remove any conflicting file formats that are used be other plugins (Flash, MIDPLUG, MOD Plugin).
					content = content.replace('VLC Web Plugin|$\n139', 'VLC Web Plugin|$\n140')
					content = content.replace('115|application/x-shockwave-flash|Shockwave Flash file|swf,swfl|$', '115||Shockwave Flash file||$')
					content = content.replace('119|audio/midi|MIDI audio|mid,midi,kar|$', '119||MIDI audio||$')
					content = content.replace('132|audio/x-mod|Amiga SoundTracker audio||$', '132||Amiga SoundTracker audio||$')
					content = content.replace('133|audio/x-s3m|Scream Tracker 3 audio||$', '133||Scream Tracker 3 audio||$')
					content = content.replace('138|video/x-nsv|NullSoft video||$', '138|video/x-nsv|NullSoft video||$\n139|video/quicktime|QuickTime video|mov|$')

					# YAMAHA MIDPLUG for XG seems to crash when it plays anything other than MIDI files, so we'll remove these media types.
					content = content.replace('4|audio/x-wav|WAVE|wav|$', '4||WAVE||$')
					content = content.replace('5|audio/wav|WAVE|wav|$', '5||WAVE||$')
					content = content.replace('6|audio/x-aiff|AIFF|aif,aiff|$', '6||AIFF||$')
					content = content.replace('7|audio/aiff|AIFF|aif,aiff|$', '7||AIFF||$')
					content = content.replace('8|audio/basic|AU|au|$', '8||AU||$')
					
					with open(plugin_reg_destination_path, 'w', encoding='utf-8', newline='') as file:
						file.write(content)

					print(f'Generated "{plugin_reg_destination_path}".')

				except OSError as error:
					print(f'Failed to generate the pluginreg.dat file with the error: {repr(error)}')

			try:
				print(f'Opening the page "{args.url}".')
				driver.get(args.url)
			except WebDriverException as error:
				print(f'Failed to open the page with the error: {repr(error)}')

			input('>>>>> Press enter to close the browser <<<<<')

	except KeyboardInterrupt:
		print('Detected a keyboard interrupt when these should not be used to terminate the scout due to a bug when using both Windows and the Firefox WebDriver.')

	print('Finished running.')