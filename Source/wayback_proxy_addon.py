#!/usr/bin/env python3

"""
	This mitmproxy script tells the recorder script if the page is still making requests while also
	checking if any missing files are available in a different subdomain. This script should not be
	run directly and is instead started automatically by the recorder if the "use_proxy" option is
	true.
"""

from threading import Lock, Thread
from urllib.parse import urljoin, urlparse

import requests
from mitmproxy import http # type: ignore
from mitmproxy.script import concurrent # type: ignore
from tldextract import TLDExtract
from waybackpy import WaybackMachineCDXServerAPI as Cdx

from common import Snapshot, compose_wayback_machine_snapshot_url, parse_wayback_machine_snapshot_url
from record import RecordConfig

####################################################################################################

# This script must be executed with unbuffered output to work properly (e.g. "python -u" or PYTHONUNBUFFERED = '1').
# Messages are sent to the recorder script through stdout.

config = RecordConfig()
lock = Lock()
no_fetch_tld_extract = TLDExtract(suffix_list_urls=None) # type: ignore

# Set by the commands passed from the recorder script.
# - If set to the current snapshot's timestamp, any non-200 responses from
# the Wayback Machine are checked against the CDX API in order to find the
# same missing resource archived in a different path or subdomain.
# - Otherwise, the requests and responses are not intercepted.
current_timestamp = None

def listen_for_commands() -> None:
	""" Executes any commands passed by the recorder script. """

	while True:
		try:
			command = input()
			with lock:
				exec(command, globals())
				print(f'[{command}]')
		except EOFError:
			pass

Thread(target=listen_for_commands, daemon=True).start()

# Useful resources for this mitmproxy script:
# - https://github.com/mitmproxy/mitmproxy/discussions/5023
# - https://github.com/mitmproxy/mitmproxy/blob/main/examples/addons/nonblocking.py
# - https://github.com/mitmproxy/mitmproxy/blob/main/examples/addons/http-reply-from-proxy.py
# - https://docs.mitmproxy.org/stable/api/mitmproxy/flow.html

@concurrent
def request(flow: http.HTTPFlow) -> None:
	
	if current_timestamp is None:
		return

	request = flow.request

	with lock:
		timestamp = current_timestamp
		print(f'[{request.http_version} {request.method}] [{request.url}] [{flow.id}]')

	if request.method not in ['GET', 'POST']:
		return

	wayback_parts = parse_wayback_machine_snapshot_url(request.url)

	# Not a Wayback Machine URL.
	if wayback_parts is None:
		return
	
	# It might be tempting to convert all non-archive.org requests to Wayback Machine snapshots
	# using the current timestamp. There are few cases where this could be useful: e.g. pages or
	# plugin media that try to connect directly to now defunct resources. In pratice, however,
	# this would break the JavaScript and CSS files that are automatically inserted by the Wayback
	# Machine since they're loaded relative to the current host.

	if config.find_missing_proxy_snapshots_using_cdx:

		try:
			response = requests.head(request.url)

			# We'll try to locate missing snapshots even for 3xx responses since the Wayback Machine
			# might redirect us to an invalid resource (e.g. a 404 page from the future).
			if response.status_code != 200:
				
				parts = urlparse(wayback_parts.Url)
				
				# E.g. "http://www.example.com/path1/path2/file.ext" -> "/path2/file.ext" (2 components).
				if config.max_missing_proxy_snapshot_path_components is not None:
					split_path = parts.path.split('/')
					path = '/'.join(split_path[-config.max_missing_proxy_snapshot_path_components:])
				else:
					path = parts.path

				# We'll match any URLs in this domain and subdomains that contain the path pattern above.
				# The pattern is checked against the end of the URL or a query string.
				# E.g. "http://example.com/path/file.swf" or "http://example.com/path/file.swf?1234567890".
				# This match is case insensitive to maximize the amount of archived results (even if the
				# path is technically case sensitive).
				#
				# # E.g. https://web.archive.org/cdx/search/cdx?url=shockwave.com&matchType=domain&filter=statuscode:200&filter=original:(?i).*(/sis/game.swf)($|\?.*)&fl=original,timestamp,statuscode&collapse=urlkey
				extract = no_fetch_tld_extract(wayback_parts.Url)
				subdomain_cdx = Cdx(url=extract.registered_domain, match_type='domain', filters=['statuscode:200', fr'original:(?i).*{path}($|\?.*)'])
				
				if parts.query or parts.fragment:
					# For websites with a lot of captures (e.g. YouTube), the CDX query above won't return
					# any results. In some cases, a URL with a query string points to the same resource as
					# one without it (e.g. cache busting). We'll try to address the previous annoying issue
					# by checking if the same URL without the query or fragment was archived.
					#
					# # E.g. https://web.archive.org/cdx/search/cdx?url=http://www.youtube.com/player2.swf&filter=statuscode:200&fl=original,timestamp,statuscode&collapse=urlkey
					url_without_query = urljoin(wayback_parts.Url, parts.path)
					no_query_cdx = Cdx(url=url_without_query, filters=['statuscode:200'])
				else:
					no_query_cdx = None

				for i, cdx in enumerate(filter(None, [subdomain_cdx, no_query_cdx])):
					
					flow.marked = f'CDX {i+1} {response.status_code}'

					try:
						config.wait_for_cdx_api_rate_limit()
						snapshot = cdx.near(wayback_machine_timestamp=timestamp)
						wayback_parts = wayback_parts._replace(Timestamp=snapshot.timestamp, Url=snapshot.original)
						break
					except Exception:
						pass

		except requests.RequestException:
			pass

	# Avoid showing the toolbar in frame pages that are missing their modifier.
	if wayback_parts.Modifier is None:
		wayback_parts = wayback_parts._replace(Modifier=Snapshot.IFRAME_MODIFIER)

	request.url = compose_wayback_machine_snapshot_url(parts=wayback_parts)
	
@concurrent
def response(flow: http.HTTPFlow) -> None:
	
	if current_timestamp is None:
		return
	
	mark = flow.marked or '-'
	content_type = flow.response.headers.get('content-type', '-')
	
	with lock:
		print(f'[{flow.response.status_code}] [{mark}] [{content_type}] [{flow.request.url}] [{flow.id}]')