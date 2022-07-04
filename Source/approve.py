#!/usr/bin/env python3

"""
	This script approves snapshot recordings for publishing.
	This operation is optional and may only be done if the publisher script was started with the "require_approval" option set to true.
"""

import os
import sqlite3
from argparse import ArgumentParser
from typing import Optional

from common import Database, Recording, Snapshot
from publish import PublishConfig

####################################################################################################

if __name__ == '__main__':

	config = PublishConfig()

	parser = ArgumentParser(description='Approves snapshot recordings for publishing. This operation is optional and may be done if the publisher script was started with the "require_approval" option set to true.')
	parser.add_argument('max_recordings', nargs='?', type=int, default=-1, help='How many recordings to approve. Omit or set to %(default)s to approve all recordings.')
	args = parser.parse_args()

	if not config.require_approval:
		parser.error('This script can only be used if the "require_approval" option is set to true.')

	with Database() as db:
		
		try:
			cursor = db.execute('''
								SELECT S.*, R.*, R.Id AS RecordingId
								FROM Snapshot S
								INNER JOIN Recording R ON S.Id = R.SnapshotId
								WHERE S.State = :recorded_state AND NOT R.IsProcessed
								ORDER BY R.CreationTime
								LIMIT :max_recordings;
								''', {'recorded_state': Snapshot.RECORDED, 'max_recordings': args.max_recordings})

			snapshot_updates = []
			recording_updates = []

			total_snapshots = 0
			num_approved = 0
			num_rejected = 0
			num_to_record_again = 0
			num_missing = 0

			for row in cursor:

				total_snapshots += 1

				row = dict(row)
				del row['Id']
				snapshot = Snapshot(**row, Id=row['SnapshotId'])
				recording = Recording(**row, Id=row['RecordingId'])

				try:
					print(f'Evaluating the recording "{recording.UploadFilename}" for snapshot #{snapshot.Id} {snapshot} entitled "{snapshot.DisplayTitle}" (metadata = {snapshot.DisplayMetadata}).')
					os.startfile(recording.UploadFilePath)
				except FileNotFoundError:
					print('The recording file does not exist.')
					num_missing += 1
					continue

				while True:
					verdict = input('Verdict [(y)es, (n)o, (r)ecord again]: ').lower()

					if not verdict:
						continue
					
					elif verdict[0] == 'y':
						state = Snapshot.APPROVED
						priority = snapshot.Priority
						is_processed = recording.IsProcessed
						num_approved += 1

					elif verdict[0] == 'n':
						state = Snapshot.REJECTED
						priority = snapshot.NO_PRIORITY
						is_processed = True
						num_rejected += 1

					elif verdict[0] == 'r':
						state = Snapshot.SCOUTED
						priority = max(snapshot.Priority, Snapshot.RECORD_PRIORITY)
						is_processed = True
						num_to_record_again += 1

					else:
						print(f'Invalid input "{verdict}".')
						continue

					is_sensitive_override: Optional[bool]

					while True:
						sensitive = input('Sensitive [(y)es, (n)o, (s)kip]: ').lower()

						if not sensitive:
							continue

						elif sensitive[0] == 'y':
							is_sensitive_override = True
						elif sensitive[0] == 'n':
							is_sensitive_override = False
						elif sensitive[0] == 's':
							is_sensitive_override = snapshot.IsSensitiveOverride
						else:
							print(f'Invalid input "{sensitive}".')
							continue

						break

					snapshot_updates.append({'state': state, 'priority': priority, 'is_sensitive_override': is_sensitive_override, 'id': snapshot.Id})
					recording_updates.append({'is_processed': is_processed, 'id': recording.Id})
					break

			if total_snapshots > 0:

				db.executemany('UPDATE Snapshot SET State = :state, Priority = :priority, IsSensitiveOverride = :is_sensitive_override WHERE Id = :id;', snapshot_updates)
				db.executemany('UPDATE Recording SET IsProcessed = :is_processed WHERE Id = :id;', recording_updates)
				db.commit()
				
				print(f'Evaluated {total_snapshots} snapshots: {num_approved} approved, {num_rejected} rejected, {num_to_record_again} to be recorded again, {num_missing} missing files.')

			else:
				print('Ran out of snapshots to approve.')

		except sqlite3.Error as error:
			print(f'Failed to approve the recorded snapshots with the error: {repr(error)}')
			db.rollback()

	print('Finished running.')