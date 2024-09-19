#!/usr/bin/env python3

import sqlite3
from datetime import datetime, timezone
from random import random
from typing import Any, Optional, Union

from .config import config
from .logger import log
from .net import is_url_key_allowed
from .snapshot import Snapshot

class Database:
	""" The database that contains all scraped snapshot metadata and their recordings. """

	connection: sqlite3.Connection

	def __init__(self):

		log.info(f'Connecting to the database in "{config.database_path}".')

		config.database_path.parent.mkdir(parents=True, exist_ok=True)
		self.connection = sqlite3.connect(config.database_path)

		def dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
			""" Converts a SQLite row into a dictionary. """
			# Adapted from: https://docs.python.org/3/library/sqlite3.html#how-to-create-and-use-row-factories
			fields = [column[0] for column in cursor.description]
			return {key: value for key, value in zip(fields, row)}

		self.connection.row_factory = dict_factory

		self.connection.execute('PRAGMA foreign_keys = ON;')
		self.connection.execute('PRAGMA journal_mode = WAL;')
		self.connection.execute('PRAGMA synchronous = NORMAL;')
		self.connection.execute('PRAGMA temp_store = MEMORY;')

		def rank_snapshot_by_points(points: Optional[int], offset: Optional[int]) -> float:
			""" Ranks a snapshot by its points so that the highest ranked one will be scouted or recorded next. """

			if offset is None:
				return random()

			# This uses a modified weighted random sampling algorithm:
			# - https://stackoverflow.com/a/56006340/18442724
			# - https://stackoverflow.com/a/51090191/18442724
			# - http://utopia.duth.gr/~pefraimi/research/data/2007EncOfAlg.pdf

			# For snapshots without a parent during scouting.
			if points is None:
				return 0

			sign = 1 if points >= 0 else -1
			return sign * random() ** (1 / (abs(points) + 1 + offset))

		self.connection.create_function('IS_URL_KEY_ALLOWED', 1, is_url_key_allowed)
		self.connection.create_function('RANK_SNAPSHOT_BY_POINTS', 2, rank_snapshot_by_points)

		# A few notes for future reference:
		#
		# The following two pages have different URLs and timestamps but their digest (i.e. content) is the same:
		# - https://web.archive.org/web/20010203164200if_/http://www.tripod.lycos.com:80/service/welcome/preferences
		# - https://web.archive.org/web/20010203180900if_/http://www.tripod.lycos.com:80/bin/membership/login
		#
		# Some examples of the Url, Timestamp, UrlKey, and Digest database columns as seen in the CDX API.
		# Notice how the UrlKey and Digest are the same, even though the URLs and timestamps are different.
		# - http://www.geocities.com/Heartland/Plains/1036/africa.gif	20090730213441	com,geocities)/heartland/plains/1036/africa.gif	RRCC3TTUVIQTMFN6BDRRIXR7OBNCGS6X
		# - http://geocities.com/Heartland/Plains/1036/africa.gif		20090820053240	com,geocities)/heartland/plains/1036/africa.gif	RRCC3TTUVIQTMFN6BDRRIXR7OBNCGS6X
		# - http://geocities.com/Heartland/Plains/1036/africa.gif		20091026145159	com,geocities)/heartland/plains/1036/africa.gif	RRCC3TTUVIQTMFN6BDRRIXR7OBNCGS6X

		self.connection.execute(f'''
								CREATE TABLE IF NOT EXISTS Snapshot
								(
									Id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
									ParentId INTEGER,
									Depth INTEGER NOT NULL,
									State INTEGER NOT NULL,
									Priority INTEGER NOT NULL DEFAULT {Snapshot.NO_PRIORITY},
									IsInitial BOOLEAN NOT NULL DEFAULT FALSE,
									IsExcluded BOOLEAN NOT NULL,
									IsMedia BOOLEAN,
									PageLanguage TEXT,
									PageTitle TEXT,
									PageUsesPlugins BOOLEAN,
									MediaExtension TEXT,
									MediaTitle TEXT,
									MediaAuthor TEXT,
									ScoutTime TIMESTAMP,
									Url TEXT NOT NULL,
									Timestamp VARCHAR(14) NOT NULL,
									LastModifiedTime VARCHAR(14),
									UrlKey TEXT,
									Digest VARCHAR(64),
									IsSensitiveOverride BOOLEAN,
									Options JSON,

									UNIQUE (Url, Timestamp)
									UNIQUE (Url, Digest)

									FOREIGN KEY (ParentId) REFERENCES Snapshot (Id)
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS Topology
								(
									ParentId INTEGER NOT NULL,
									ChildId INTEGER NOT NULL,

									PRIMARY KEY (ParentId, ChildId),
									FOREIGN KEY (ParentId) REFERENCES Snapshot (Id),
									FOREIGN KEY (ChildId) REFERENCES Snapshot (Id)
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS Word
								(
									Id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
									Word TEXT NOT NULL,
									IsTag BOOLEAN NOT NULL,
									Points INTEGER NOT NULL DEFAULT 0,
									IsSensitive BOOLEAN NOT NULL DEFAULT FALSE,

									UNIQUE (Word, IsTag)
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS SnapshotWord
								(
									SnapshotId INTEGER NOT NULL,
									WordId INTEGER NOT NULL,
									Count INTEGER NOT NULL,

									PRIMARY KEY (SnapshotId, WordId),
									FOREIGN KEY (SnapshotId) REFERENCES Snapshot (Id),
									FOREIGN KEY (WordId) REFERENCES Word (Id)
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS Config
								(
									Name TEXT NOT NULL PRIMARY KEY,
									Value TEXT NOT NULL
								);
								''')

		# Regular words should only count once, even if they appear multiple times on a page.
		self.connection.execute(f'''
								CREATE VIEW IF NOT EXISTS SnapshotInfo AS
								SELECT
									S.Id AS Id,
									CAST(MIN(SUBSTR(S.Timestamp, 1, 4), IFNULL(SUBSTR(S.LastModifiedTime, 1, 4), '9999')) AS INTEGER) AS OldestYear,
									SUBSTR(S.UrlKey, 1, INSTR(S.UrlKey, ')') - 1) AS UrlHost,
									(
										CASE WHEN S.State = {Snapshot.QUEUED} THEN NULL
										ELSE IFNULL(S.IsSensitiveOverride, IFNULL(MAX(W.IsSensitive), FALSE))
										END
									) AS IsSensitive,
									(
										CASE WHEN S.State = {Snapshot.QUEUED} THEN NULL
											 ELSE IFNULL(CASE WHEN S.IsMedia THEN (SELECT CAST(Value AS INTEGER) FROM Config WHERE Name = 'media_points')
															  WHEN W.IsTag THEN SUM(SW.Count * W.Points)
															  ELSE SUM(MIN(SW.Count, 1) * W.Points)
														 END, 0)
										END
									) AS Points
								FROM Snapshot S
								LEFT JOIN SnapshotWord SW ON S.Id = SW.SnapshotId
								LEFT JOIN Word W ON SW.WordId = W.Id
								GROUP BY S.Id;
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS SavedUrl
								(
									Id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
									SnapshotId INTEGER NOT NULL,
									RecordingId INTEGER NOT NULL,
									Url TEXT NOT NULL UNIQUE,
									Timestamp VARCHAR(14),
									Failed BOOLEAN NOT NULL,

									FOREIGN KEY (SnapshotId) REFERENCES Snapshot (Id),
									FOREIGN KEY (RecordingId) REFERENCES Recording (Id)
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS Recording
								(
									Id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
									SnapshotId INTEGER NOT NULL,
									IsProcessed BOOLEAN NOT NULL DEFAULT FALSE,
									HasAudio BOOLEAN NOT NULL,
									UploadFilename TEXT NOT NULL UNIQUE,
									ArchiveFilename TEXT UNIQUE,
									TextToSpeechFilename TEXT UNIQUE,
									CreationTime TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
									PublishTime TIMESTAMP,
									TwitterMediaId INTEGER,
									TwitterStatusId INTEGER,
									MastodonMediaId INTEGER,
									MastodonStatusId INTEGER,
									TumblrStatusId INTEGER,
									BlueskyUri TEXT,
									BlueskyCid TEXT,

									FOREIGN KEY (SnapshotId) REFERENCES Snapshot (Id)
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS Compilation
								(
									Id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
									UploadFilename TEXT NOT NULL UNIQUE,
									TimestampsFilename TEXT NOT NULL UNIQUE,
									CreationTime TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
								);
								''')

		self.connection.execute('''
								CREATE TABLE IF NOT EXISTS RecordingCompilation
								(
									RecordingId INTEGER NOT NULL,
									CompilationId INTEGER NOT NULL,
									SnapshotId INTEGER NOT NULL,
									Position INTEGER NOT NULL,

									PRIMARY KEY (RecordingId, CompilationId),
									FOREIGN KEY (RecordingId) REFERENCES Recording (Id),
									FOREIGN KEY (CompilationId) REFERENCES Compilation (Id),
									FOREIGN KEY (SnapshotId) REFERENCES Snapshot (Id)
								);
								''')

		self.connection.commit()

	def disconnect(self) -> None:
		""" Disconnects from the database. """

		try:
			self.connection.close()
		except sqlite3.Error as error:
			log.error(f'Failed to close the database with the error: {repr(error)}')

	def __enter__(self):
		return self.connection

	def __exit__(self, exception_type, exception_value, traceback):
		self.disconnect()

	@staticmethod
	def bool_or_none(value: Any) -> Union[bool, None]:
		""" Converts a SQLite boolean into the proper Python type. """
		return bool(value) if value is not None else None

	@staticmethod
	def get_current_timestamp() -> str:
		""" Retrieves the current timestamp in UTC using the same format as SQLite's CURRENT_TIMESTAMP. """
		return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

if config.debug:
	sqlite3.enable_callback_tracebacks(True)