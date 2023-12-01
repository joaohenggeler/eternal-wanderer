#!/usr/bin/env python3

"""
	This mitmproxy script generates a dump file containing all HTTP/HTTPS responses received by the browser.
	This script should not be run directly and is instead started automatically by the browser script if the
	-dump argument was used.
"""

from datetime import datetime, timezone

from mitmproxy import http, io # type: ignore

# See: https://github.com/mitmproxy/mitmproxy/blob/v8.0.0/examples/addons/io-write-flow-file.py

timestamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
dump_file = open(f'mitmproxy.{timestamp}.dump', 'wb')
flow_writer = io.FlowWriter(dump_file)

def response(flow: http.HTTPFlow) -> None:
	flow_writer.add(flow)

def done() -> None:
	dump_file.close()