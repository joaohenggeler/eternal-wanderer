#!/usr/bin/env python3

import os
import queue
import re
from queue import Queue
from subprocess import PIPE, Popen, STDOUT
from threading import Thread
from time import sleep
from typing import Optional

from selenium.webdriver.common.utils import free_port # type: ignore

from .logger import log

class Proxy(Thread):
	""" A proxy thread that intercepts all HTTP/HTTPS requests made by Firefox and its plugins. Used to locate missing resources
	in other subdomains via the CDX API while also allowing plugin media that loads slowly to finish requesting assets. """

	port: int

	process: Popen
	queue: Queue
	timestamp: Optional[str]

	RESPONSE_REGEX = re.compile(r'\[RESPONSE\] \[(?P<status_code>.+)\] \[(?P<mark>.+)\] \[(?P<content_type>.+)\] \[(?P<url>.+)\] \[(?P<id>.+)\]')
	SAVE_REGEX  = re.compile(r'\[SAVE\] \[(?P<url>.+)\]')
	REALMEDIA_REGEX  = re.compile(r'\[RAM\] \[(?P<url>.+)\]')
	FILENAME_REGEX  = re.compile(r'(?P<name>.*?)(?P<num>\d+)(?P<extension>\..*)')

	def __init__(self, port: int):

		super().__init__(name='proxy', daemon=True)

		self.port = port

		os.environ['PYTHONUNBUFFERED'] = '1'
		self.process = Popen(['mitmdump', '--quiet', '--listen-port', str(self.port), '--script', 'wayback_proxy_addon.py'], stdin=PIPE, stdout=PIPE, stderr=STDOUT, bufsize=1, encoding='utf-8')
		self.queue = Queue()
		self.timestamp = None

		self.start()

	@staticmethod
	def create(port: Optional[int] = None) -> 'Proxy':
		""" Creates the proxy while handling any errors at startup (e.g. Python errors or already used ports). """

		if port is None:
			port = free_port()

		while True:
			try:
				log.info(f'Creating the proxy on port {port}.')
				proxy = Proxy(port)

				error = proxy.get(timeout=10)
				log.error(f'Failed to create the proxy with the error: {error}')
				proxy.task_done()

				proxy.process.kill()
				sleep(5)
				port = free_port()

			except queue.Empty:
				break

		return proxy

	def run(self):
		""" Runs the proxy thread on a loop, enqueuing any messages received from the mitmproxy script. """
		for line in iter(self.process.stdout.readline, ''):
			self.queue.put(line.rstrip('\n'))
		self.process.stdout.close()

	def get(self, **kwargs) -> str:
		""" Retrieves a message from the queue. """
		return self.queue.get(**kwargs)

	def task_done(self) -> None:
		""" Signals that a retrieved message was handled. """
		self.queue.task_done()

	def clear(self) -> None:
		""" Clears the message queue. """
		while not self.queue.empty():
			try:
				self.get(block=False)
				self.task_done()
			except queue.Empty:
				pass

	def exec(self, command: str) -> None:
		""" Passes a command that is then executed in the mitmproxy script. """
		self.process.stdin.write(command + '\n') # type: ignore
		self.get()
		self.task_done()

	def shutdown(self) -> None:
		""" Stops the mitmproxy script and proxy thread. """
		try:
			self.process.terminate()
			self.join()
		except OSError as error:
			log.error(f'Failed to terminate the proxy process with the error: {repr(error)}')

	def __enter__(self):
		self.clear()
		self.exec(f'current_timestamp = "{self.timestamp}"')

	def __exit__(self, exception_type, exception_value, traceback):
		self.exec('current_timestamp = None')