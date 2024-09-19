#!/usr/bin/env python3

import os
import shutil
import sqlite3
from argparse import ArgumentParser

from common.config import config
from common.database import Database
from common.recording import Recording
from common.snapshot import Snapshot

if __name__ == '__main__':

	parser = ArgumentParser(description='Shows snapshot and recording statistics from the database.')
	args = parser.parse_args()

	with Database() as db:

		try:
			print('Database:')
			print()

			database_size = os.path.getsize(config.database_path) / 10 ** 9
			print(f'- Path: {config.database_path}')
			print(f'- Size: {database_size:.2f} GB')

			print()

			cursor = db.execute('''
								SELECT
									(SELECT COUNT(*) FROM Snapshot) AS TotalSnapshots,
									(SELECT COUNT(*) FROM Recording) AS TotalRecordings,
									(SELECT COUNT(*) FROM Compilation) AS TotalCompilations,
									(SELECT COUNT(*) FROM SavedUrl) AS TotalSaved;
								''')

			row = cursor.fetchone()
			total_snapshots = row['TotalSnapshots']
			total_recordings = row['TotalRecordings']
			total_compilations = row['TotalCompilations']
			total_saved = row['TotalSaved']

			print(f'Snapshots ({total_snapshots}):')
			print()

			cursor = db.execute('SELECT State, COUNT(*) AS Total FROM Snapshot GROUP BY State ORDER BY State;')
			state_total = {row['State']: row['Total'] for row in cursor}

			for state, name in Snapshot.STATE_NAMES.items():
				total = state_total.get(state, 0)
				percent = total / total_snapshots * 100 if total_snapshots > 0 else 0
				print(f'- {name}: {total} ({percent:.2f}%)')

			print()

			cursor = db.execute('SELECT IsMedia, COUNT(*) AS Total FROM Snapshot GROUP BY IsMedia ORDER BY IsMedia;')
			type_total = {row['IsMedia']: row['Total'] for row in cursor}

			for type, name in [(0, 'Pages'), (1, 'Media'), (None, 'Excluded')]:
				total = type_total.get(type, 0)
				percent = total / total_snapshots * 100 if total_snapshots > 0 else 0
				print(f'- {name}: {total} ({percent:.2f}%)')

			print()

			cursor = db.execute('SELECT COUNT(*) AS Total FROM Snapshot WHERE PageUsesPlugins IS NOT NULL;')
			row = cursor.fetchone()
			total_can_use_plugins = row['Total']

			percent = total_can_use_plugins / total_snapshots * 100 if total_snapshots > 0 else 0
			print(f'- Can Use Plugins: {total_can_use_plugins} ({percent:.2f}%)')

			cursor = db.execute('SELECT PageUsesPlugins, COUNT(*) AS Total FROM Snapshot WHERE PageUsesPlugins IS NOT NULL GROUP BY PageUsesPlugins ORDER BY PageUsesPlugins;')
			plugin_total = {row['PageUsesPlugins']: row['Total'] for row in cursor}

			for uses_plugins, name in [(0, 'No Plugins'), (1, 'Uses Plugins')]:
				total = plugin_total.get(uses_plugins, 0)
				percent = total / total_can_use_plugins * 100 if total_can_use_plugins > 0 else 0
				print(f'-> {name}: {total} ({percent:.2f}%)')

			print()

			cursor = db.execute('SELECT COUNT(*) AS Total FROM Snapshot WHERE Priority > 0;')
			row = cursor.fetchone()
			total_prioritized = row['Total']

			percent = total_prioritized / total_snapshots * 100 if total_snapshots > 0 else 0
			print(f'- Prioritized: {total_prioritized} ({percent:.2f}%)')

			cursor = db.execute('''
								SELECT Priority / :priority_size AS PriorityKey, COUNT(*) AS Total
								FROM Snapshot
								WHERE Priority > 0
								GROUP BY PriorityKey
								ORDER BY PriorityKey;
								''',
								{'priority_size': Snapshot.PRIORITY_SIZE})

			priority_total = {row['PriorityKey']: row['Total'] for row in cursor}

			for key, total in priority_total.items():
				priority = key * Snapshot.PRIORITY_SIZE
				name = Snapshot.get_priority_name(priority)
				percent = total / total_snapshots * 100 if total_snapshots > 0 else 0
				print(f'-> {name}: {total} ({percent:.2f}%)')

			print()

			print(f'Recordings ({total_recordings}):')
			print()

			cursor = db.execute('SELECT IsProcessed, COUNT(*) AS Total FROM Recording GROUP BY IsProcessed ORDER BY IsProcessed;')
			processed_total = {row['IsProcessed']: row['Total'] for row in cursor}

			for is_processed, name in [(0, 'Unprocessed'), (1, 'Processed')]:
				total = processed_total.get(is_processed, 0)
				percent = total / total_recordings * 100 if total_recordings > 0 else 0
				print(f'- {name}: {total} ({percent:.2f}%)')

			print()

			cursor = db.execute('SELECT HasAudio, COUNT(*) AS Total FROM Recording GROUP BY HasAudio ORDER BY HasAudio;')
			audio_total = {row['HasAudio']: row['Total'] for row in cursor}

			for has_audio, name in [(0, 'Silent'), (1, 'Audible')]:
				total = audio_total.get(has_audio, 0)
				percent = total / total_recordings * 100 if total_recordings > 0 else 0
				print(f'- {name}: {total} ({percent:.2f}%)')

			print()

			queries = [
				('Last Created', 'SELECT S.*, R.*, R.Id AS RecordingId, R.CreationTime AS LastTime FROM Snapshot S INNER JOIN Recording R ON S.Id = R.SnapshotId ORDER BY CreationTime DESC LIMIT 1;'),
				('Last Published', 'SELECT S.*, R.*, R.Id AS RecordingId, R.PublishTime AS LastTime FROM Snapshot S INNER JOIN Recording R ON S.Id = R.SnapshotId ORDER BY PublishTime DESC LIMIT 1;'),
			]

			for name, query in queries:

				cursor = db.execute(query)
				row = cursor.fetchone()

				if row is not None:
					# Avoid naming conflicts with each table's primary key.
					del row['Id']
					snapshot = Snapshot(**row, Id=row['SnapshotId'])
					recording = Recording(**row, Id=row['RecordingId'])

					last_time = row['LastTime']
					twitter_url = f'https://twitter.com/waybackwanderer/status/{recording.TwitterStatusId}' if recording.TwitterStatusId is not None else '-'
					mastodon_url = f'https://botsin.space/@eternalwanderer/{recording.MastodonStatusId}' if recording.MastodonStatusId is not None else '-'
					tumblr_url = f'https://www.tumblr.com/waybackwanderer/{recording.TumblrStatusId}' if recording.TumblrStatusId is not None else '-'
					bluesky_url = recording.BlueskyUri if recording.BlueskyUri is not None else '-'

					print(f'- {name}: {last_time}')
					print(f'-> Snapshot: #{snapshot.Id} {snapshot}')
					print(f'-> Recording: {recording.UploadFilename}')
					print(f'-> Twitter: {twitter_url}')
					print(f'-> Mastodon: {mastodon_url}')
					print(f'-> Tumblr: {tumblr_url}')
					print(f'-> Bluesky: {bluesky_url}')
				else:
					print(f'- {name}: -')

				print()

			total_recordings_size: float = 0
			for path in config.recordings_path.rglob('*'):
				if path.is_file():
					total_recordings_size += os.path.getsize(path)

			total_recordings_size = total_recordings_size / 10 ** 9
			total_disk_space, _, free_disk_space = (size / 10 ** 9 for size in shutil.disk_usage('/'))

			print(f'- Recordings Disk Space: {total_recordings_size:.2f} of {total_disk_space:.2f} GB ({free_disk_space:.2f} free)')
			print()

			print(f'- Compilations: {total_compilations}')
			print()

			cursor = db.execute('SELECT Failed, COUNT(*) AS Total FROM SavedUrl GROUP BY Failed ORDER BY Failed;')
			saved_urls_total = {row['Failed']: row['Total'] for row in cursor}

			print(f'Saved URLs ({total_saved}):')
			print()

			for status, name in [(0, 'Saved'), (1, 'Failed')]:
				total = saved_urls_total.get(status, 0)
				percent = total / total_saved * 100 if total_saved > 0 else 0
				print(f'- {name}: {total} ({percent:.2f}%)')

		except sqlite3.Error as error:
			print(f'Failed to query the database with the error: {repr(error)}')