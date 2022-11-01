#!/usr/bin/env python3

import os
import sqlite3
import tempfile
from argparse import ArgumentParser
from glob import iglob
from typing import Optional

from common import (
	CommonConfig, Database, Recording, TemporaryRegistry,
	delete_directory, delete_file,
)

if __name__ == '__main__':

	parser = ArgumentParser(description='Deletes all video files belonging to unapproved and/or compiled recordings.')
	parser.add_argument('-unapproved', action='store_true', help='Delete unapproved recordings (rejected or to be recorded again).')
	parser.add_argument('-compiled', action='store_true', help='Delete published recordings that are part of a compilation.')
	parser.add_argument('-temporary', action='store_true', help=f'Delete any temporary files or directories with the "{CommonConfig.TEMPORARY_PATH_PREFIX}" prefix.')
	parser.add_argument('-registry', action='store_true', help='Delete any leftover registry keys used by plugins. This option requires running the script with elevated privileges.')
	args = parser.parse_args()

	if not any(vars(args).values()):
		parser.error('No arguments provided.')

	def delete_recordings(recording_list: list[Recording]) -> tuple[int, int]:
		""" Deletes the all video files from a list of recordings. """

		total = 0
		num_deleted = 0

		def delete(path: Optional[str]) -> None:
			
			nonlocal total, num_deleted

			if path is not None:
				total += 1
				print(f'- Recording #{recording.Id} ({recording.CreationTime}): {path}')
				
				if delete_file(path):
					num_deleted += 1

		for recording in recording_list:
			delete(recording.UploadFilePath)
			delete(recording.ArchiveFilePath)
			delete(recording.TextToSpeechFilePath)

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

			if args.temporary:

				total_temporary = 0
				num_temporary_deleted = 0
				
				temporary_search_path = os.path.join(tempfile.gettempdir(), CommonConfig.TEMPORARY_PATH_PREFIX + '*')
				for path in iglob(temporary_search_path):
					
					total_temporary += 1
					print(f'- Temporary: {path}')

					if os.path.isfile(path) and delete_file(path):
						num_temporary_deleted += 1
					elif os.path.isdir(path) and delete_directory(path):
						num_temporary_deleted += 1

			if args.registry:

				registry_keys = [
					# Shockwave Player (settings)
					'HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 11\\allowfallback',
					'HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 12\\allowfallback',
					'HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Macromedia\\Shockwave 10\\renderer3dsetting',
					'HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Macromedia\\Shockwave 10\\renderer3dsettingPerm',
					'HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 11\\renderer3dsetting',
					'HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 11\\renderer3dsettingPerm',
					'HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 12\\renderer3dsetting',
					'HKEY_CURRENT_USER\\SOFTWARE\\AppDataLow\\Software\\Adobe\\Shockwave 12\\renderer3dsettingPerm',

					# Cosmo Player (installation and settings)
					'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646731-BCF3-11D0-9518-00C04FC2DD79}',
					'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\FILTER\\{06646731-BCF3-11D0-9518-00C04FC2DD79}',
					'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\CLSID\\{06646732-BCF3-11D0-9518-00C04FC2DD79}',
					'HKEY_LOCAL_MACHINE\\SOFTWARE\\CLASSES\\FILTER\\{06646732-BCF3-11D0-9518-00C04FC2DD79}',
					'HKEY_LOCAL_MACHINE\\SOFTWARE\\COSMOSOFTWARE',
					'HKEY_CURRENT_USER\\SOFTWARE\\CosmoSoftware',

					# 3DVIA Player (settings)
					'HKEY_LOCAL_MACHINE\\SOFTWARE\\Virtools\\WebPlayer\\Config',
				]

				try:
					for key in registry_keys:
						print(f'- Registry Key: {key}')
						TemporaryRegistry.delete_key_tree(key)
				except PermissionError:
					print('Deleting the registry keys requires elevated privileges.')

			if args.unapproved:
				print(f'Deleted {num_unapproved_deleted} of {total_unapproved} unapproved recordings.')
			
			if args.compiled:
				print(f'Deleted {num_compiled_deleted} of {total_compiled} compiled recordings.')

			if args.temporary:
				print(f'Deleted {num_temporary_deleted} of {total_temporary} temporary files/directories.')

		except sqlite3.Error as error:
			print(f'Failed to retrieve the recordings with the error: {repr(error)}')