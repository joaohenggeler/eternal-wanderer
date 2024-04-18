#!/usr/bin/env python3

import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Optional

from requests import RequestException
from waybackpy import WaybackMachineCDXServerAPI as Cdx
from waybackpy.cdx_snapshot import CDXSnapshot

from .logger import log
from .net import extract_media_extension_from_url, global_session, is_url_available
from .rate_limiter import global_rate_limiter

@dataclass
class WaybackParts:
	timestamp: str
	modifier: Optional[str]
	url: str

WAYBACK_MACHINE_SNAPSHOT_URL_REGEX = re.compile(r'https?://web\.archive\.org/web/(?P<timestamp>\d+)(?P<modifier>[a-z]+_)?/(?P<url>.+)', re.IGNORECASE)

def parse_wayback_machine_snapshot_url(url: str) -> Optional[WaybackParts]:
	""" Divides the URL of a Wayback Machine snapshot into its basic components. """

	result = None

	match = WAYBACK_MACHINE_SNAPSHOT_URL_REGEX.fullmatch(url)
	if match is not None:

		timestamp = match['timestamp']
		modifier = match['modifier']
		url = match['url']
		result = WaybackParts(timestamp, modifier, url)

	return result

def compose_wayback_machine_snapshot_url(*, timestamp: Optional[str] = None, modifier: Optional[str] = None,
										 url: Optional[str] = None, parts: Optional[WaybackParts] = None) -> str:
	""" Combines the basic components of a Wayback Machine snapshot into a URL. """

	if parts is not None:
		timestamp = parts.timestamp
		modifier = parts.modifier
		url = parts.url

	if timestamp is None or url is None:
		raise ValueError('Missing the Wayback Machine timestamp and URL.')

	modifier = modifier or ''
	return f'https://web.archive.org/web/{timestamp}{modifier}/{url}'

def find_best_wayback_machine_snapshot(timestamp: str, url: str) -> tuple[CDXSnapshot, bool, Optional[str]]:
	""" Finds the best Wayback Machine snapshot given its timestamp and URL. By best snapshot we mean
	locating the nearest one and then finding the oldest capture where the content is identical. """

	global_rate_limiter.wait_for_cdx_api_rate_limit()
	cdx = Cdx(url=url, filters=['statuscode:200'])
	snapshot = cdx.near(wayback_machine_timestamp=timestamp)

	global_rate_limiter.wait_for_cdx_api_rate_limit()
	cdx.filters.append(f'digest:{snapshot.digest}')
	snapshot = cdx.oldest()

	# Consider plain text files since regular HTML pages may be served with this MIME type.
	# E.g. https://web.archive.org/web/20011201170113if_/http://www.yahoo.co.jp/bin/top3
	is_media = snapshot.mimetype not in ['text/html', 'text/plain']
	media_extension = extract_media_extension_from_url(snapshot.original) if is_media else None

	return snapshot, is_media, media_extension

def find_extra_wayback_machine_snapshot_info(wayback_url: str) -> Optional[str]:
	""" Finds the last modified time of a Wayback Machine snapshot. Note that not every snapshot has this information. """

	# The last modified time seems to always be returned regardless of the modifier.
	# There's other headers that require the iframe modifier (e.g. x-archive-guessed-charset).

	last_modified_time = None

	try:
		global_rate_limiter.wait_for_wayback_machine_rate_limit()
		response = global_session.head(wayback_url)
		response.raise_for_status()

		last_modified_header = response.headers.get('x-archive-orig-last-modified')
		if last_modified_header is not None:

			# Fix an issue where the time zone appears twice.
			# E.g. https://web.archive.org/web/19961018174824if_/http://www.com-stock.com:80/dave/
			# Where the last modified time is "Friday, 18-Oct-96 15:48:24 GMT GMT".
			if last_modified_header.endswith('GMT GMT'):
				log.warning(f'Fixing the broken last modified time "{last_modified_header}".')
				last_modified_header = last_modified_header.replace('GMT GMT', 'GMT')

			# Fix an issue where the time zone and time are not delimited.
			# E.g. https://web.archive.org/web/20060813091112if_/http://www.phone-books.net/
			# Where the last modified time is "Sun, 13 Aug 2006 09:11:11GMT".
			if last_modified_header.endswith('GMT') and not last_modified_header.endswith(' GMT'):
				log.warning(f'Fixing the broken last modified time "{last_modified_header}".')
				last_modified_header = last_modified_header.removesuffix('GMT') + ' GMT'

			# Fix an issue where the minutes and seconds are not delimited.
			# E.g. https://web.archive.org/web/20010926042147if_/http://geocities.yahoo.co.jp:80/
			# Where the last modified time is "Mon, 24 Sep 2001 04:2146 GMT".
			split_header = last_modified_header.split(':')
			if len(split_header) == 2:
				log.warning(f'Fixing the broken last modified time "{last_modified_header}".')
				last_modified_header = ':'.join([split_header[0], split_header[1][:2], split_header[1][2:]])

			# Fix an issue where the time is missing. This solution adds potentially
			# incorrect information to the datetime, which is fine for our purposes.
			# E.g. https://web.archive.org/web/19970112174206if_/http://www.manish.com:80/jneko/
			# Where the last modified time is "Wed, 27 Mar 1996 ? GMT".
			if last_modified_header.endswith('? GMT'):
				log.warning(f'Fixing the broken last modified time "{last_modified_header}".')
				last_modified_header = last_modified_header.replace('? GMT', '00:00:00 GMT')

			from .snapshot import Snapshot
			last_modified_time = parsedate_to_datetime(last_modified_header).strftime(Snapshot.TIMESTAMP_FORMAT)

	except RequestException as error:
		log.error(f'Failed to find any extra information from the snapshot "{wayback_url}" with the error: {repr(error)}')
	except (ValueError, TypeError) as error:
		# Catching TypeError is necessary for other unhandled broken dates.
		log.error(f'Failed to parse the last modified time "{last_modified_header}" of the snapshot "{wayback_url}" with the error: {repr(error)}')

	return last_modified_time

def is_wayback_machine_available() -> bool:
	""" Checks if the Wayback Machine is available. """
	global_rate_limiter.wait_for_wayback_machine_rate_limit()
	return is_url_available('https://web.archive.org', allow_redirects=True)

def is_cdx_api_available() -> bool:
	""" Checks if the CDX API is available. """
	global_rate_limiter.wait_for_cdx_api_rate_limit()
	return is_url_available('https://web.archive.org/cdx/search/cdx?url=archive.org&limit=1', allow_redirects=True)

def are_wayback_machine_services_available() -> bool:
	""" Checks if both the Wayback Machine and CDX API are available. """
	return is_wayback_machine_available() and is_cdx_api_available()