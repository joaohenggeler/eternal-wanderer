#!/usr/bin/env python3

import dataclasses
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from random import randint
from typing import Optional
from urllib.parse import unquote, urlparse, urlunparse

from .config import config
from .logger import log
from .wayback import compose_wayback_machine_snapshot_url

@dataclass
class Snapshot:
	""" A snapshot from the Wayback Machine at a specific time and location. """

	# From the database.
	Id: int
	ParentId: Optional[int]
	Depth: int
	State: int
	Priority: int
	IsInitial: bool
	IsExcluded: bool
	IsMedia: Optional[bool]
	PageLanguage: Optional[str]
	PageTitle: Optional[str]
	PageUsesPlugins: Optional[bool]
	MediaExtension: Optional[str]
	MediaTitle: Optional[str]
	MediaAuthor: Optional[str]
	ScoutTime: Optional[str]
	Url: str
	Timestamp: str
	LastModifiedTime: Optional[str]
	UrlKey: Optional[str]
	Digest: Optional[str]
	IsSensitiveOverride: Optional[bool]
	Options: dict # Different from the database data type.

	# Determined dynamically if joined with the SnapshotInfo view.
	OldestYear: Optional[int]
	UrlHost: Optional[str]
	IsSensitive: Optional[bool]
	Points: Optional[int]

	# Determined from the Options column.
	Emojis: list[str]
	Encoding: str
	MediaExtensionOverride: Optional[str]
	Notes: str
	Script: Optional[str]
	Tags: list[str]
	TitleOverride: Optional[str]

	# Determined at runtime.
	PriorityName: str
	WaybackUrl: str
	OldestTimestamp: str
	OldestDatetime: datetime
	ShortDate: str
	DisplayTitle: str
	DisplayMetadata: Optional[str]
	LanguageName: Optional[str]

	# Constants. Each of these must be greater than the last.
	QUEUED = 0
	INVALID = 1
	SCOUTED = 2
	ABORTED = 3
	RECORDED = 4
	REJECTED = 5
	APPROVED = 6
	PUBLISHED = 7
	WITHHELD = 8

	STATE_NAMES: dict[int, str]

	PRIORITY_SIZE = 1000
	NO_PRIORITY = 0
	SCOUT_PRIORITY = 1 * PRIORITY_SIZE
	RECORD_PRIORITY = 2 * PRIORITY_SIZE
	PUBLISH_PRIORITY = 3 * PRIORITY_SIZE

	TIMESTAMP_FORMAT = '%Y%m%d%H%M%S'

	IFRAME_MODIFIER = 'if_'
	OBJECT_EMBED_MODIFIER = 'oe_'
	IDENTICAL_MODIFIER = 'id_'

	def __init__(self, **kwargs):

		self.OldestYear = None
		self.UrlHost = None
		self.IsSensitive = None
		self.Points = None

		field_names = set(field.name for field in dataclasses.fields(self))
		self.__dict__.update({key: value for key, value in kwargs.items() if key in field_names})

		from .database import Database
		self.IsInitial = Database.bool_or_none(self.IsInitial)
		self.IsExcluded = Database.bool_or_none(self.IsExcluded)
		self.IsMedia = Database.bool_or_none(self.IsMedia)
		self.PageUsesPlugins = Database.bool_or_none(self.PageUsesPlugins)
		self.IsSensitiveOverride = Database.bool_or_none(self.IsSensitiveOverride)
		self.IsSensitive = Database.bool_or_none(self.IsSensitive)

		if self.Options is not None:
			try:
				self.Options = json.loads(self.Options)
			except json.JSONDecodeError as error:
				log.error(f'Failed to load the options for the snapshot {self} with the error: {repr(error)}')
				self.Options = {}
		else:
			self.Options = {}

		self.Emojis = self.Options.get('emojis', [])
		self.Encoding = self.Options.get('encoding', '')
		self.MediaExtensionOverride = self.Options.get('media_extension_override')
		self.Notes = self.Options.get('notes', '')
		self.Script = self.Options.get('script')
		self.Tags = self.Options.get('tags', [])
		self.TitleOverride = self.Options.get('title_override')

		# In some rare cases, media snapshots can have incorrect file extensions.
		# To solve this, we'll allow forcing the media conversion by overriding
		# the original extension.
		# E.g. https://web.archive.org/web/19961029094219if_/http://www.asiaonline.net:80/comradio/news.ram
		# Which should be news.rm.
		if self.MediaExtensionOverride is not None:
			self.MediaExtension = self.MediaExtensionOverride

		self.PriorityName = Snapshot.get_priority_name(self.Priority)

		modifier = Snapshot.OBJECT_EMBED_MODIFIER if self.IsMedia else Snapshot.IFRAME_MODIFIER
		self.WaybackUrl = compose_wayback_machine_snapshot_url(timestamp=self.Timestamp, modifier=modifier, url=self.Url)

		# If the last modified time is older than the first website (August 1991)
		# or if it's newer than the archival date, use the snapshot's timestamp.
		# See: https://en.wikipedia.org/wiki/List_of_websites_founded_before_1995
		#
		# E.g. https://web.archive.org/web/19961111002723if_/http://www.metamor.com:80/pages/missioncontrol/mission_control.html
		# Where the last modified time is 19800501233128 (too old).
		# E.g. https://web.archive.org/web/19961222034448if_/http://panter.soci.aau.dk:80/
		# Where the last modified time is 20090215174615 (too new).
		if self.LastModifiedTime is not None and self.LastModifiedTime >= '1991':
			self.OldestTimestamp = min(self.Timestamp, self.LastModifiedTime)
		else:
			self.OldestTimestamp = self.Timestamp

		self.OldestDatetime = datetime.strptime(self.OldestTimestamp, Snapshot.TIMESTAMP_FORMAT)

		# How the date is formatted depends on the current locale.
		self.ShortDate = self.OldestDatetime.strftime('%b %Y')

		self.DisplayTitle = self.TitleOverride or self.PageTitle

		if not self.DisplayTitle:
			parts = urlparse(unquote(self.Url))
			self.DisplayTitle = Path(parts.path).name

			if not self.DisplayTitle:
				new_parts = parts._replace(netloc=parts.hostname, params='', query='', fragment='')
				self.DisplayTitle = urlunparse(new_parts)

		if self.MediaTitle and self.MediaAuthor:
			self.DisplayMetadata = f'"{self.MediaTitle}" by "{self.MediaAuthor}"'
		elif self.MediaTitle:
			self.DisplayMetadata = f'"{self.MediaTitle}"'
		elif self.MediaAuthor:
			self.DisplayMetadata =  f'By "{self.MediaAuthor}"'
		else:
			self.DisplayMetadata = None

		self.LanguageName = config.language_names.get(self.PageLanguage, self.PageLanguage) if self.PageLanguage is not None else None

	def __str__(self):
		return f'({self.Url}, {self.Timestamp})'

	@staticmethod
	def get_priority_name(priority: int) -> str:
		""" Retrieves the name of a priority from its value. """

		priority //= Snapshot.PRIORITY_SIZE

		if priority == 0:
			name = 'None'
		elif priority == 1:
			name = 'Scout'
		elif priority == 2:
			name = 'Record'
		elif priority == 3:
			name = 'Publish'
		else:
			name = 'Unknown'

		return name

	@staticmethod
	def randomize_priority(priority: int) -> int:
		""" Randomizes a priority without changing its type. """
		min_priority = priority // Snapshot.PRIORITY_SIZE * Snapshot.PRIORITY_SIZE
		max_priority = min_priority + Snapshot.PRIORITY_SIZE - 1
		return randint(min_priority, max_priority)

Snapshot.STATE_NAMES = {
	Snapshot.QUEUED: 'Queued',
	Snapshot.INVALID: 'Invalid',
	Snapshot.SCOUTED: 'Scouted',
	Snapshot.ABORTED: 'Aborted',
	Snapshot.RECORDED: 'Recorded',
	Snapshot.REJECTED: 'Rejected',
	Snapshot.APPROVED: 'Approved',
	Snapshot.PUBLISHED: 'Published',
	Snapshot.WITHHELD: 'Withheld',
}