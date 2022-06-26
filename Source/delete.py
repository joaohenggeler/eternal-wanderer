#!/usr/bin/env python3

"""
	This script deletes all video files belonging to unapproved and/or compiled recordings.
"""

import sqlite3
from argparse import ArgumentParser
from typing import List, Tuple

from common import Database, Recording, delete_file

####################################################################################################

if __name__ == '__main__':

	parser = ArgumentParser(description='Deletes all video files belonging to unapproved and/or compiled recordings.')
	parser.add_argument('-unapproved', action='store_true', help='Whether to delete unapproved recordings (rejected or to be recorded again).')
	parser.add_argument('-compiled', action='store_true', help='Whether to delete published recordings that are part of a compilation.')
	args = parser.parse_args()

	if not args.unapproved and not args.compiled:
		parser.error('You must specify at least one type of recording to delete.')

	def delete_recordings(recording_list: List[Recording]) -> Tuple[int, int]:
		""" Deletes the all video files from a list of recordings. """

		total = 0
		num_deleted = 0

		for recording in recording_list:

			total += 1
			print(f'- #{recording.Id} ({recording.CreationTime}): {recording.UploadFilePath}')
			if delete_file(recording.UploadFilePath):
				num_deleted += 1

			if recording.ArchiveFilePath is not None:
				
				total += 1
				print(f'- #{recording.Id} ({recording.CreationTime}): {recording.ArchiveFilePath}')
				if delete_file(recording.ArchiveFilePath):
					num_deleted += 1

		return num_deleted, total

	with Database() as db:
		
		try:
			if args.unapproved:

				cursor = db.execute('SELECT * FROM Recording WHERE IsProcessed AND PublishTime IS NULL ORDER BY CreationTime;')
				unapproved_recordings = [Recording(**dict(row)) for row in cursor]

				print(f'Deleting the files from {len(unapproved_recordings)} unapproved recordings.')
				num_unapproved_deleted, total_unapproved = delete_recordings(unapproved_recordings)

			if args.compiled:

				cursor = db.execute('''
									SELECT R.*
									FROM Recording R
									INNER JOIN RecordingCompilation RC ON R.Id = RC.RecordingId
									ORDER BY RC.CompilationId, RC.Position;
									''')

				compiled_recordings = [Recording(**dict(row)) for row in cursor]

				print(f'Deleting the files from {len(compiled_recordings)} compiled recordings.')
				num_compiled_deleted, total_compiled = delete_recordings(compiled_recordings)

			if args.unapproved:
				print(f'Deleted {num_unapproved_deleted} of {total_unapproved} unapproved recordings.')
			
			if args.compiled:
				print(f'Deleted {num_compiled_deleted} of {total_compiled} compiled recordings.')

		except sqlite3.Error as error:
			print(f'Failed to retrieve the recordings with the error: {repr(error)}')

	print('Finished running.')