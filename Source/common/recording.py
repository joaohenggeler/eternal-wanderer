#!/usr/bin/env python3

import dataclasses
import os
from dataclasses import dataclass
from typing import Optional

from .config import config

@dataclass
class Recording:
	""" A video recording of a Wayback Machine snapshot. """

	# From the database.
	Id: int
	SnapshotId: int
	IsProcessed: bool
	HasAudio: bool
	UploadFilename: str
	ArchiveFilename: Optional[str]
	TextToSpeechFilename: Optional[str]
	CreationTime: str
	PublishTime: Optional[str]
	TwitterMediaId: Optional[int]
	TwitterStatusId: Optional[int]
	MastodonMediaId: Optional[int]
	MastodonStatusId: Optional[int]
	TumblrStatusId: Optional[int]

	# Determined at runtime.
	UploadFilePath: str
	ArchiveFilePath: Optional[str]
	TextToSpeechFilePath: Optional[str]
	CompilationSegmentFilePath: Optional[str]

	def __init__(self, **kwargs):

		field_names = set(field.name for field in dataclasses.fields(self))
		self.__dict__.update({key: value for key, value in kwargs.items() if key in field_names})

		from .database import Database
		self.IsProcessed = Database.bool_or_none(self.IsProcessed)
		self.HasAudio = Database.bool_or_none(self.HasAudio)

		subdirectory_path = config.get_recording_subdirectory_path(self.Id)
		self.UploadFilePath = os.path.join(subdirectory_path, self.UploadFilename)
		self.ArchiveFilePath = os.path.join(subdirectory_path, self.ArchiveFilename) if self.ArchiveFilename is not None else None
		self.TextToSpeechFilePath = os.path.join(subdirectory_path, self.TextToSpeechFilename) if self.TextToSpeechFilename is not None else None
		self.CompilationSegmentFilePath = None # Set in the compilation script.