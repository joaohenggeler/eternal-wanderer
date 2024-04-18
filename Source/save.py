#!/usr/bin/env python3

from argparse import ArgumentParser

from waybackpy import WaybackMachineSaveAPI
from waybackpy.exceptions import TooManyRequestsError

from common.rate_limiter import global_rate_limiter

if __name__ == '__main__':

	parser = ArgumentParser(description='Saves URLs from the standard input using the Wayback Machine Save API.')
	args = parser.parse_args()

	total_urls = 0
	num_saved_urls = 0

	while True:
		try:
			url = input()

			if not url:
				continue
			elif not url.startswith('#'):

				total_urls += 1

				try:
					global_rate_limiter.wait_for_save_api_rate_limit()
					save = WaybackMachineSaveAPI(url)
					wayback_url = save.save()
					num_saved_urls += 1

					if save.cached_save:
						print(f'Cached: "{wayback_url}"')
					else:
						print(f'Saved: "{wayback_url}".')

				except TooManyRequestsError as error:
					print(f'Reached the Save API limit while trying to save the URL "{url}": {repr(error)}')
					break
				except Exception as error:
					print(f'Failed to save the URL "{url}" with the error: {repr(error)}')

		except (EOFError, KeyboardInterrupt):
			break

	print(f'Saved {num_saved_urls} of {total_urls} URLs.')