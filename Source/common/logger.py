#!/usr/bin/env python3

import logging

log = logging.getLogger('eternal wanderer')

def setup_logger(name: str) -> logging.Logger:
	""" Adds a stream and file handler to the Eternal Wanderer logger. """

	stream_handler = logging.StreamHandler()
	stream_formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
	stream_handler.setFormatter(stream_formatter)

	file_handler = logging.FileHandler(name + '.log', 'a', 'utf-8')
	file_formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(filename)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
	file_handler.setFormatter(file_formatter)

	global log
	log.addHandler(stream_handler)
	log.addHandler(file_handler)

	return log