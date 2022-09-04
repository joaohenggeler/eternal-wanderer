#!/usr/bin/env python3

import os
import shutil
import sqlite3
from argparse import ArgumentParser
from glob import iglob

from common import CommonConfig, Database, Recording, Snapshot

if __name__ == '__main__':

	parser = ArgumentParser(description='Shows snapshot and recording statistics from the database.')
	args = parser.parse_args()

	config = CommonConfig()

	with Database() as db:

		try:
			cursor = db.execute('''
								SELECT
									(SELECT COUNT(*) FROM Snapshot) AS TotalSnapshots,
									(SELECT COUNT(*) FROM Recording) AS TotalRecordings,
									(SELECT COUNT(*) FROM SavedUrl) AS TotalSaved;
								''')
			row = cursor.fetchone()
			total_snapshots, total_recordings, total_saved = row['TotalSnapshots'], row['TotalRecordings'], row['TotalSaved']

			cursor = db.execute('SELECT State, COUNT(*) AS Total FROM Snapshot GROUP BY State ORDER BY State;')
			state_total = {row['State']: row['Total'] for row in cursor}

			print(f'Snapshots ({total_snapshots}):')
			print()

			for state, name in Snapshot.STATE_NAMES.items():
				total = state_total.get(state, 0)
				print(f'- {name}: {total} ({total / total_snapshots * 100:.2f}%)')

			print()

			cursor = db.execute('SELECT IsStandaloneMedia, COUNT(*) AS Total FROM Snapshot GROUP BY IsStandaloneMedia ORDER BY IsStandaloneMedia;')
			type_total = {row['IsStandaloneMedia']: row['Total'] for row in cursor}

			for type, name in [(0, 'Pages'), (1, 'Media'), (None, 'Excluded')]:
				total = type_total.get(type, 0)
				print(f'- {name}: {total} ({total / total_snapshots * 100:.2f}%)')

			print()

			cursor = db.execute('SELECT IsProcessed, COUNT(*) AS Total FROM Recording GROUP BY IsProcessed ORDER BY IsProcessed;')
			recording_total = {row['IsProcessed']: row['Total'] for row in cursor}

			print(f'Recordings ({total_recordings}):')
			print()

			for is_processed, name in [(0, 'Unprocessed'), (1, 'Processed')]:
				total = recording_total.get(is_processed, 0)
				print(f'- {name}: {total} ({total / total_recordings * 100:.2f}%)')

			print()

			queries = [
				('Last Created', 'SELECT *, R.Id AS RecordingId, R.CreationTime AS LastTime FROM Snapshot S INNER JOIN Recording R ON S.Id = R.SnapshotId ORDER BY CreationTime DESC LIMIT 1;'),
				('Last Published (Twitter)', 'SELECT *, R.Id AS RecordingId, R.PublishTime AS LastTime FROM Snapshot S INNER JOIN Recording R ON S.Id = R.SnapshotId WHERE TwitterPostId IS NOT NULL ORDER BY PublishTime DESC LIMIT 1;'),
				('Last Published (Mastodon)', 'SELECT *, R.Id AS RecordingId, R.PublishTime AS LastTime FROM Snapshot S INNER JOIN Recording R ON S.Id = R.SnapshotId WHERE MastodonPostId IS NOT NULL ORDER BY PublishTime DESC LIMIT 1;'),
			]

			for name, query in queries:
				cursor = db.execute(query)
				row = cursor.fetchone()

				if row is not None:
					row = dict(row)
							
					# Avoid naming conflicts with each table's primary key.
					del row['Id']
					snapshot = Snapshot(**row, Id=row['SnapshotId'])
					recording = Recording(**row, Id=row['RecordingId'])

					last_time = row['LastTime']
					twitter_url = f'https://twitter.com/waybackwanderer/status/{recording.TwitterPostId}' if recording.TwitterPostId is not None else '-'
					mastodon_url = f'https://botsin.space/web/@eternalwanderer/{recording.MastodonPostId}' if recording.MastodonPostId is not None else '-'

					print(f'- {name}: {last_time}')
					print(f'-> Snapshot: {snapshot}')
					print(f'-> Twitter: {twitter_url}')
					print(f'-> Mastodon: {mastodon_url}')
				else:
					print(f'- {name}: -')

				print()

			total_recordings_size: float = 0
			for path in iglob(os.path.join(config.recordings_path, '**'), recursive=True):
				if os.path.isfile(path):
					total_recordings_size += os.path.getsize(path)

			total_recordings_size = total_recordings_size / 10 ** 9
			total_disk_space, _, free_disk_space = (size / 10 ** 9 for size in shutil.disk_usage('/'))
			
			print(f'- Recordings Disk Space: {total_recordings_size:.2f} of {total_disk_space:.2f} GB ({free_disk_space:.2f} free).')

			print()

			cursor = db.execute('SELECT Failed, COUNT(*) AS Total FROM SavedUrl GROUP BY Failed ORDER BY Failed;')
			saved_urls_total = {row['Failed']: row['Total'] for row in cursor}

			print(f'Saved URLs ({total_saved}):')
			print()

			for status, name in [(0, 'Saved'), (1, 'Failed')]:
				total = saved_urls_total.get(status, 0)
				print(f'- {name}: {total} ({total / total_saved * 100:.2f}%)')

		except sqlite3.Error as error:
			print(f'Failed to query the database with the error: {repr(error)}')