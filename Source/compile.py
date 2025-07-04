#!/usr/bin/env python3

import sqlite3
from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from common.config import CommonConfig
from common.database import Database
from common.ffmpeg import (
	ffmpeg, FfmpegException,
	ffprobe_duration, ffprobe_info,
)
from common.recording import Recording
from common.snapshot import Snapshot
from common.util import delete_file
from record import RecordConfig

if __name__ == '__main__':

	parser = ArgumentParser(description='Compiles multiple snapshot recordings into a single video. This can be done for published recordings that haven\'t been compiled yet, or for any recordings given their database IDs. A short transition with a user-defined background color, duration, and sound effect is inserted between each recording.')
	parser.add_argument('-published', nargs=2, metavar=('BEGIN_DATE', 'END_DATE'), help='Which published recordings to compile. Each date must use a format between "YYYY" and "YYYY-MM-DD HH:MM:SS" with different granularities. For example, "2022-07" and "2022-08-15" would compile all published recordings between July 1st (inclusive) and August 15th (exclusive), 2022. This option cannot be used with -any.')
	parser.add_argument('-any', nargs=2, metavar=('ID_TYPE', 'ID_LIST'), help='Which recordings to compile, regardless if they have been published or not. The ID_TYPE argument must be either "snapshot" or "recording" depending on the database IDs specified in ID_LIST. The ID_LIST argument specifies which of these IDs to include or exclude from the compilation. For example, "1,5-10,!7,!9-10" would result in the ID list [1, 5, 6, 8]. For ID ranges, if the first value is greater than the second then the range is reversed. For example, "3-1" would result in [3, 2, 1], meaning the recordings would be shown in reverse order. If ID_TYPE is "snapshot" and two or more recordings of the same snapshot are found, only the most recently created one is used. You can compile the same snapshot more than once by setting ID_TYPE to "recording". This option cannot be used with -published.')
	parser.add_argument('-tts', action='store_true', help='Use the text-to-speech video files instead of the snapshot recordings.')
	parser.add_argument('-color', default='white', help='The background color for the transition. If omitted, this defaults to %(default)s. This may be a hexadecimal color code or a color name defined here: https://ffmpeg.org/ffmpeg-utils.html#Color')
	parser.add_argument('-duration', type=int, default=2, help='How long the transition lasts for in seconds. If omitted, this defaults to %(default)s.')
	parser.add_argument('-sfx', type=Path, help='The path to the transition sound effect file. If omitted, no sound is added to the transition.')
	args = parser.parse_args()

	if args.published is not None and args.any is not None:
		parser.error('The -published and -any options cannot be used at the same time.')

	if args.any is not None and args.any[0] not in ['snapshot', 'recording']:
		parser.error(f'Unknown ID type "{args.any[0]}". Only "snapshot" and "recording" are allowed.')

	if args.sfx is not None and not args.sfx.is_file():
		parser.error(f'Could not find the sound effect file "{args.sfx}".')

	if args.published is not None:
		begin_date = args.published[0]
		end_date = args.published[1]
	elif args.any is not None:
		try:
			id_type = args.any[0]
			id_list = args.any[1]

			include_id_list: list[int] = []
			exclude_id_list: list[int] = []

			for id_ in id_list.split(','):

				current_list = exclude_id_list if id_.startswith('!') else include_id_list
				id_ = id_.removeprefix('!')

				if '-' in id_:
					begin_id, _, end_id = id_.partition('-')
					begin_id, end_id = int(begin_id), int(end_id)

					if begin_id <= end_id:
						range_id_list = list(range(begin_id, end_id + 1))
					else:
						range_id_list = list(range(begin_id, end_id - 1, -1))

					current_list.extend(range_id_list)
				else:
					current_list.append(int(id_))

			include_id_list = list(dict.fromkeys(include_id_list))
			exclude_id_list = list(dict.fromkeys(exclude_id_list))

			id_list = [id_ for id_ in include_id_list if id_ not in exclude_id_list]

		except ValueError:
			parser.error(f'Could not convert the snapshot IDs "{id_list}" into a list of integers.')
	else:
		assert False, f'Found an unhandled command line option.'

	config = RecordConfig()

	with Database() as db:

		try:
			# Find the next auto incremented row ID.
			cursor = db.execute("SELECT seq + 1 AS NextCompilationId FROM sqlite_sequence WHERE name = 'Compilation';")
			row = cursor.fetchone()
			compilation_id = row['NextCompilationId'] if row is not None else 1

			if args.published is not None:
				cursor = db.execute('''
									SELECT S.*, SI.IsSensitive, R.*, R.Id AS RecordingId
									FROM Snapshot S
									INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
									INNER JOIN Recording R ON S.Id = R.SnapshotId
									WHERE R.PublishTime BETWEEN :begin_date AND :end_date
									ORDER BY R.PublishTime;
									''',
									{'begin_date': begin_date, 'end_date': end_date})

			else:
				def is_recording_part_of_compilation(id_: int) -> bool:
					""" Checks if a recording should be compiled given its snapshot or recording ID. """
					return id_ in id_list

				db.create_function('IS_RECORDING_PART_OF_COMPILATION', 1, is_recording_part_of_compilation)

				if id_type == 'snapshot':
					cursor = db.execute('''
										SELECT S.*, SI.IsSensitive, R.*, R.Id AS RecordingId
										FROM Snapshot S
										INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
										INNER JOIN Recording R ON S.Id = R.SnapshotId
										INNER JOIN
										(
											SELECT R.SnapshotId, MAX(R.CreationTime) AS LastCreationTime
											FROM Recording R
											GROUP BY R.SnapshotId
										) LCR ON S.Id = LCR.SnapshotId AND R.CreationTime = LCR.LastCreationTime
										WHERE IS_RECORDING_PART_OF_COMPILATION(S.Id);
										''')
				else:
					cursor = db.execute('''
										SELECT S.*, SI.IsSensitive, R.*, R.Id AS RecordingId
										FROM Snapshot S
										INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
										INNER JOIN Recording R ON S.Id = R.SnapshotId
										WHERE IS_RECORDING_PART_OF_COMPILATION(R.Id);
										''')

			total_recordings = 0
			num_found = 0

			snapshots_and_recordings = []
			for row in cursor:

				# Avoid naming conflicts with each table's primary key.
				del row['Id']
				snapshot = Snapshot(**row, Id=row['SnapshotId'])
				recording = Recording(**row, Id=row['RecordingId'])

				assert snapshot.IsSensitive is not None, 'The IsSensitive column is not being computed properly.'

				if args.tts and snapshot.IsMedia:
					continue

				total_recordings += 1
				recording.CompilationSegmentFilePath = recording.TextToSpeechFilePath if args.tts else recording.UploadFilePath

				if recording.CompilationSegmentFilePath is not None and recording.CompilationSegmentFilePath.is_file():
					snapshots_and_recordings.append((snapshot, recording))
					num_found += 1
				else:
					print(f'- Skipping recording #{recording.Id} of snapshot #{snapshot.Id} {snapshot} since the file "{recording.CompilationSegmentFilePath}" is missing.')

			if args.any is not None:
				tuple_index = 0 if id_type == 'snapshot' else 1
				snapshots_and_recordings.sort(key=lambda x: id_list.index(x[tuple_index].Id))

			if snapshots_and_recordings:

				transition_file = NamedTemporaryFile(mode='wb', prefix=CommonConfig.TEMPORARY_PATH_PREFIX, suffix='.ts', delete=False)
				concat_file = NamedTemporaryFile(mode='w', encoding='utf-8', prefix=CommonConfig.TEMPORARY_PATH_PREFIX, suffix='.txt', delete=False)
				intermediate_file_list = []

				try:
					try:
						template_recording = snapshots_and_recordings[0][1]
						info = ffprobe_info(template_recording.CompilationSegmentFilePath)
						template_stream = next(stream for stream in info['streams'] if stream['codec_type'] == 'video')

						width = template_stream['width']
						height = template_stream['height']
						framerate = template_stream['r_frame_rate']

						# See: https://trac.ffmpeg.org/wiki/FilteringGuide#SyntheticInput
						input_args = [
							'-f', 'lavfi',
							'-i', f'color={args.color}:size={width}x{height}:duration={args.duration}:rate={framerate}',
						]

						if args.sfx is not None:
							input_args.extend(['-i', args.sfx])

						output_args = config.text_to_speech_ffmpeg_output_args.copy() if args.tts else config.upload_ffmpeg_output_args.copy()

						try:
							idx = output_args.index('-tune')
							output_args[idx + 1] = 'stillimage'
						except (ValueError, IndexError):
							output_args.extend(['-tune', 'stillimage'])

						# Remove the -shortest flags used when generating the text-to-speech file so they don't shorten the transition.

						try:
							output_args.remove('-shortest')
						except ValueError:
							pass

						output_args.append(transition_file.name)

						ffmpeg(*input_args, *output_args)
						transition_duration = ffprobe_duration(transition_file.name)

					except (FfmpegException, StopIteration) as error:
						print(f'Failed to create the transition video with the error: {repr(error)}')
						raise

					config.compilations_path.mkdir(parents=True, exist_ok=True)

					id_identifier = str(compilation_id) if args.published is not None else None
					type_identifier = 'published' if args.published is not None else f'any_{id_type}'

					if args.any is not None:
						id_list_bytes = str(id_list).encode()
						id_list_hash = sha256(id_list_bytes).hexdigest()
						range_identifier = id_list_hash[:6]
					else:
						formatted_begin_date = begin_date.replace('-', '_').replace(' ', '_').replace(':', '_')
						formatted_end_date = end_date.replace('-', '_').replace(' ', '_').replace(':', '_')
						range_identifier = formatted_begin_date + '_to_' + formatted_end_date

					total_identifier = f'with_{num_found}_of_{total_recordings}'
					text_to_speech_identifier = 'tts' if args.tts else None

					compilation_identifiers = [id_identifier, type_identifier, range_identifier, total_identifier, text_to_speech_identifier]
					compilation_path_prefix = config.compilations_path / '_'.join(filter(None, compilation_identifiers))

					compilation_path = Path(str(compilation_path_prefix) + '.mp4')
					timestamps_path = Path(str(compilation_path_prefix) + '.txt')

					concat_file.write('ffconcat version 1.0\n')
					current_duration: float = 0

					try:
						with open(timestamps_path, 'w', encoding='utf-8') as timestamps_file:

							print(f'Compiling {num_found} of {total_recordings} recordings.')

							for snapshot, recording in snapshots_and_recordings:

								print(f'- Adding recording #{recording.Id} of snapshot #{snapshot.Id} {snapshot}.')

								# The recordings are remuxed to MPEG-TS to try to avoid any errors when concatenating every file.
								# E.g. "Non-monotonous DTS in output stream"
								intermediate_file = NamedTemporaryFile(mode='wb', prefix=CommonConfig.TEMPORARY_PATH_PREFIX, suffix='.ts', delete=False)
								intermediate_file_list.append(intermediate_file)

								# See:
								# - https://stackoverflow.com/a/47725134/18442724
								# - https://ffmpeg.org/ffmpeg-bitstream-filters.html#h264_005fmp4toannexb
								input_args = ['-i', recording.CompilationSegmentFilePath]
								output_args = ['-c', 'copy', intermediate_file.name]
								ffmpeg(*input_args, *output_args)

								# See: https://superuser.com/questions/718027/ffmpeg-concat-doesnt-work-with-absolute-path/1551017#1551017
								recording_concat_path = intermediate_file.name.replace('\\', '/')
								transition_concat_path = transition_file.name.replace('\\', '/')

								concat_file.write(f"file 'file:{recording_concat_path}'\n")
								concat_file.write(f"file 'file:{transition_concat_path}'\n")

								minutes, seconds = divmod(round(current_duration), 60)
								hours, minutes = divmod(minutes, 60)
								timestamp = f'{hours:02}:{minutes:02}:{seconds:02}'

								media_emoji = '\N{DVD}' if snapshot.IsMedia else ('\N{Jigsaw Puzzle Piece}' if snapshot.PageUsesPlugins else None)
								sensitive_emoji = '\N{No One Under Eighteen Symbol}' if snapshot.IsSensitive else None
								audio_emoji = '\N{Speaker With Three Sound Waves}' if recording.HasAudio else None
								emojis = [media_emoji, sensitive_emoji, audio_emoji, *snapshot.Emojis]

								line = [timestamp, snapshot.DisplayTitle, snapshot.DisplayMetadata, f'({snapshot.ShortDate})', *emojis]
								line = ' '.join(filter(None, line)) + '\n'
								timestamps_file.write(line)

								current_duration += ffprobe_duration(intermediate_file.name) + transition_duration

							timestamps_file.write('\n')

							minutes, seconds = divmod(round(current_duration), 60)
							hours, minutes = divmod(minutes, 60)

							snapshot_ids = ','.join(str(snapshot.Id) for snapshot, _ in snapshots_and_recordings)
							recording_ids = ','.join(str(recording.Id) for _, recording in snapshots_and_recordings)

							timestamps_file.write(f'Duration: {hours:02}:{minutes:02}:{seconds:02}\n')
							timestamps_file.write(f'Total: {len(snapshots_and_recordings)}\n')
							timestamps_file.write(f'Snapshots: {snapshot_ids}\n')
							timestamps_file.write(f'Recordings: {recording_ids}\n')

							timestamps_file.write('\n')

							if args.published is not None:
								timestamps_file.write(f'Type: Published ({begin_date} to {end_date})\n')
							else:
								timestamps_file.write(f'Type: Any {id_type.title()} ({range_identifier})\n')

							timestamps_file.write(f'Text-to-Speech: {"Yes" if args.tts else "No"}\n')
							timestamps_file.write(f'Transition Color: {args.color}\n')
							timestamps_file.write(f'Transition Duration: {args.duration}\n')
							timestamps_file.write(f'Transition Sfx: {args.sfx}')

					except FfmpegException as error:
						print(f'Failed to create the timestamps file with the error: {repr(error)}')
						raise

					concat_file.flush()

					try:
						# See:
						# - https://trac.ffmpeg.org/wiki/Concatenate#samecodec
						# - https://ffmpeg.org/ffmpeg-formats.html#concat
						input_args = ['-f', 'concat', '-safe', 0, '-i', concat_file.name]
						output_args = ['-c', 'copy', '-movflags', 'faststart', compilation_path]
						ffmpeg(*input_args, *output_args)
					except FfmpegException as error:
						print(f'Failed to create the compilation video with the error: {repr(error)}')
						raise

					if args.published is not None:

						recording_compilation = []
						for i, (snapshot, recording) in enumerate(snapshots_and_recordings, start=1):
							recording_compilation.append({'recording_id': recording.Id, 'compilation_id': compilation_id, 'snapshot_id': snapshot.Id, 'position': i})

						db.execute(	'''
									INSERT INTO Compilation (UploadFilename, TimestampsFilename)
									VALUES (:upload_filename, :timestamps_filename);
									''',
									{'upload_filename': compilation_path.name, 'timestamps_filename': timestamps_path.name})

						db.executemany(	'''
										INSERT INTO RecordingCompilation (RecordingId, CompilationId, SnapshotId, Position)
										VALUES (:recording_id, :compilation_id, :snapshot_id, :position);
										''', recording_compilation)

						db.commit()

					print(f'Created the compilation "{compilation_path.name}".')

				except (FfmpegException, StopIteration):
					pass
				finally:
					transition_file.close()
					delete_file(transition_file.name)

					concat_file.close()
					delete_file(concat_file.name)

					for file in intermediate_file_list:
						file.close()
						delete_file(file.name)

			else:
				print('Could not find any recordings that match the given criteria.')

		except sqlite3.Error as error:
			print(f'Failed to compile the recorded snapshots with the error: {repr(error)}')
			db.rollback()