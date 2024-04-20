#!/usr/bin/env python3

import json
import os
import re
import subprocess
from json import JSONDecodeError
from pathlib import Path
from subprocess import Popen

from .config import config

class FfmpegException(Exception):
	pass

def _run(*args) -> tuple[str, str]:
	""" @TODO """

	args = [str(arg) for arg in args]
	process = subprocess.run(args, capture_output=True, text=True)

	if process.returncode != 0:
		raise FfmpegException(process.stderr.rstrip('\n'))

	return process.stdout.rstrip('\n'), process.stderr.rstrip('\n')

def ffmpeg(*args, log_level='warning') -> tuple[str, str]:
	""" @TODO """
	return _run('ffmpeg', *args, '-y', '-loglevel', log_level, '-hide_banner', '-nostats')

def ffprobe(*args) -> str:
	""" @TODO """
	output, _ = _run('ffprobe', *args, '-loglevel', 'error')
	return output

def ffmpeg_process(*args, **kwargs) -> Popen:
	""" @TODO """
	args = ['ffmpeg'] + [str(arg) for arg in args] + ['-y', '-loglevel', 'warning', '-hide_banner', '-nostats']

	try:
		return Popen(args, **kwargs)
	except OSError as error:
		raise FfmpegException(error)

# E.g. "[silencedetect @ 0000022c2f32bf40] silence_end: 4.54283 | silence_duration: 0.377167"
SILENCE_DURATION_REGEX = re.compile(r'^\[silencedetect.*silence_duration: (?P<duration>\d+\.\d+)', re.MULTILINE)

def ffmpeg_detect_audio(path: Path) -> bool:
	""" @TODO """

	# We need the log level set to info in order to get the filter's output.
	# The minimum silence duration should be under one second so we can detect
	# audio in short media files. The filter's output goes to stderr.
	# E.g. https://web.archive.org/web/19961106150353if_/http://www.dnai.com:80/~sharrow/wav/frog.wav
	input_args = ['-i', path]
	output_args = ['-f', 'null', '-af', 'silencedetect=duration=0.1', '-']
	_, output = ffmpeg(*input_args, *output_args, log_level='info')

	# From testing, the difference between the durations in silent files is usually
	# under 0.1 seconds, so we'll increase this threshold for good measure. If FFmpeg
	# couldn't detect any silence, we still have to check if the file has an audio
	# stream because it might be a video-only media file.
	# E.g. https://web.archive.org/web/19970119195540if_/http://www.gwha.com:80/dynimg/lapse.mpeg
	match = SILENCE_DURATION_REGEX.search(output)

	if match is not None:
		total_duration = ffprobe_duration(path)
		silence_duration = float(match['duration'])
		has_audio = abs(total_duration - silence_duration) > 0.2
	else:
		has_audio = ffprobe_has_audio_stream(path)

	return has_audio

def ffprobe_duration(path: Path) -> float:
	""" @TODO """
	try:
		duration = ffprobe('-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', path)
		return float(duration)
	except ValueError as error:
		raise FfmpegException(error)

def ffprobe_info(path: Path) -> dict:
	""" @TODO """
	try:
		info = ffprobe('-show_format', '-show_streams', '-of', 'json', path)
		return json.loads(info)
	except JSONDecodeError as error:
		raise FfmpegException(error)

def ffprobe_has_video_stream(path: Path) -> bool:
	""" @TODO """
	info = ffprobe_info(path)
	return any(stream for stream in info['streams'] if stream['codec_type'] == 'video')

def ffprobe_has_audio_stream(path: Path) -> bool:
	""" @TODO """
	info = ffprobe_info(path)
	return any(stream for stream in info['streams'] if stream['codec_type'] == 'audio')

if config.ffmpeg_path is not None:
	os.environ['PATH'] = str(config.ffmpeg_path) + ';' + os.environ.get('PATH', '')