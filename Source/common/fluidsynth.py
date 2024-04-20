#!/usr/bin/env python3

import os
import subprocess

from .config import config

class FluidSynthException(Exception):
	pass

def fluidsynth(*args) -> None:
	""" Runs FluidSynth. """

	args = ['fluidsynth', '--quiet', '--no-midi-in', '--no-shell', '--disable-lash'] + [str(arg) for arg in args]
	process = subprocess.run(args, capture_output=True, text=True)

	if process.returncode != 0:
		raise FluidSynthException(process.stderr.rstrip('\n'))

if config.fluidsynth_path is not None:
	os.environ['PATH'] = str(config.fluidsynth_path) + ';' + os.environ.get('PATH', '')