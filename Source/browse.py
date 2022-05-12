#!/usr/bin/env python3

"""
	This script opens a specific URL in the Firefox version used when recording.
	Since this is an older browser version that runs various plugins, you shouldn't use it to browse live websites.
"""

from argparse import ArgumentParser

from selenium.common.exceptions import TimeoutException, WebDriverException # type: ignore

from common import Browser

####################################################################################################

parser = ArgumentParser(description='Open a page in a Firefox version equipped with various plugins and extensions. You should not use this version to browse live websites.')
parser.add_argument('url', nargs='?', default='about:support', help='The URL of the page to open. If not specified, it defaults to "%(default)s".')
args = parser.parse_args()

try:
	with Browser(use_extensions=True, use_plugins=True) as (browser, driver):
		try:
			print(f'Opening the page "{args.url}".')
			driver.get(args.url)
		except TimeoutException as error:
			print('Timed out while loading the page.')
		except WebDriverException as error:
			print(f'Failed to open the page with the error: {repr(error)}')

		input('>>>>> Press enter to close the browser <<<<<')
except KeyboardInterrupt:
	print('Detected a keyboard interrupt when these should not be used to terminate the scout due to a bug when using both Windows and the Firefox WebDriver.')

print('Finished running.')