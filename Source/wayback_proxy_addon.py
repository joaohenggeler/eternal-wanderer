#!/usr/bin/env python3

"""
	This mitmproxy script tells the recorder script if the page is still making requests while also
	checking if any missing files are available in a different subdomain. This script should not be
	run directly and is instead started automatically by the recorder if the "use_proxy" option is
	true.
"""

import os
from threading import Lock, Thread
from urllib.parse import unquote, urlparse, urlunparse

import requests
from mitmproxy import http # type: ignore
from mitmproxy.script import concurrent # type: ignore
from tldextract import TLDExtract
from waybackpy import WaybackMachineCDXServerAPI as Cdx

from common import Snapshot, compose_wayback_machine_snapshot_url, global_rate_limiter, is_url_available, is_url_from_domain, parse_wayback_machine_snapshot_url
from record import RecordConfig

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

	# Not a Wayback Machine snapshot.
	if wayback_parts is None:
		return
	
	extracted_realaudio_url = False

	if config.convert_realaudio_metadata_proxy_snapshots:
		
		parts = urlparse(wayback_parts.Url)
		_, file_extension = os.path.splitext(parts.path)

		if file_extension.lower() == '.ram':
			try:
				# The RealAudio metadata files only contain the audio stream's URL, not the content itself.
				# In order to play the audio correctly, we'll extract this URL, convert it to a Wayback
				# Machine snapshot URL, and send it back to the recorder script. Note that we'll change the
				# scheme from PNM or RTSP to HTTP since that's how it's served through the Wayback Machine
				# and the CDX API. A previous implementation tried to only modify the stream's URL while
				# keeping the protocol but that didn't work.

				# E.g. https://web.archive.org/web/19970607012148if_/http://www.t0.or.at:80/rafiles/megamix.ram
				# Which contains "pnm://www.t0.or.at/megamix.ra".
				# While "http://www.t0.or.at/megamix.ra" doesn't exist in the Wayback Machine, this one does:
				# https://web.archive.org/web/20220702123119if_/http://noisebase.t0.or.at/escape/megamix.ra
				# By extracting the URL early in this script and later finding missing snapshots using the
				# CDX API, we can play the audio correctly in the recorder script.
				global_rate_limiter.wait_for_wayback_machine_rate_limit()
				metadata_response = requests.get(request.url, stream=True)
				metadata_response.raise_for_status()

				if metadata_response.encoding is None:
					metadata_response.encoding = 'utf-8'

				# Extracting a string from the response requires both a valid encoding and the decode_unicode argument.
				url = next(metadata_response.iter_lines(decode_unicode=True), None)

				if url is not None:
					
					# Checking for valid URLs using netloc only makes sense if it was properly decoded.
					# E.g. "http%3A//www.geocities.com/Hollywood/Hills/5988/main.html" would result in
					# an empty netloc instead of "www.geocities.com".
					url = unquote(url)
					parts = urlparse(url)
					
					if parts.netloc:
						extracted_realaudio_url = True

						parts = parts._replace(scheme='http')
						url = urlunparse(parts)
					
						wayback_parts = wayback_parts._replace(Url=url)
						request.url = compose_wayback_machine_snapshot_url(parts=wayback_parts)

			except (requests.RequestException, UnicodeError):
				pass

	if config.find_missing_proxy_snapshots_using_cdx or config.save_missing_proxy_snapshots_that_still_exist_online:
		try:
			# E.g.
			# - https://web.archive.org/web/20020120142510if_/http://example.com/, allow_redirects=False -> 200
			# - https://web.archive.org/web/20020120142510if_/http://example.com/, allow_redirects=True -> 200
			# - https://web.archive.org/web/1if_/http://www.example.com/, allow_redirects=False -> 302
			# - https://web.archive.org/web/1if_/http://www.example.com/, allow_redirects=True -> 200
			# - https://web.archive.org/web/1if_/http://www.example.com/this/doesnt/exist, allow_redirects=False -> 404
			# - https://web.archive.org/web/1if_/http://www.example.com/this/doesnt/exist, allow_redirects=True -> 404
			global_rate_limiter.wait_for_wayback_machine_rate_limit()
			wayback_response = requests.head(request.url, allow_redirects=True)
			found_snapshot = wayback_response.status_code == 200
		except requests.RequestException:
			wayback_response = None
			found_snapshot = None

	# Let's look at a concrete example: https://web.archive.org/web/20030717041359if_/http://songviolin.bravepages.com:80/
	# This page requests http://askmiky.com/images/FSaward01.gif
	# Which the Wayback Machine redirects to a 404 page: https://web.archive.org/web/20030101155124im_/http://askmiky.com/images/FSaward01.gif
	# Even though there's a valid snapshot here: https://web.archive.org/web/20011016092411im_/http://www.askmiky.com:80/images/FSaward01.gif
	# We can find this URL by asking the CDX API for a valid snapshot near 20030717041359 (the current request's timestamp).

	cdx_mark = None

	if config.find_missing_proxy_snapshots_using_cdx and wayback_response is not None:

		if not found_snapshot:
			
			extract = no_fetch_tld_extract(wayback_parts.Url)
			parts = urlparse(wayback_parts.Url)
			split_path = parts.path.split('/')

			# E.g. "http://www.example.com/path1/path2/file.ext" -> "/path2/file.ext" (2 components).
			if config.max_missing_proxy_snapshot_path_components is not None:
				path = '/'.join(split_path[-config.max_missing_proxy_snapshot_path_components:])
			else:
				path = parts.path

			# We'll match any URLs in this domain and subdomains that contain the path pattern above.
			# The pattern is checked against the end of the URL or a query string.
			# E.g. "http://example.com/path/file.swf" or "http://example.com/path/file.swf?cache=123".
			# This match is case insensitive to maximize the amount of archived results (even if a
			# URL's path is technically case sensitive).
			#
			# E.g. https://web.archive.org/cdx/search/cdx?url=shockwave.com&matchType=domain&filter=statuscode:200&filter=original:(?i).*(/sis/game.swf)($|\?.*)&fl=original,timestamp,statuscode&collapse=urlkey
			subdomain_cdx = Cdx(url=extract.registered_domain, match_type='domain', filters=['statuscode:200', fr'original:(?i).*{path}($|\?.*)'])

			# E.g. "http://www.example.com/path1/path2/file.ext" -> "/path1/".
			# If there is a path, the first split value is an empty string.
			first_path_component = '/'.join(split_path[:2]) + '/' if len(split_path) >= 2 else None
			
			# Ideally, the previous query should work for all cases. Unfortunately, there are cases
			# where the CDX API only returns results if the query specifies a domain and at least
			# one path component. We'll perform this query first (if this component exists) since
			# it should be faster than the subdomain search.
			#
			# E.g. Only the last query yields results.
			# - https://web.archive.org/cdx/search/cdx?url=big.or.jp&matchType=domain&filter=statuscode:200&filter=original:(?i).*\.aif($|\?.*)&fl=original,timestamp,statuscode&collapse=urlkey
			# - https://web.archive.org/cdx/search/cdx?url=big.or.jp&matchType=prefix&filter=statuscode:200&filter=original:(?i).*\.aif($|\?.*)&fl=original,timestamp,statuscode&collapse=urlkey
			# - https://web.archive.org/cdx/search/cdx?url=big.or.jp/~frog/&matchType=prefix&filter=statuscode:200&filter=original:(?i).*\.aif($|\?.*)&fl=original,timestamp,statuscode&collapse=urlkey
			if first_path_component is not None:
				prefix_cdx = Cdx(url=extract.registered_domain + first_path_component, match_type='prefix', filters=['statuscode:200', fr'original:(?i).*{path}($|\?.*)'])
			else:
				prefix_cdx = None

			if parts.query or parts.fragment:
				# E.g. "http://www.example.com/path/file.ext?key=value#top" -> "http://www.example.com/path/file.ext".
				new_parts = parts._replace(params='', query='', fragment='')
				url_without_query = urlunparse(new_parts)

				# For websites with a lot of captures (e.g. YouTube), the CDX query above won't return
				# any results. In some cases, a URL with a query string points to the same resource as
				# one without it (e.g. cache busting). We'll try to address this annoying issue by
				# checking if the same URL without the query or fragment was archived.
				#
				# E.g. https://web.archive.org/cdx/search/cdx?url=http://www.youtube.com/player2.swf&filter=statuscode:200&fl=original,timestamp,statuscode&collapse=urlkey
				no_query_cdx = Cdx(url=url_without_query, filters=['statuscode:200'])
			else:
				no_query_cdx = None

			cdx_list = [(prefix_cdx, 'PREFIX'), (subdomain_cdx, 'SUBDOMAIN'), (no_query_cdx, 'NO QUERY')]
			for i, (cdx, identifier) in enumerate(filter(lambda x: x[0], cdx_list)):
				try:
					# Queries to the CDX API cost twice as much as the queries sent from other scripts.
					global_rate_limiter.wait_for_cdx_api_rate_limit(cost=2)
					snapshot = cdx.near(wayback_machine_timestamp=timestamp)
					wayback_parts = wayback_parts._replace(Timestamp=snapshot.timestamp, Url=snapshot.original)
					found_snapshot = True
					cdx_mark = f'{identifier} CDX {wayback_response.status_code} -> {snapshot.statuscode}'
					break
				except Exception:
					pass
			else:
				cdx_mark = 'NO CDX'

	redirect_to_original = False
	if config.save_missing_proxy_snapshots_that_still_exist_online and wayback_response is not None:

		if not found_snapshot and is_url_available(wayback_parts.Url):
			
			redirect_to_original = True
			with lock:
				print(f'[SAVE] [{wayback_parts.Url}]')

	# Avoid showing the toolbar in frame pages that are missing their modifier.
	if wayback_parts.Modifier is None:
		wayback_parts = wayback_parts._replace(Modifier=Snapshot.IFRAME_MODIFIER)

	# This is used to redirect the request in the majority of cases. For VRML
	# worlds, we'll create the response ourselves (see below).
	request.url = wayback_parts.Url if redirect_to_original else compose_wayback_machine_snapshot_url(parts=wayback_parts)

	if extracted_realaudio_url:
		with lock:
			print(f'[RAM] [{request.url}]')

	# The Cosmo Player plugin used to display VRML worlds doesn't seem to handle
	# HTTP redirects properly. When a world requests an image or audio file from
	# the Wayback Machine, these will naturally be redirected since it's unlikely
	# that they share the same timestamp as the main world file. To prevent Cosmo
	# Player from throwing an error when loading assets, we'll request them here
	# and create the mitmproxy response ourselves.
	request_came_from_vrml = False
	referer = request.headers.get('referer')
	
	if referer is not None:

		parts = urlparse(referer)
		path = parts.path.lower()
		_, file_extension = os.path.splitext(path)

		# Check if the request came from a VRML world.
		if file_extension in ['.wrl', '.wrz'] or path.endswith('.wrl.gz'):
			
			request_came_from_vrml = True

			try:
				# From testing, it appears we have to pass the response's decoded
				# content instead of using the raw one with the stream parameter
				# enabled. The Gzip and Deflate encodings are handled natively,
				# with Brotli being supported if the brotlicffi package is installed.
				#
				# See:
				# - https://requests.readthedocs.io/en/latest/api/#requests.Response
				# - https://urllib3.readthedocs.io/en/stable/reference/urllib3.response.html
				global_rate_limiter.wait_for_wayback_machine_rate_limit()
				response = requests.request(request.method, request.url, headers=dict(request.headers))
				flow.response = http.Response.make(response.status_code, response.content, dict(response.headers))
			except requests.RequestException:
				pass

	live_mark = 'LIVE' if redirect_to_original else None
	realaudio_mark = 'RAM' if extracted_realaudio_url else None
	vrml_mark = 'VRML' if request_came_from_vrml else None

	flow.marked = ', '.join(filter(None, [live_mark, realaudio_mark, vrml_mark, cdx_mark]))

@concurrent
def response(flow: http.HTTPFlow) -> None:
	
	if current_timestamp is None:
		return
	
	request = flow.request
	response = flow.response

	mark = flow.marked or '-'
	content_type = response.headers.get('content-type', '-')
	
	with lock:
		print(f'[RESPONSE] [{response.status_code}] [{mark}] [{content_type}] [{request.url}] [{flow.id}]')

	if config.cache_missing_proxy_responses and response.status_code in [404, 410]:
		response.headers['cache-control'] = 'public; max-age=3600'

	if config.debug:
		response.headers['x-eternal-wanderer'] = mark