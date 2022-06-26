#!/usr/bin/env python3

"""
	This script compiles multiple snapshot recordings into a single video.
	This can be done for published recordings that haven't been compiled yet, or for any recordings given their database IDs.
	A short transition with a user-defined background color, duration, and sound effect is inserted between each recording.
"""

import os
import sqlite3
from argparse import ArgumentParser
from datetime import timedelta
from tempfile import NamedTemporaryFile
from typing import List

import ffmpeg # type: ignore

from common import Database, Recording, Snapshot, delete_file, get_current_timestamp
from record import RecordConfig

####################################################################################################

if __name__ == '__main__':

	config = RecordConfig()

	parser = ArgumentParser(description='Compiles multiple snapshot recordings into a single video. This can be done for published recordings that haven\'t been compiled yet, or for any recordings given their database IDs. A short transition with a user-defined background color, duration, and sound effect is inserted between each recording.')
	parser.add_argument('-published', type=int, metavar='MAX_RECORDINGS', help='How many published recordings to compile. The selected recordings are stored in a database to prevent future compilations from showing repeated snapshots. This option cannot be used with -any.')
	parser.add_argument('-any', nargs=2, metavar=('ID_TYPE', 'ID_LIST'), help='Which recordings to compile, regardless if they have been published or not. The ID_TYPE argument must be either "snapshot" or "recording" depending on the database IDs specified in ID_LIST. The ID_LIST argument specifies which of these IDs to include or exclude from the compilation. For example, "1,5-10,!7,!9-10" would result in the ID list [1, 5, 6, 8]. For ID ranges, if the first value is greater than the second then the range is reversed. For example, "3-1" would result in [3, 2, 1], meaning the recordings would be shown in reverse order. This option cannot be used with -published.')
	parser.add_argument('-color', default='white', help='The background color for the transition. If omitted, this defaults to %(default)s. This may be a hexadecimal color code or a color name defined here: https://ffmpeg.org/ffmpeg-utils.html#Color')
	parser.add_argument('-duration', type=int, default=2, help='How long the transition lasts for in seconds. If omitted, this defaults to %(default)s.')
	parser.add_argument('-sfx', help='The path to the transition sound effect file. If omitted, no sound is added to the transition.')
	args = parser.parse_args()

	if args.published and args.any:
		parser.error('The -published and -any options cannot be used at the same time.')

	if args.any and args.any[0] not in ['snapshot', 'recording']:
		parser.error(f'Unknown ID type "{args.any[0]}". Only "snapshot" and "recording" are allowed.')

	if args.sfx:
		if os.path.isfile(args.sfx):
			args.sfx = os.path.abspath(args.sfx)
		else:
			parser.error(f'Could not find the sound effect file "{args.sfx}".')

	if args.any:
		try:
			id_type = args.any[0]
			id_list = args.any[1]
			
			include_id_list: List[int] = []
			exclude_id_list: List[int] = []
			
			for id in id_list.split(','):
				
				current_list = exclude_id_list if id.startswith('!') else include_id_list
				id = id.strip('!')

				if '-' in id:
					begin_id, end_id = id.split('-', 1)
					begin_id, end_id = int(begin_id), int(end_id)

					if begin_id <= end_id:
						range_id_list = list(range(begin_id, end_id + 1))
					else:
						range_id_list = list(range(begin_id, end_id - 1, -1))

					current_list.extend(range_id_list)
				else:
					current_list.append(int(id))

			include_id_list = list(dict.fromkeys(include_id_list))
			exclude_id_list = list(dict.fromkeys(exclude_id_list))

			id_list = [id for id in include_id_list if id not in exclude_id_list]

		except ValueError:
			parser.error(f'Could not convert the snapshot IDs "{id_list}" into a list of integers.') 

	with Database() as db:
		
		try:
			# Find the next auto incremented row ID.
			cursor = db.execute('''SELECT seq + 1 AS NextCompilationId FROM sqlite_sequence WHERE name = 'Compilation';''')
			row = cursor.fetchone()
			compilation_id = row['NextCompilationId'] if row is not None else 1

			if args.published:
				cursor = db.execute('''
									SELECT S.*, R.*, R.Id AS RecordingId
									FROM Snapshot S
									INNER JOIN Recording R ON S.Id = R.SnapshotId
									INNER JOIN
									(
										SELECT LR.SnapshotId, MAX(LR.PublishTime) AS LastPublishTime
										FROM Recording LR
										GROUP BY LR.SnapshotId
									) LR ON S.Id = LR.SnapshotId AND R.PublishTime = LR.LastPublishTime
									WHERE NOT EXISTS(SELECT 1 FROM RecordingCompilation RC WHERE RC.SnapshotId = S.Id)
									ORDER BY R.PublishTime
									LIMIT :max_recordings;
									''', {'max_recordings': args.published})

			else:
				query_id_list = '(' + ', '.join(str(id) for id in id_list) + ')'

				if id_type == 'snapshot':
					cursor = db.execute(f'''
										SELECT S.*, R.*, R.Id AS RecordingId
										FROM Snapshot S
										INNER JOIN Recording R ON S.Id = R.SnapshotId
										INNER JOIN
										(
											SELECT LR.SnapshotId, MAX(LR.CreationTime) AS LastCreationTime
											FROM Recording LR
											GROUP BY LR.SnapshotId
										) LR ON S.Id = LR.SnapshotId AND R.CreationTime = LR.LastCreationTime
										WHERE S.Id IN {query_id_list};
										''')
				else:
					cursor = db.execute(f'''
										SELECT S.*, R.*, R.Id AS RecordingId
										FROM Snapshot S
										INNER JOIN Recording R ON S.Id = R.SnapshotId
										WHERE R.Id IN {query_id_list};
										''')

			total_recordings = 0
			num_valid_recordings = 0

			snapshots_and_recordings = []
			for row in cursor:

				total_recordings += 1

				row = dict(row)
				# Avoid naming conflicts with each table's primary key.
				del row['Id']
				snapshot = Snapshot(**row, Id=row['SnapshotId'])
				recording = Recording(**row, Id=row['RecordingId'])

				if os.path.isfile(recording.UploadFilePath):
					snapshots_and_recordings.append((snapshot, recording))
					num_valid_recordings += 1
				else:
					print(f'- Skipping the recording #{recording.Id} for snapshot #{snapshot.Id} ({snapshot}) since the file "{recording.UploadFilename}" is missing.')

			if args.any:
				tuple_index = 0 if id_type == 'snapshot' else 1
				snapshots_and_recordings.sort(key=lambda x: id_list.index(x[tuple_index].Id))

			if snapshots_and_recordings:

				try:
					transition_file = NamedTemporaryFile(prefix='wanderer.', suffix='.mp4', delete=False)
					concat_file = NamedTemporaryFile(mode='w', encoding='utf-8', prefix='wanderer.', suffix='.txt', delete=False)

					try:
						template_recording = snapshots_and_recordings[0][1]
						probe = ffmpeg.probe(template_recording.UploadFilePath)
						template_stream = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
						width = template_stream['width']
						height = template_stream['height']
						framerate = template_stream['r_frame_rate']

						# See: https://trac.ffmpeg.org/wiki/FilteringGuide#SyntheticInput

						video_stream = ffmpeg.input(f'color={args.color}:size={width}x{height}:duration={args.duration}:rate={framerate}', f='lavfi')
						audio_stream = ffmpeg.input(args.sfx, guess_layout_max=0) if args.sfx else None
						input_streams: List[ffmpeg.Stream] = list(filter(None, [video_stream, audio_stream]))

						stream = ffmpeg.output(*input_streams, transition_file.name, tune='stillimage', **config.ffmpeg_upload_output)
						stream = stream.global_args(*config.ffmpeg_global)
						stream = stream.overwrite_output()
						stream.run()

						probe = ffmpeg.probe(transition_file.name)
						transition_duration = float(probe['format']['duration'])

					except (ffmpeg.Error, StopIteration, KeyError, ValueError) as error:
						print(f'Failed to create the transition video with the error: {repr(error)}')
						raise

					os.makedirs(config.compilations_path, exist_ok=True)
					
					range_identifier = args.any[1] if args.any else f'{snapshots_and_recordings[0][0].Id}-{snapshots_and_recordings[-1][0].Id}'
					compilation_identifiers = [str(compilation_id) if args.published else None, 'published' if args.published else 'any', id_type if args.any else None, range_identifier, 'with', str(num_valid_recordings), 'of', str(total_recordings)]
					compilation_path_prefix = os.path.join(config.compilations_path, '_'.join(filter(None, compilation_identifiers)))
					
					compilation_path = compilation_path_prefix + '.mp4'
					timestamps_path = compilation_path_prefix + '.txt'

					concat_file.write('ffconcat version 1.0\n')
					current_duration: float = 0

					try:
						with open(timestamps_path, 'w', encoding='utf-8') as timestamps_file:

							timestamps_file.write(f'{range_identifier}\n\n')
							
							print(f'Compiling {num_valid_recordings} valid files out of {total_recordings} selected recordings.')
							
							for snapshot, recording in snapshots_and_recordings:
								
								print(f'- Adding the recording #{recording.Id} for snapshot #{snapshot.Id} {snapshot}.')

								# See: https://superuser.com/questions/718027/ffmpeg-concat-doesnt-work-with-absolute-path/1551017#1551017
								recording_path = recording.UploadFilePath.replace('\\', '/')
								transition_path = transition_file.name.replace('\\', '/')

								concat_file.write(f"file 'file:{recording_path}'\n")
								concat_file.write(f"file 'file:{transition_path}'\n")

								timestamp = timedelta(seconds=round(current_duration))
								formatted_timestamp = str(timestamp).zfill(8)
								recording_identifiers = [formatted_timestamp, snapshot.DisplayTitle, '\N{jigsaw puzzle piece}' if snapshot.IsStandaloneMedia or snapshot.UsesPlugins else None]
								timestamp_line = ' '.join(filter(None, recording_identifiers))
								timestamps_file.write(f'{timestamp_line}\n')
								
								probe = ffmpeg.probe(recording.UploadFilePath)
								recording_duration = float(probe['format']['duration'])							
								current_duration += recording_duration + transition_duration

					except (ffmpeg.Error, KeyError, ValueError) as error:
						print(f'Failed to create the timestamps file with the error: {repr(error)}')
						raise

					concat_file.flush()

					try:
						# See:
						# - https://trac.ffmpeg.org/wiki/Concatenate#samecodec
						# - https://ffmpeg.org/ffmpeg-formats.html#concat
						stream = ffmpeg.input(concat_file.name, f='concat', safe=0)
						stream = stream.output(compilation_path, c='copy')
						stream = stream.global_args(*config.ffmpeg_global)
						stream = stream.overwrite_output()
						stream.run()
					except ffmpeg.Error as error:
						print(f'Failed to create the compilation video with the error: {repr(error)}')
						raise

					compilation_filename = os.path.basename(compilation_path)
					timestamps_filename = os.path.basename(timestamps_path)

					if args.published:

						recording_compilation = []
						for i, (snapshot, recording) in enumerate(snapshots_and_recordings):
							recording_compilation.append({'recording_id': recording.Id, 'compilation_id': compilation_id, 'snapshot_id': snapshot.Id, 'position': i + 1})

						db.execute(	'''
									INSERT INTO Compilation (UploadFilename, TimestampsFilename, CreationTime)
									VALUES (:upload_filename, :timestamps_filename, :creation_time);
									''', {'upload_filename': compilation_filename, 'timestamps_filename': timestamps_filename, 'creation_time': get_current_timestamp()})

						db.executemany(	'''
										INSERT INTO RecordingCompilation (RecordingId, CompilationId, SnapshotId, Position)
										VALUES (:recording_id, :compilation_id, :snapshot_id, :position);
										''', recording_compilation)

						db.commit()

					print(f'Created the compilation "{compilation_filename}".')

				except (ffmpeg.Error, StopIteration, KeyError, ValueError):
					pass
				finally:
					transition_file.close()
					concat_file.close()
					delete_file(transition_file.name)
					delete_file(concat_file.name)
			else:
				print('Could not find any recordings that match the given criteria.')

		except sqlite3.Error as error:
			print(f'Failed to compile the recorded snapshots with the error: {repr(error)}')
			db.rollback()

	print('Finished running.')