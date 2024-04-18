#!/usr/bin/env python3

import sqlite3
from argparse import ArgumentParser

from waybackpy.exceptions import BlockedSiteError, NoCDXRecordFound

from common.database import Database
from common.snapshot import Snapshot
from common.wayback import (
	are_wayback_machine_services_available, find_best_wayback_machine_snapshot,
	find_extra_wayback_machine_snapshot_info, parse_wayback_machine_snapshot_url,
)

if __name__ == '__main__':

	parser = ArgumentParser(description='Adds a Wayback Machine snapshot to the queue with a given priority. This can be used to scout, record, or publish any existing or new snapshots as soon as possible.')
	parser.add_argument('priority_name', choices=['scout', 'record', 'publish'], help='The priority to assign to the snapshot.')
	parser.add_argument('url', help='The URL of the snapshot.')
	parser.add_argument('timestamp', nargs='?', help='The timestamp of the snapshot. May be omitted if the URL already points to a Wayback Machine snapshot.')
	args = parser.parse_args()

	wayback_parts = parse_wayback_machine_snapshot_url(args.url)
	if wayback_parts is not None:
		args.url = wayback_parts.url
		args.timestamp = wayback_parts.timestamp
	elif args.timestamp is None:
		parser.error('The timestamp cannot be omitted unless the URL already points to a Wayback Machine snapshot.')

	names_to_values = {'scout': Snapshot.SCOUT_PRIORITY, 'record': Snapshot.RECORD_PRIORITY, 'publish': Snapshot.PUBLISH_PRIORITY}
	priority = names_to_values[args.priority_name]

	with Database() as db:

		try:
			best_snapshot, is_media, media_extension = find_best_wayback_machine_snapshot(timestamp=args.timestamp, url=args.url)
			last_modified_time = find_extra_wayback_machine_snapshot_info(best_snapshot.archive_url)

			first_state = Snapshot.SCOUTED if is_media else Snapshot.QUEUED
			scout_time = Database.get_current_timestamp() if is_media else None

			# Media files shouldn't be scouted.
			if is_media and args.priority_name == 'scout':
				priority = Snapshot.NO_PRIORITY

			try:
				db.execute(	'''
							INSERT INTO Snapshot (Depth, State, Priority, IsExcluded, IsMedia, MediaExtension, ScoutTime, Url, Timestamp, LastModifiedTime, UrlKey, Digest)
							VALUES (0, :state, :priority, FALSE, :is_media, :media_extension, :scout_time, :url, :timestamp, :last_modified_time, :url_key, :digest);
							''',
							{'state': first_state, 'priority': priority, 'is_media': is_media, 'media_extension': media_extension,
							 'scout_time': scout_time, 'url': best_snapshot.original, 'timestamp': best_snapshot.timestamp,
							 'last_modified_time': last_modified_time, 'url_key': best_snapshot.urlkey, 'digest': best_snapshot.digest})
				db.commit()

				snapshot_type = 'media file' if is_media else 'web page'
				print(f'Added the {snapshot_type} snapshot ({best_snapshot.original}, {best_snapshot.timestamp}) with the {args.priority_name} priority.')

				if first_state == Snapshot.QUEUED and args.priority_name in ['record', 'publish']:
					print('The snapshot must be scouted before it can be recorded.')

			except sqlite3.IntegrityError:

				try:
					cursor = db.execute('SELECT * FROM Snapshot WHERE Url = :url AND Timestamp = :timestamp;',
										{'url': best_snapshot.original, 'timestamp': best_snapshot.timestamp})

					row = cursor.fetchone()
					if row is not None:

						snapshot = Snapshot(**row)
						old_state = snapshot.State
						priority = max(priority, snapshot.Priority)

						if args.priority_name == 'scout':
							new_state = first_state
						elif args.priority_name == 'record':
							new_state = Snapshot.SCOUTED if old_state >= Snapshot.SCOUTED else first_state
						elif args.priority_name == 'publish':
							new_state = Snapshot.RECORDED if old_state >= Snapshot.RECORDED else (Snapshot.SCOUTED if old_state >= Snapshot.SCOUTED else first_state)
						else:
							assert False, f'Unhandled priority "{args.priority_name}".'

						db.execute('UPDATE Snapshot SET State = :state, Priority = :priority WHERE Id = :id;', {'state': new_state, 'priority': priority, 'id': snapshot.Id})
						db.commit()

						snapshot_type = 'media file' if snapshot.IsMedia else 'web page'
						print(f'Updated the {snapshot_type} snapshot {snapshot} to the {args.priority_name} priority.')

						if new_state == Snapshot.QUEUED and args.priority_name in ['record', 'publish']:
							print('The snapshot must be scouted before it can be recorded.')
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
			if not are_wayback_machine_services_available():
				print('The Wayback Machine is not currently available.')