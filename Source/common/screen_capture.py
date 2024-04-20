#!/usr/bin/env python3

from pathlib import Path
from subprocess import PIPE, Popen, TimeoutExpired
from typing import Optional, Union

from .config import CommonConfig
from .ffmpeg import ffmpeg, ffmpeg_process, FfmpegException
from .logger import log
from .util import delete_file

class ScreenCapture:
	""" A process that captures the screen and stores the recording on disk using FFmpeg. """

	path_prefix: Path
	config: CommonConfig

	raw_path: Path
	archive_path: Optional[Path]
	upload_path: Path

	process: Popen
	failed: bool

	def __init__(self, path_prefix: Path, config: CommonConfig):
		self.config = config
		self.raw_path = Path(str(path_prefix) + '.raw.mkv')
		self.archive_path = Path(str(path_prefix) + '.mkv') if config.save_archive_copy else None
		self.upload_path = Path(str(path_prefix) + '.mp4')

	def start(self) -> None:
		""" Starts the FFmpeg screen capture process asynchronously. """

		self.failed = False

		input_args = ['-t', self.config.max_duration] + self.config.raw_ffmpeg_input_args + ['-i', self.config.raw_ffmpeg_input_name]
		output_args = self.config.raw_ffmpeg_output_args + [self.raw_path]
		log.debug(f'Capturing the screen with the FFmpeg arguments: {input_args + output_args}')

		# Connecting a pipe to stdin is required to stop the recording by pressing Q.
		# See: https://github.com/kkroening/ffmpeg-python/issues/162
		# Connecting a pipe to stdout and stderr is useful to check for any FFmpeg error messages.
		self.process = ffmpeg_process(*input_args, *output_args, stdin=PIPE, stdout=PIPE, stderr=PIPE, text=True)

	def stop(self) -> None:
		""" Stops the FFmpeg screen capture process gracefully or kills it doesn't respond. """

		try:
			output, errors = self.process.communicate('q', timeout=10)

			for line in output.splitlines():
				log.info(f'FFmpeg output: {line}')

			for line in errors.splitlines():
				log.warning(f'FFmpeg warning/error: {line}')

		except TimeoutExpired:
			log.error('Failed to stop the screen capture gracefully.')
			self.failed = True
			self.process.kill()

	def __enter__(self):
		self.start()
		return self

	def __exit__(self, exception_type, exception_value, traceback):
		self.stop()

	def perform_post_processing(self) -> None:
		""" Converts the lossless MKV recording into a lossy MP4 video, and optionally reduces the size of the lossless copy for archival. """

		if not self.failed:

			input_args = ['-i', self.raw_path]

			output_args_list = []

			if self.archive_path is not None:
				output_args_list.append(self.config.archive_ffmpeg_output_args + [self.archive_path])

			output_args_list.append(self.config.upload_ffmpeg_output_args + [self.upload_path])

			for output_args in output_args_list:
				try:
					log.debug(f'Processing the recording with the FFmpeg arguments: {input_args + output_args}')
					ffmpeg(*input_args, *output_args)
				except FfmpegException as error:
					log.error(f'Failed to process "{self.raw_path}" with the error: {repr(error)}')
					self.failed = True
					break

		delete_file(self.raw_path)