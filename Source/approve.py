#!/usr/bin/env python3

import os
import sqlite3
from argparse import ArgumentParser
from collections.abc import Iterator

from common.database import Database
from common.recording import Recording
from common.snapshot import Snapshot
from publish import PublishConfig

if __name__ == '__main__':

	parser = ArgumentParser(description='Approves recordings for publishing. This process is optional and can only be done if the publisher script was started with the "require_approval" option enabled.')
	parser.add_argument('max_recordings', nargs='?', type=int, default=-1, help='How many recordings to approve. Omit or set to %(default)s to approve all recordings.')
	parser.add_argument('-tts', action='store_true', help='Play the text-to-speech audio files after each recording.')
	parser.add_argument('-alternate', action='store_true', help='Alternate recordings between the beginning and the end.')
	parser.add_argument('-randomize', action='store_true', help='Randomize the order in which approved recordings are published. Cannot be used with -customize.')
	parser.add_argument('-customize', action='store_true', help='Ask for the order in which approved recordings are published. Cannot be used with -randomize.')
	parser.add_argument('-withhold', action='store_true', help='Prevent approved recordings from being published right away. Useful for storing recordings for future events.')
	parser.add_argument('-redo', action='store_true', help='Record every snapshot again.')
	args = parser.parse_args()

	config = PublishConfig()

	if not config.require_approval:
		parser.error('This script can only be used if the "require_approval" option is enabled.')

	if args.randomize and args.customize:
		parser.error('The -randomize and -customize options cannot be used at the same time.')

	with Database() as db:

		try:
			cursor = db.execute('''
								SELECT 	S.*,
										SI.*,
										R.*,
										R.Id AS RecordingId,
										IFNULL((SELECT COUNT(*) FROM SavedUrl SU WHERE SU.RecordingId = R.Id AND NOT SU.Failed GROUP BY SU.RecordingId), 0) AS SavedRecordingUrls,
										IFNULL((SELECT COUNT(*) FROM SavedUrl SU WHERE SU.RecordingId = R.Id GROUP BY SU.RecordingId), 0) AS TotalRecordingUrls,
										IFNULL((SELECT COUNT(*) FROM SavedUrl SU WHERE SU.SnapshotId = S.Id AND NOT SU.Failed GROUP BY SU.SnapshotId), 0) AS SavedSnapshotUrls,
										IFNULL((SELECT COUNT(*) FROM SavedUrl SU WHERE SU.SnapshotId = S.Id GROUP BY SU.SnapshotId), 0) AS TotalSnapshotUrls
								FROM Snapshot S
								INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
								INNER JOIN Recording R ON S.Id = R.SnapshotId
								WHERE S.State = :recorded_state AND NOT R.IsProcessed
								ORDER BY S.Priority DESC, R.CreationTime
								LIMIT :max_recordings;
								''',
								{'recorded_state': Snapshot.RECORDED, 'max_recordings': args.max_recordings})

			row_list = cursor.fetchall()
			total_recordings = len(row_list)

			snapshot_updates = []
			recording_updates = []

			num_approved = 0
			num_rejected = 0
			num_to_record_again = 0
			num_missing = 0

			def alternate(items: list) -> Iterator:
				""" Alternates elements between the beginning and the end of a list. """
				for i in range(len(items)):
					yield items[i//2] if i % 2 == 0 else items[-i//2]

			def record_snapshot_again(snapshot: Snapshot, recording: Recording) -> None:
				""" Tells the other scripts that this snapshot must be recorded again as soon as possible. """

				state = Snapshot.SCOUTED
				priority = max(snapshot.Priority, Snapshot.MIN_RECORD_PRIORITY)
				is_sensitive_override = snapshot.IsSensitiveOverride
				is_processed = True

				snapshot_updates.append({'state': state, 'priority': priority, 'is_sensitive_override': is_sensitive_override, 'id': snapshot.Id})
				recording_updates.append({'is_processed': is_processed, 'id': recording.Id})

			row_iter = alternate(row_list) if args.alternate else row_list

			for i, row in enumerate(row_iter, start=1):

				del row['Id']
				snapshot = Snapshot(**row, Id=row['SnapshotId'])
				recording = Recording(**row, Id=row['RecordingId'])

				if args.redo:
					record_snapshot_again(snapshot, recording)
					num_to_record_again += 1
					continue

				if snapshot.IsSensitiveOverride is not None:
					sensitive_reason = '(overridden)'
				elif snapshot.IsSensitive:
					cursor = db.execute('''
										SELECT W.Word
						 				FROM Snapshot S
						 				INNER JOIN SnapshotWord SW ON S.Id = SW.SnapshotId
						 				INNER JOIN Word W ON SW.WordId = W.Id
						 				WHERE S.Id = :snapshot_id AND W.IsSensitive
						 				ORDER BY W.Word;
						 				''', {'snapshot_id': snapshot.Id})
					sensitive_reason = '(' + ', '.join(row['Word'] for row in cursor) + ')'
				else:
					sensitive_reason = ''

				try:
					print()
					print(f'[{i} of {total_recordings}] Approve the following recording:')
					print(f'- Snapshot: #{snapshot.Id} {snapshot}')
					print(f'- Type: {"Media" if snapshot.IsMedia else "Page"}')
					print(f'- Title: {snapshot.DisplayTitle}')

					if snapshot.LanguageName is not None:
						print(f'- Language: {snapshot.LanguageName}')

					if snapshot.PageUsesPlugins:
						print(f'- Uses Plugins: {snapshot.PageUsesPlugins}')

					if snapshot.DisplayMetadata is not None:
						print(f'- Metadata: {snapshot.DisplayMetadata}')

					print(f'- Points: {snapshot.Points}')

					if snapshot.IsSensitive:
						print(f'- Sensitive: {snapshot.IsSensitive} {sensitive_reason}')

					if snapshot.Options:
						print(f'- Options: {snapshot.Options}')

					if row['TotalRecordingUrls'] > 0:
						print(f'- Saved URLs (Recording): {row["SavedRecordingUrls"]} of {row["TotalRecordingUrls"]}')

					if row['TotalSnapshotUrls'] > 0:
						print(f'- Saved URLs (Snapshot): {row["SavedSnapshotUrls"]} of {row["TotalSnapshotUrls"]}')

					print(f'- Filename: {recording.UploadFilename}')

					if recording.HasAudio:
						print(f'- Has Audio: {recording.HasAudio}')

					has_tts = recording.TextToSpeechFilename is not None
					if has_tts:
						print(f'- Text-to-Speech: {has_tts}')

					print()

					input('Press enter to watch the recording.')
					os.startfile(recording.UploadFilePath)

					if args.tts and recording.TextToSpeechFilePath is not None:
						input('Press enter to listen to the text-to-speech audio file.')
						os.startfile(recording.TextToSpeechFilePath)

				except FileNotFoundError:
					print('The recording file does not exist.')
					record_snapshot_again(snapshot, recording)
					num_missing += 1
					continue

				while True:
					verdict = input('Verdict [(y)es, (n)o, (r)ecord again]: ').lower()

					if not verdict:
						continue
					elif verdict[0] == 'y':

						if args.withhold:
							print('[APPROVED BUT WITHHELD]')
							state = Snapshot.WITHHELD
						else:
							print('[APPROVED]')
							state = Snapshot.APPROVED

						priority = Snapshot.randomize_priority(Snapshot.MIN_PUBLISH_PRIORITY) if args.randomize else snapshot.Priority
						is_processed = recording.IsProcessed
						num_approved += 1

					elif verdict[0] == 'n':
						print('[REJECTED]')
						state = Snapshot.REJECTED
						priority = snapshot.NO_PRIORITY
						is_processed = True
						num_rejected += 1

					elif verdict[0] == 'r':
						print('[RECORD AGAIN]')
						state = Snapshot.SCOUTED
						priority = max(snapshot.Priority, Snapshot.MIN_RECORD_PRIORITY)
						is_processed = True
						num_to_record_again += 1

					else:
						print(f'Invalid input "{verdict}".')
						continue

					while True:
						sensitive = input('Sensitive Override [(s)kip, (y)es, (n)o]: ').lower()

						if not sensitive:
							continue
						elif sensitive[0] == 's':
							print('[SKIPPED]')
							is_sensitive_override = snapshot.IsSensitiveOverride
						elif sensitive[0] == 'y':
							print('[YES]')
							is_sensitive_override = True
						elif sensitive[0] == 'n':
							print('[NO]')
							is_sensitive_override = False
						else:
							print(f'Invalid input "{sensitive}".')
							continue

						break

					if args.customize and state in [Snapshot.APPROVED, Snapshot.WITHHELD]:
						while True:
							try:
								priority = input(f'Custom Priority [{Snapshot.MIN_PUBLISH_PRIORITY} to {Snapshot.MAX_PUBLISH_PRIORITY}]: ')
								priority = int(priority)
								if Snapshot.MIN_PUBLISH_PRIORITY <= priority <= Snapshot.MAX_PUBLISH_PRIORITY:
									print(f'[{priority}]')
									break
								else:
									print(f'Invalid priority {priority}.')
							except ValueError:
								print(f'Invalid input "{priority}".')

					snapshot_updates.append({'state': state, 'priority': priority, 'is_sensitive_override': is_sensitive_override, 'id': snapshot.Id})
					recording_updates.append({'is_processed': is_processed, 'id': recording.Id})
					break

			if total_recordings > 0:

				db.executemany('UPDATE Snapshot SET State = :state, Priority = :priority, IsSensitiveOverride = :is_sensitive_override WHERE Id = :id;', snapshot_updates)
				db.executemany('UPDATE Recording SET IsProcessed = :is_processed WHERE Id = :id;', recording_updates)
				db.commit()

				print()
				print(f'Evaluated {total_recordings} recordings: {num_approved} approved, {num_rejected} rejected, {num_to_record_again} to be recorded again, {num_missing} missing files.')
			else:
				print('Ran out of recordings to approve.')

		except sqlite3.Error as error:
			print(f'Failed to approve the recordings with the error: {repr(error)}')
			db.rollback()