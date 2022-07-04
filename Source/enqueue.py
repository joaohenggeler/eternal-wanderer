#!/usr/bin/env python3

"""
	This script adds a Wayback Machine snapshot to the Eternal Wanderer queue with a given priority.
	This can be used to scout, record, or publish any existing or new snapshots as soon as possible.
"""

import sqlite3
from argparse import ArgumentParser

from waybackpy.exceptions import BlockedSiteError, NoCDXRecordFound

from common import Database, Snapshot, find_best_wayback_machine_snapshot, find_extra_wayback_machine_snapshot_info, is_wayback_machine_available, parse_wayback_machine_snapshot_url

####################################################################################################

if __name__ == '__main__':

	parser = ArgumentParser(description='Adds a Wayback Machine snapshot to the Eternal Wanderer queue with a given priority. This can be used to scout, record, or publish any existing or new snapshots as soon as possible.')
	parser.add_argument('priority', choices=['scout', 'record', 'publish'], help='The priority to assign to the snapshot.')
	parser.add_argument('url', help='The URL of the snapshot.')
	parser.add_argument('timestamp', nargs='?', help='The timestamp of the snapshot. May be omitted if the URL already points to a Wayback Machine snapshot.')
	args = parser.parse_args()

	wayback_parts = parse_wayback_machine_snapshot_url(args.url)
	if wayback_parts is not None:
		args.url = wayback_parts.Url
		args.timestamp = wayback_parts.Timestamp
	elif args.timestamp is None:
		parser.error('The timestamp cannot be omitted unless the URL already points to a Wayback Machine snapshot.')

	names_to_values = {'scout': Snapshot.SCOUT_PRIORITY, 'record': Snapshot.RECORD_PRIORITY, 'publish': Snapshot.PUBLISH_PRIORITY}
	priority = names_to_values[args.priority]

	with Database() as db:
		try:
			best_snapshot, is_standalone_media, file_extension = find_best_wayback_machine_snapshot(timestamp=args.timestamp, url=args.url)
			guessed_encoding, last_modified_time = find_extra_wayback_machine_snapshot_info(best_snapshot.archive_url)

			first_state = Snapshot.SCOUTED if is_standalone_media else Snapshot.QUEUED

			# Standalone media shouldn't be scouted.
			if is_standalone_media and priority == Snapshot.SCOUT_PRIORITY:
				priority = Snapshot.NO_PRIORITY

			try:
				db.execute(	'''
							INSERT INTO Snapshot (State, Depth, Priority, IsExcluded, IsStandaloneMedia, FileExtension, Url, Timestamp, GuessedEncoding, LastModifiedTime, UrlKey, Digest)
							VALUES (:state, :depth, :priority, :is_excluded, :is_standalone_media, :file_extension, :url, :timestamp, :guessed_encoding, :last_modified_time, :url_key, :digest);
							''',
							{'state': first_state, 'depth': 0, 'priority': priority, 'is_excluded': False, 'is_standalone_media': is_standalone_media,
							 'file_extension': file_extension, 'url': best_snapshot.original, 'timestamp': best_snapshot.timestamp,
							 'guessed_encoding': guessed_encoding, 'last_modified_time': last_modified_time, 'url_key': best_snapshot.urlkey,
							 'digest': best_snapshot.digest})
				db.commit()
				
				print(f'Added the snapshot ({best_snapshot.original}, {best_snapshot.timestamp}, {best_snapshot.mimetype}) with the "{args.priority}" priority.')
				
				if first_state == Snapshot.QUEUED and priority > Snapshot.SCOUT_PRIORITY:
					print('The snapshot must be scouted before it can be recorded or published.')

			except sqlite3.IntegrityError:
				
				try:
					cursor = db.execute('SELECT * FROM Snapshot WHERE Url = :url AND Timestamp = :timestamp;',
										{'url': best_snapshot.original, 'timestamp': best_snapshot.timestamp})
					
					row = cursor.fetchone()
					if row is not None:
						
						snapshot = Snapshot(**dict(row))
						old_state = snapshot.State

						if priority == Snapshot.SCOUT_PRIORITY:
							new_state = first_state
						elif priority == Snapshot.RECORD_PRIORITY:
							new_state = Snapshot.SCOUTED if old_state >= Snapshot.SCOUTED else first_state
						elif priority == Snapshot.PUBLISH_PRIORITY:
							new_state = Snapshot.RECORDED if old_state >= Snapshot.RECORDED else (Snapshot.SCOUTED if old_state >= Snapshot.SCOUTED else first_state)
						else:
							assert False, f'Unhandled priority "{args.priority}" ({priority}).'

						db.execute('UPDATE Snapshot SET State = :state, Priority = :priority WHERE Id = :id;', {'state': new_state, 'priority': priority, 'id': snapshot.Id})
						db.commit()
						
						print(f'Updated the snapshot {snapshot} to the "{args.priority}" priority.')

						if new_state == Snapshot.QUEUED and priority > Snapshot.SCOUT_PRIORITY:
							print('The snapshot must be scouted before it can be recorded or published.')
					else:
						print(f'Could not add or update the snapshot ({best_snapshot.original}, {best_snapshot.timestamp}) since another one with the same digest but different URL and timestamp values already exists.')
				
				except sqlite3.Error as error:
					print(f'Could not update the snapshot with the error: {repr(error)}')
					db.rollback()

			except sqlite3.Error as error:
				print(f'Could not insert the snapshot with the error: {repr(error)}')
				db.rollback()

		except NoCDXRecordFound as error:
			print(f'Could not find any snapshots at "{args.url}" near {args.timestamp}.')	
		except BlockedSiteError:
			print(f'The snapshot at "{args.url}" near {args.timestamp} has been excluded from the Wayback Machine.')
		except Exception as error:
			print(f'Failed to find a snapshot at "{args.url}" near {args.timestamp} with the error: {repr(error)}')
			if not is_wayback_machine_available():
				print('The Wayback Machine is not currently available.')

	print('Finished running.')