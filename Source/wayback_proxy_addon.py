#!/usr/bin/env python3

"""
	This mitmproxy script tells the recorder script if the page is still making requests while also
	checking if any missing files are available in a different subdomain. This script should not be
	run directly and is instead started automatically by the recorder if the "use_proxy" option is
	true.
"""

import os
from threading import Lock, Thread
from urllib.parse import urljoin, urlparse

import requests
from mitmproxy import http # type: ignore
from mitmproxy.script import concurrent # type: ignore
from tldextract import TLDExtract
from waybackpy import WaybackMachineCDXServerAPI as Cdx

from common import Snapshot, compose_wayback_machine_snapshot_url, is_url_available, is_url_from_domain, parse_wayback_machine_snapshot_url
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
# - https://github.com/mitmproxy/mitmproxy/blob/v8.0.0/examples/addons/nonblocking.py
# - https://github.com/mitmproxy/mitmproxy/blob/v8.0.0/examples/addons/http-reply-from-proxy.py
# - https://docs.mitmproxy.org/stable/api/mitmproxy/flow.html
# - https://docs.mitmproxy.org/stable/api/mitmproxy/http.html

@concurrent
def request(flow: http.HTTPFlow) -> None:
	
	request = flow.request

	if config.block_proxy_requests_outside_archive_org and not is_url_from_domain(request.url, 'archive.org'):
		flow.kill()
		return

	if current_timestamp is None:
		return

	with lock:
		timestamp = current_timestamp
		print(f'[REQUEST] [{request.http_version} {request.method}] [{request.url}] [{flow.id}]')

	if request.method not in ['GET', 'POST']:
		return

	wayback_parts = parse_wayback_machine_snapshot_url(request.url)

	# Not a snapshot URL.
	if wayback_parts is None:
		return
	
	original_url = wayback_parts.Url

	if config.find_missing_proxy_snapshots_using_cdx or config.save_missing_proxy_snapshots_that_still_exist_online:
		try:
			# E.g.
			# - https://web.archive.org/web/20020120142510if_/http://example.com/, allow_redirects=False -> 200
			# - https://web.archive.org/web/20020120142510if_/http://example.com/, allow_redirects=True -> 200
			# - https://web.archive.org/web/1if_/http://www.example.com/, allow_redirects=False -> 302
			# - https://web.archive.org/web/1if_/http://www.example.com/, allow_redirects=True -> 200
			# - https://web.archive.org/web/1if_/http://www.example.com/this/doesnt/exist, allow_redirects=False -> 404
			# - https://web.archive.org/web/1if_/http://www.example.com/this/doesnt/exist, allow_redirects=True -> 404
			config.wait_for_wayback_machine_rate_limit()
			wayback_response = requests.head(request.url, allow_redirects=True)
		except requests.RequestException:
			wayback_response = None 

	cdx_mark = None

	if config.find_missing_proxy_snapshots_using_cdx and wayback_response is not None:

		if wayback_response.status_code != 200:
			
			parts = urlparse(original_url)
			
			# E.g. "http://www.example.com/path1/path2/file.ext" -> "/path2/file.ext" (2 components).
			if config.max_missing_proxy_snapshot_path_components is not None:
				split_path = parts.path.split('/')
				path = '/'.join(split_path[-config.max_missing_proxy_snapshot_path_components:])
			else:
				path = parts.path

			# We'll match any URLs in this domain and subdomains that contain the path pattern above.
			# The pattern is checked against the end of the URL or a query string.
			# E.g. "http://example.com/path/file.swf" or "http://example.com/path/file.swf?1234567890".
			# This match is case insensitive to maximize the amount of archived results (even if a
			# URL's path is technically case sensitive).
			#
			# # E.g. https://web.archive.org/cdx/search/cdx?url=shockwave.com&matchType=domain&filter=statuscode:200&filter=original:(?i).*(/sis/game.swf)($|\?.*)&fl=original,timestamp,statuscode&collapse=urlkey
			extract = no_fetch_tld_extract(original_url)
			subdomain_cdx = Cdx(url=extract.registered_domain, match_type='domain', filters=['statuscode:200', fr'original:(?i).*{path}($|\?.*)'])
			
			if parts.query or parts.fragment:
				# For websites with a lot of captures (e.g. YouTube), the CDX query above won't return
				# any results. In some cases, a URL with a query string points to the same resource as
				# one without it (e.g. cache busting). We'll try to address the previous annoying issue
				# by checking if the same URL without the query or fragment was archived.
				#
				# E.g. https://web.archive.org/cdx/search/cdx?url=http://www.youtube.com/player2.swf&filter=statuscode:200&fl=original,timestamp,statuscode&collapse=urlkey
				url_without_query = urljoin(original_url, parts.path)
				no_query_cdx = Cdx(url=url_without_query, filters=['statuscode:200'])
			else:
				no_query_cdx = None

			cdx_list = list(filter(None, [subdomain_cdx, no_query_cdx]))
			for i, cdx in enumerate(cdx_list):
				
				cdx_mark = f'CDX {i+1}/{len(cdx_list)} {wayback_response.status_code}'

				try:
					config.wait_for_cdx_api_rate_limit()
					snapshot = cdx.near(wayback_machine_timestamp=timestamp)
					wayback_parts = wayback_parts._replace(Timestamp=snapshot.timestamp, Url=snapshot.original)
					break
				except Exception:
					pass

	redirect_to_original = False
	if config.save_missing_proxy_snapshots_that_still_exist_online and wayback_response is not None:

		if wayback_response.status_code != 200 and is_url_available(original_url):
			
			redirect_to_original = True
			with lock:
				print(f'[SAVE] [{original_url}]')

	# Avoid showing the toolbar in frame pages that are missing their modifier.
	if wayback_parts.Modifier is None:
		wayback_parts = wayback_parts._replace(Modifier=Snapshot.IFRAME_MODIFIER)

	# This is used to redirect the request in the majority of cases. For VRML
	# worlds, we'll create the response ourselves (see below).
	request.url = original_url if redirect_to_original else compose_wayback_machine_snapshot_url(parts=wayback_parts)
	live_mark = 'LIVE' if redirect_to_original else None

	# The Cosmo Player plugin used to display VRML worlds doesn't seem to handle
	# HTTP redirects properly. When a world requests an image or audio file from
	# the Wayback Machine, these will naturally be redirected since it's unlikely
	# that they share the same timestamp as the main world file. To prevent Cosmo
	# Player from throwing an error when loading assets, we'll request them here
	# and create the mitmproxy response ourselves.
	vrml_mark = None
	referer = request.headers.get('referer')
	
	if referer is not None:

		parts = urlparse(referer)
		path = parts.path.lower()
		_, file_extension = os.path.splitext(path)

		# Check if the request came from a VRML world.
		if file_extension in ['.wrl', '.wrz'] or path.endswith('.wrl.gz'):
			
			vrml_mark = 'VRML'

			try:
				# From testing, it appears we have to pass the response's decoded
				# content instead of using the raw one with the stream parameter
				# enabled. The Gzip and Deflate encodings are handled natively,
				# with Brotli being supported if the brotlicffi package is installed.
				#
				# See:
				# - https://requests.readthedocs.io/en/latest/api/#requests.Response
				# - https://urllib3.readthedocs.io/en/stable/reference/urllib3.response.html
				config.wait_for_wayback_machine_rate_limit()
				response = requests.request(request.method, request.url, headers=dict(request.headers))
				flow.response = http.Response.make(response.status_code, response.content, dict(response.headers))	
			except request.RequestException:
				pass

	flow.marked = ', '.join(filter(None, [live_mark, vrml_mark, cdx_mark]))

@concurrent
def response(flow: http.HTTPFlow) -> None:
	
	if current_timestamp is None:
		return
	
	mark = flow.marked or '-'
	content_type = flow.response.headers.get('content-type', '-')
	
	with lock:
		print(f'[RESPONSE] [{flow.response.status_code}] [{mark}] [{content_type}] [{flow.request.url}] [{flow.id}]')

	if config.debug:
		flow.response.headers['x-eternal-wanderer'] = 'debug'