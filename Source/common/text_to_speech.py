#!/usr/bin/env python3

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO, Optional

from comtypes import COMError # type: ignore
from comtypes.client import CreateObject # type: ignore

# We need to create a speech engine at least once before importing SpeechLib. Otherwise, we'd get an ImportError.
CreateObject('SAPI.SpVoice')
from comtypes.gen import SpeechLib # type: ignore

from .config import CommonConfig
from .ffmpeg import ffmpeg, FfmpegException
from .logger import log
from .util import delete_file

class TextToSpeech:
	""" A wrapper for the Microsoft Speech API and FFmpeg that generates a text-to-speech recording. """

	config: CommonConfig

	engine: SpeechLib.ISpeechVoice
	stream: SpeechLib.ISpeechFileStream
	temporary_file: BinaryIO

	language_to_voice: dict[Optional[str], SpeechLib.ISpeechObjectToken]

	def __init__(self, config: CommonConfig):

		self.config = config

		# See:
		# - https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ms723602(v=vs.85)
		# - https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ms722561(v=vs.85)
		self.engine = CreateObject('SAPI.SpVoice')
		self.stream = CreateObject('SAPI.SpFileStream')

		# We have to close the temporary file so SpFileStream.Open() doesn't fail.
		self.temporary_file = NamedTemporaryFile(mode='wb', prefix=CommonConfig.TEMPORARY_PATH_PREFIX, suffix='.wav', delete=False)
		self.temporary_file.close()

		try:
			if self.config.text_to_speech_audio_format_type is not None:
				self.engine.AllowOutputFormatChangesOnNextSet = False
				self.stream.Format.Type = getattr(SpeechLib, self.config.text_to_speech_audio_format_type)
		except AttributeError:
			log.error(f'Could not find the audio format type "{self.config.text_to_speech_audio_format_type}".')

		if self.config.text_to_speech_rate is not None:
			self.engine.Rate = self.config.text_to_speech_rate

		voices = {}
		for voice in self.engine.GetVoices():
			name = voice.GetAttribute('Name')
			voices[name] = voice

			language = voice.GetAttribute('Language')
			gender = voice.GetAttribute('Gender')
			age = voice.GetAttribute('Age')
			vendor = voice.GetAttribute('Vendor')
			description = voice.GetDescription()
			log.info(f'Found the text-to-speech voice ({name}, {language}, {gender}, {age}, {vendor}): "{description}".')

		default_voice = self.engine.Voice
		if self.config.text_to_speech_default_voice is not None:
			default_voice = next((voice for name, voice in voices.items() if self.config.text_to_speech_default_voice.lower() in name.lower()), default_voice)

		self.language_to_voice = defaultdict(lambda: default_voice)

		for language, voice_name in self.config.text_to_speech_language_voices.items():
			voice = next((voice for name, voice in voices.items() if voice_name.lower() in name.lower()), None)
			if voice is not None:
				self.language_to_voice[language] = voice

	def generate_text_to_speech_file(self, title: str, date: datetime, text: str, language: Optional[str], path_prefix: Path) -> Optional[str]:
		""" Generates a video file that contains the text-to-speech in the audio track and a blank screen on the video one.
		The voice used by the Speech API is specified in the configuration file and depends on the page's language.
		The correct voice packages have been installed on Windows, otherwise a default voice is used instead. """

		# Add some context XML so the date is spoken correctly no matter the language.
		# See: https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ee125665(v=vs.85)
		date_xml = f'<context id="date_ymd">{date.year}/{date.month}/{date.day}</context>'

		extension = f'.tts.{language}.mp4' if language is not None else '.tts.mp4'
		output_path: Optional[str] = Path(str(path_prefix) + extension)

		try:
			# See:
			# - https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ms720858(v=vs.85)
			# - https://docs.microsoft.com/en-us/previous-versions/windows/desktop/ms720892(v=vs.85)
			self.stream.Open(self.temporary_file.name, SpeechLib.SSFMCreateForWrite)
			self.engine.AudioOutputStream = self.stream
			self.engine.Voice = self.language_to_voice[language]
			self.engine.Speak(title, SpeechLib.SVSFPurgeBeforeSpeak | SpeechLib.SVSFIsNotXML)
			self.engine.Speak(date_xml, SpeechLib.SVSFPurgeBeforeSpeak | SpeechLib.SVSFIsXML)
			self.engine.Speak(text, SpeechLib.SVSFPurgeBeforeSpeak | SpeechLib.SVSFIsNotXML)
			self.stream.Close()

			video_input_args = self.config.text_to_speech_ffmpeg_video_input_args + ['-i', self.config.text_to_speech_ffmpeg_video_input_name]
			audio_input_args = self.config.text_to_speech_ffmpeg_audio_input_args + ['-i', self.temporary_file.name]
			output_args = self.config.text_to_speech_ffmpeg_output_args + [output_path]

			log.debug(f'Generating the text-to-speech file with the FFmpeg arguments: {video_input_args + audio_input_args + output_args}')
			ffmpeg(*video_input_args, *audio_input_args, *output_args)

		except (COMError, FfmpegException) as error:
			log.error(f'Failed to generate the text-to-speech file "{output_path}" with the error: {repr(error)}')
			output_path = None

		return output_path

	def cleanup(self) -> None:
		""" Deletes the temporary WAV file created by the Speech API. """
		delete_file(self.temporary_file.name)