#!/usr/bin/env python3

"""
	This script adds a Wayback Machine snapshot with a given priority to the database.
	This can be used to scout, record, or upload any existing or new snapshots as soon as possible.
"""

import sqlite3
from argparse import ArgumentParser

from waybackpy import WaybackMachineCDXServerAPI as Cdx
from waybackpy.exceptions import BlockedSiteError, NoCDXRecordFound

from common import CommonConfig, Database, Snapshot, is_wayback_machine_available

####################################################################################################

parser = ArgumentParser(description='Adds a Wayback Machine snapshot (URL and timestamp) to the Eternal Wanderer queue.')
parser.add_argument('priority', choices=['scout', 'record', 'upload'], help='The priority to assign to the snapshot.')
parser.add_argument('url', help='The URL of the snapshot.')
parser.add_argument('timestamp', help='The timestamp of the snapshot.')
parser.add_argument('-standalone', action='store_true', help='If it\'s a snapshot of standalone media.')
parser.add_argument('-filtered', action='store_true', help='If the standalone media snapshot should be filtered.')
args = parser.parse_args()

names_to_values = {'scout': Snapshot.SCOUT_PRIORITY, 'record': Snapshot.RECORD_PRIORITY, 'upload': Snapshot.UPLOAD_PRIORITY}
priority = names_to_values[args.priority]

with Database() as db:
	try:
		first_state = Snapshot.SCOUTED if args.standalone else Snapshot.QUEUED
		mime_type_filter = r'!mimetype:text/.*' if args.standalone else r'mimetype:text/html'
		
		uses_plugins = True if args.standalone else None
		is_filtered = args.filtered if args.standalone else None

		cdx = Cdx(url=args.url, filters=['statuscode:200', mime_type_filter])
		best_snapshot = cdx.near(wayback_machine_timestamp=args.timestamp)

		cdx.filters.append(f'digest:{best_snapshot.digest}')
		best_snapshot = cdx.oldest()

		try:
			db.execute(	'''
						INSERT INTO Snapshot (State, Depth, Priority, UsesPlugins, IsFiltered, IsStandaloneMedia, Url, Timestamp, IsExcluded, UrlKey, Digest)
						VALUES (:state, :depth, :priority, :uses_plugins, :is_filtered, :is_standalone_media, :url, :timestamp, :is_excluded, :url_key, :digest);
						''', {'state': first_state, 'depth': 0, 'priority': priority, 'is_standalone_media': args.standalone, 'uses_plugins': uses_plugins,
							  'is_filtered': is_filtered, 'url': best_snapshot.original, 'timestamp': best_snapshot.timestamp, 'is_excluded': False,
							  'url_key': best_snapshot.urlkey, 'digest': best_snapshot.digest})
			db.commit()
			print(f'Added the snapshot ({best_snapshot.original}, {best_snapshot.timestamp}) with the "{args.priority}" priority.')
		
		except sqlite3.IntegrityError:
			
			try:
				cursor = db.execute( 'SELECT State FROM Snapshot WHERE Url = :url AND Timestamp = :timestamp AND Digest = :digest;',
									{'url': best_snapshot.original, 'timestamp': best_snapshot.timestamp, 'digest': best_snapshot.digest})
				
				row = cursor.fetchone()
				if row is not None:
					
					old_state = row['State']

					if priority == Snapshot.SCOUT_PRIORITY:
						new_state = first_state
					elif priority == Snapshot.RECORD_PRIORITY:
						new_state = Snapshot.SCOUTED if old_state >= Snapshot.SCOUTED else first_state
					elif priority == Snapshot.UPLOAD_PRIORITY:
						new_state = Snapshot.RECORDED if old_state >= Snapshot.RECORDED else (Snapshot.SCOUTED if old_state >= Snapshot.SCOUTED else first_state)
					else:
						assert False, f'Unhandled priority "{args.priority}" ({priority}).'

					db.execute( 'UPDATE Snapshot SET State = :state, Priority = :priority WHERE Digest = :digest;',
							 	{'state': new_state, 'priority': priority, 'digest': best_snapshot.digest})
					db.commit()
					print(f'Updated the snapshot ({best_snapshot.original}, {best_snapshot.timestamp}) to the "{args.priority}" priority.')
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