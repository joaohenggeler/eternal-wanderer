#!/usr/bin/env python3

import os
from typing import Union
from urllib.parse import ParseResult, urlparse

import requests
from requests import RequestException
from requests.adapters import HTTPAdapter, Retry
from tldextract import TLDExtract

from .config import config

def extract_media_extension_from_url(url: str) -> str:
	""" Retrieves the file extension from a media file URL. The returned extension may be
	different from the real value for the sake of convenience (e.g. compressed VRML worlds). """

	parts = urlparse(url)
	path = parts.path.lower()

	# For compressed VRML worlds that would otherwise be stored as "gz".
	if path.endswith('.wrl.gz'):
		extension = 'wrz'
	else:
		_, extension = os.path.splitext(path)
		extension = extension.removeprefix('.')

	return extension

def is_url_available(url: str, allow_redirects: bool = False) -> bool:
	""" Checks if a URL is available. """

	try:
		response = requests.head(url, allow_redirects=allow_redirects)
		result = response.status_code < 400 if allow_redirects else response.status_code == 200
	except RequestException:
		result = False

	return result

def is_url_from_domain(url: Union[str, ParseResult], domain: str) -> bool:
	""" Checks if a URL is part of a domain or any of its subdomains. """
	parts = urlparse(url) if isinstance(url, str) else url
	return parts.hostname is not None and (parts.hostname == domain or parts.hostname.endswith('.' + domain))

checked_allowed_domains: dict[str, bool] = {}
checked_disallowed_domains: dict[str, bool] = {}

def is_url_key_allowed(url_key: str) -> bool:
	""" Checks whether a URL should be scouted or recorded given its URL key. """
	return (not config.allowed_domains or url_key_matches_domain_pattern(url_key, config.allowed_domains, checked_allowed_domains)) and (not config.disallowed_domains or not url_key_matches_domain_pattern(url_key, config.disallowed_domains, checked_disallowed_domains))

def url_key_matches_domain_pattern(url_key: str, domain_patterns: list[list[str]], cache: dict[str, bool]) -> bool:
	""" Checks whether a URL's key matches a list of domain patterns. """

	result = False

	if domain_patterns:

		# E.g. "com,geocities)/hollywood/hills/5988"
		domain, *_ = url_key.lower().partition(')')

		# E.g. "com,sun,java:8081)/products/javamail/index.html"
		domain, *_ = domain.partition(':')

		if domain in cache:
			return cache[domain]

		component_list = domain.split(',')

		for pattern_component_list in domain_patterns:

			# If the domain has fewer components then it can't match the allowed pattern.
			if len(component_list) < len(pattern_component_list):
				continue

			# If there are more components in the domain than in the allowed pattern, these will be ignored.
			# Since we're looking at these domains backwards, this means we'll match any subdomains.
			for component, pattern_component in zip(component_list, pattern_component_list):
				if pattern_component != '*' and component != pattern_component:
					break
			else:
				result = True
				break

		cache[domain] = result

	return result

retry = Retry(total=5, status_forcelist=[502, 503, 504], backoff_factor=1)
adapter = HTTPAdapter(max_retries=retry)

global_session = requests.Session()
global_session.mount('http://web.archive.org', adapter)
global_session.mount('https://web.archive.org', adapter)

del retry, adapter

tld_extract = TLDExtract(suffix_list_urls=())