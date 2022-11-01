#!/usr/bin/env python3

import os
import sqlite3
import sys
import tempfile
from argparse import ArgumentParser
from glob import glob
from tempfile import NamedTemporaryFile
from time import sleep
from typing import Optional, Union

import ffmpeg # type: ignore
import tweepy # type: ignore
from apscheduler.schedulers import SchedulerNotRunningError # type: ignore
from apscheduler.schedulers.blocking import BlockingScheduler # type: ignore
from mastodon import ( # type: ignore
	Mastodon, MastodonBadGatewayError, MastodonError,
	MastodonGatewayTimeoutError, MastodonServiceUnavailableError,
)
from tweepy.errors import TweepyException # type: ignore

from common import (
	CommonConfig, Database, Recording, Snapshot,
	container_to_lowercase, delete_file,
	get_current_timestamp, setup_logger,
	was_exit_command_entered,
)

class PublishConfig(CommonConfig):
	""" The configuration that applies to the publisher script. """

	# From the config file.
	scheduler: dict[str, Union[int, str]]
	num_recordings_per_scheduled_batch: int
	
	enable_twitter: bool
	enable_mastodon: bool

	require_approval: bool
	flag_sensitive_snapshots: bool
	show_media_metadata: bool
	reply_with_text_to_speech: bool
	delete_files_after_upload: bool
	api_wait: int

	twitter_api_key: str
	twitter_api_secret: str
	twitter_access_token: str
	twitter_access_token_secret: str
	
	twitter_max_retries: int
	twitter_retry_wait: int

	twitter_max_status_length: int
	twitter_text_to_speech_segment_duration: int
	twitter_max_text_to_speech_segments: Optional[int]
	twitter_text_to_speech_upload_wait: int
	
	mastodon_instance_url: str
	mastodon_access_token: str

	mastodon_max_retries: int
	mastodon_retry_wait: int

	mastodon_max_status_length: int
	mastodon_max_file_size: Optional[int]

	mastodon_enable_ffmpeg: bool
	mastodon_ffmpeg_output_args: dict[str, Union[None, int, str]]

	def __init__(self):
		super().__init__()
		self.load_subconfig('publish')

		self.scheduler = container_to_lowercase(self.scheduler)

		if self.twitter_api_key is None:
			self.twitter_api_key = os.environ['WANDERER_TWITTER_API_KEY']

		if self.twitter_api_secret is None:
			self.twitter_api_secret = os.environ['WANDERER_TWITTER_API_SECRET']

		if self.twitter_access_token is None:
			self.twitter_access_token = os.environ['WANDERER_TWITTER_ACCESS_TOKEN']

		if self.twitter_access_token_secret is None:
			self.twitter_access_token_secret = os.environ['WANDERER_TWITTER_ACCESS_TOKEN_SECRET']

		if self.mastodon_access_token is None:
			self.mastodon_access_token = os.environ['WANDERER_MASTODON_ACCESS_TOKEN']

		if self.mastodon_max_file_size is not None:
			self.mastodon_max_file_size = self.mastodon_max_file_size * 10 ** 6

		self.mastodon_ffmpeg_output_args = container_to_lowercase(self.mastodon_ffmpeg_output_args)

if __name__ == '__main__':

	parser = ArgumentParser(description='Publishes the previously recorded snapshots to Twitter and Mastodon on a set schedule. The publisher script uploads each snapshot\'s MP4 video and generates a tweet with the web page\'s title, its date, and a link to its Wayback Machine capture.')
	parser.add_argument('max_iterations', nargs='?', type=int, default=-1, help='How many snapshots to publish. Omit or set to %(default)s to run forever on a set schedule.')
	args = parser.parse_args()

	config = PublishConfig()
	log = setup_logger('publish')

	if not config.enable_twitter and not config.enable_mastodon:
		parser.error('The configuration must enable publishing to at least one platform.')

	log.info('Initializing the publisher.')

	if config.enable_twitter:
		
		try:
			# At the time of writing, you need to use the standard Twitter API version 1.1 to upload videos.
			# This requires having both elevated access and using OAuth 1.0a.
			log.info('Initializing the Twitter API interface.')
			twitter_auth = tweepy.OAuth1UserHandler(config.twitter_api_key, config.twitter_api_secret,
													config.twitter_access_token, config.twitter_access_token_secret)
			twitter_api = tweepy.API(twitter_auth, 	retry_count=config.twitter_max_retries, retry_delay=config.twitter_retry_wait,
													retry_errors=[408, 502, 503, 504], wait_on_rate_limit=True)
		except TweepyException as error:
			log.error(f'Failed to initialize the Twitter API interface with the error: {repr(error)}')
			sys.exit(1)

		def publish_to_twitter(recording: Recording, title: str, body: str, alt_text: str, sensitive: bool, tts_body: str, tts_alt_text: str) -> tuple[Optional[int], Optional[int]]:
			""" Publishes a snapshot recording and text-to-speech file to Twitter. The video recording is added to the main tweet along
			with a message whose content is generated using the remaining arguments. The text-to-speech file is added as a reply to the
			main tweet. If this file is too long for Twitter's video duration limit, then it's split across multiple replies. """
			
			log.info('Publishing to Twitter.')

			media_id = None
			status_id = None

			try:
				media = twitter_api.chunked_upload(filename=recording.UploadFilePath, file_type='video/mp4', media_category='TweetVideo')
				media_id = media.media_id

				# At the time of writing, you can't add alt text to videos.
				# See: https://docs.tweepy.org/en/stable/api.html#tweepy.API.create_media_metadata
				if False:
					sleep(config.api_wait)
					twitter_api.create_media_metadata(media_id, alt_text)
				
				max_title_length = max(config.twitter_max_status_length - len(body), 0)
				text = f'{title[:max_title_length]}\n{body}'

				sleep(config.api_wait)
				status = twitter_api.update_status(text, media_ids=[media_id], possibly_sensitive=sensitive)
				status_id = status.id
				
				log.info(f'Posted the recording status #{status_id} with the media #{media_id} using {len(text)} characters.')

				# Add the text-to-speech file as a reply to the previous tweet. While Twitter has a generous
				# file size limit, the maximum video duration isn't great for the text-to-speech files. To
				# get around this, we'll split the video into segments and chain them together in the replies.
				if config.reply_with_text_to_speech and recording.TextToSpeechFilename is not None:

					temporary_path = tempfile.gettempdir()
					segment_path_format = os.path.join(temporary_path, CommonConfig.TEMPORARY_PATH_PREFIX + '%04d.' + recording.TextToSpeechFilename)

					stream = ffmpeg.input(recording.TextToSpeechFilePath)
					stream = stream.output(segment_path_format, c='copy', f='segment', segment_time=config.twitter_text_to_speech_segment_duration, reset_timestamps=1)
					stream = stream.global_args(*config.ffmpeg_global_args)
					stream = stream.overwrite_output()
					
					log.debug(f'Splitting the text-to-speech file with the FFmpeg arguments: {stream.get_args()}')
					stream.run()

					segment_search_path = os.path.join(temporary_path, '*.' + recording.TextToSpeechFilename)
					segment_file_paths = sorted(glob(segment_search_path))
					last_status_id = status_id

					try:
						if config.twitter_max_text_to_speech_segments is None or len(segment_file_paths) <= config.twitter_max_text_to_speech_segments:
							
							for i, segment_path in enumerate(segment_file_paths):

								sleep(config.api_wait)
								tts_media = twitter_api.chunked_upload(filename=segment_path, file_type='video/mp4', media_category='TweetVideo')
								tts_media_id = tts_media.media_id

								# See above.
								if False:
									sleep(config.api_wait)
									twitter_api.create_media_metadata(tts_media_id, tts_alt_text)

								segment_body = tts_body
								
								if len(segment_file_paths) > 1:
									segment_body += f'\n{i+1} of {len(segment_file_paths)}'
								
								max_title_length = max(config.twitter_max_status_length - len(segment_body), 0)
								tts_text = f'{title[:max_title_length]}\n{segment_body}'

								sleep(config.api_wait)
								tts_status = twitter_api.update_status(tts_text, in_reply_to_status_id=last_status_id, media_ids=[tts_media_id], possibly_sensitive=sensitive)
								last_status_id = tts_status.id

								log.debug(f'Posted the text-to-speech status #{last_status_id} with the media #{tts_media_id} ({i+1} of {len(segment_file_paths)}) using {len(tts_text)} characters.')

							log.info(f'Posted {len(segment_file_paths)} text-to-speech segments.')
						else:
							log.info(f'Skipping {len(segment_file_paths)} text-to-speech segments since it exceeds the limit of {config.twitter_max_text_to_speech_segments} files.')
	
					except TweepyException as error:
						log.error(f'Failed to post the text-to-speech segments with the error: {repr(error)}')
					finally:
						for segment_path in segment_file_paths:
							delete_file(segment_path)

			except TweepyException as error:
				log.error(f'Failed to post the recording status with the error: {repr(error)}')
			except ffmpeg.Error as error:
				log.error(f'Failed to split the text-to-speech file with the error: {repr(error)}')

			return media_id, status_id

	if config.enable_mastodon:
		
		try:
			log.info('Initializing the Mastodon API interface.')
			mastodon_api = Mastodon(access_token=config.mastodon_access_token, api_base_url=config.mastodon_instance_url)
		except MastodonError as error:
			log.error(f'Failed to initialize the Mastodon API interface with the error: {repr(error)}')
			sys.exit(1)

		def publish_to_mastodon(recording: Recording, title: str, body: str, alt_text: str, sensitive: bool, tts_body: str, tts_alt_text: str) -> tuple[Optional[int], Optional[int]]:
			""" Publishes a snapshot recording and text-to-speech file to a given Mastodon instance. The video recording is added to the
			main toot along with a message whose content is generated using the remaining arguments. The text-to-speech file is added
			as a reply to the main toot. This function can optionally attempt to reduce both files' size before uploading them. If a
			file exceeds the user-defined size limit, then it will be skipped. """

			log.info('Publishing to Mastodon.')

			def process_video_file(input_path: str) -> str:
				""" Runs a video file through FFmpeg, potentially reducing its size before uploading it to the Mastodon instance. """
				
				# Closing the file right away makes it easier to delete it later.
				output_file = NamedTemporaryFile(mode='wb', prefix=CommonConfig.TEMPORARY_PATH_PREFIX, suffix='.mp4', delete=False)
				output_file.close()

				stream = ffmpeg.input(input_path)
				stream = stream.output(output_file.name, **config.mastodon_ffmpeg_output_args)
				stream = stream.global_args(*config.ffmpeg_global_args)
				stream = stream.overwrite_output()
				
				log.debug(f'Processing the video file with the FFmpeg arguments: {stream.get_args()}')
				stream.run()

				return output_file.name

			def try_media_post(path: str, **kwargs) -> int:
				""" Posts a media file to the Mastodon instance, retrying if it fails with a 502, 503, or 504 HTTP error. """

				for i in range(config.mastodon_max_retries):
					try:
						media = mastodon_api.media_post(path, **kwargs)
						media_id = media.id
						break
					except (MastodonBadGatewayError, MastodonServiceUnavailableError, MastodonGatewayTimeoutError) as error:
						log.warning(f'Retrying the media post operation ({i+1} of {config.mastodon_max_retries}) after failing with the error: {repr(error)}')
						sleep(config.mastodon_retry_wait)
				else:
					raise

				return media_id

			def try_status_post(text: str, **kwargs) -> int:
				""" Posts a status to the Mastodon instance, retrying if it fails with a 502, 503, or 504 HTTP error. """

				for i in range(config.mastodon_max_retries):
					try:
						status = mastodon_api.status_post(text, **kwargs)
						status_id = status.id
						break
					except (MastodonBadGatewayError, MastodonServiceUnavailableError, MastodonGatewayTimeoutError) as error:
						log.warning(f'Retrying the status post operation ({i+1} of {config.mastodon_max_retries}) after failing with the error: {repr(error)}')
						sleep(config.mastodon_retry_wait)
				else:
					raise

				return status_id

			media_id = None
			status_id = None

			recording_path = None
			tts_path = None

			idempotency_key_prefix = get_current_timestamp() + ' '
			recording_idempotency_key = idempotency_key_prefix + 'recording'
			tts_idempotency_key = idempotency_key_prefix + 'tts'

			try:
				recording_path = process_video_file(recording.UploadFilePath) if config.mastodon_enable_ffmpeg else recording.UploadFilePath
				recording_file_size = os.path.getsize(recording_path)
				
				if config.mastodon_max_file_size is None or recording_file_size <= config.mastodon_max_file_size:

					media_id = try_media_post(recording_path, mime_type='video/mp4', description=alt_text)

					max_title_length = max(config.mastodon_max_status_length - len(body), 0)
					text = f'{title[:max_title_length]}\n{body}'

					sleep(config.api_wait)
					status_id = try_status_post(text, media_ids=[media_id], sensitive=sensitive, idempotency_key=recording_idempotency_key)

					log.info(f'Posted the recording status #{status_id} with the media #{media_id} ({recording_file_size / 10 ** 6:.1f} MB) using {len(text)} characters.')

					try:
						# Unlike with Twitter, uploading videos to Mastodon can be trickier due to hosting costs.
						# We'll try to reduce the file size while also having a size limit for both files.
						if config.reply_with_text_to_speech and recording.TextToSpeechFilePath is not None:

							tts_path = process_video_file(recording.TextToSpeechFilePath) if config.mastodon_enable_ffmpeg else recording.TextToSpeechFilePath
							tts_file_size = os.path.getsize(tts_path)

							if config.mastodon_max_file_size is None or tts_file_size <= config.mastodon_max_file_size:

								sleep(config.api_wait)
								tts_media_id = try_media_post(tts_path, mime_type='video/mp4', description=tts_alt_text)

								max_title_length = max(config.mastodon_max_status_length - len(tts_body), 0)
								tts_text = f'{title[:max_title_length]}\n{tts_body}'

								sleep(config.api_wait)
								tts_status_id = try_status_post(tts_text, in_reply_to_id=status_id, media_ids=[tts_media_id], sensitive=sensitive, idempotency_key=tts_idempotency_key)

								log.info(f'Posted the text-to-speech status #{tts_status_id} with the media #{tts_media_id} ({tts_file_size / 10 ** 6:.1f} MB) using {len(tts_text)} characters.')
							else:
								log.info(f'Skipping the text-to-speech file since its size ({tts_file_size / 10 ** 6:.1f}) exceeds the limit of {config.mastodon_max_file_size / 10 ** 6} MB.')
					
					except MastodonError as error:
						log.error(f'Failed to post the text-to-speech file with the error: {repr(error)}')
				else:
					log.info(f'Skipping the recording since its size ({recording_file_size / 10 ** 6:.1f}) exceeds the limit of {config.mastodon_max_file_size / 10 ** 6} MB.')

			except MastodonError as error:
				log.error(f'Failed to post the recording status with the error: {repr(error)}')
			except OSError as error:
				log.error(f'Failed to determine the video file size with the error: {repr(error)}')
			except ffmpeg.Error as error:
				log.error(f'Failed to process the video file with the error: {repr(error)}')
			finally:
				if config.mastodon_enable_ffmpeg:
					
					if recording_path is not None:
						delete_file(recording_path)
					
					if tts_path is not None:
						delete_file(tts_path)

			return media_id, status_id

	scheduler = BlockingScheduler()

	def publish_recordings(num_recordings: int) -> None:
		""" Publishes the recordings of a given number of snapshots in a single batch. """

		log.info(f'Publishing {num_recordings} recordings.')

		try:
			with Database() as db:

				for recording_index in range(num_recordings):

					if was_exit_command_entered():
						
						log.info('Stopping at the user\'s request.')
						
						try:
							scheduler.shutdown(wait=False)
						except SchedulerNotRunningError:
							pass
						finally:
							break

					try:
						cursor = db.execute('''
											SELECT S.*, SI.IsSensitive, R.*, R.Id AS RecordingId
											FROM Snapshot S
											INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
											INNER JOIN Recording R ON S.Id = R.SnapshotId
											INNER JOIN
											(
												-- Select only the latest recording if multiple files exist.
												-- Processed recordings must be excluded here since we only
												-- care about the unpublished ones.
												SELECT LR.SnapshotId, MAX(LR.CreationTime) AS LastCreationTime
												FROM Recording LR
												WHERE NOT LR.IsProcessed
												GROUP BY LR.SnapshotId
											) LR ON S.Id = LR.SnapshotId AND R.CreationTime = LR.LastCreationTime
											WHERE
												(S.State = :approved_state OR (S.State = :recorded_state AND NOT :require_approval))
												AND NOT R.IsProcessed
											ORDER BY S.Priority DESC, R.CreationTime
											LIMIT 1;
											''', {'approved_state': Snapshot.APPROVED, 'recorded_state': Snapshot.RECORDED,
												  'require_approval': config.require_approval})
						
						row = cursor.fetchone()
						if row is not None:
							
							row = dict(row)
							
							# Avoid naming conflicts with each table's primary key.
							del row['Id']
							snapshot = Snapshot(**row, Id=row['SnapshotId'])
							recording = Recording(**row, Id=row['RecordingId'])

							assert snapshot.IsSensitive is not None, 'The IsSensitive column is not being computed properly.'
							config.apply_snapshot_options(snapshot)
						else:
							log.info('Ran out of snapshots to publish.')
							break
					
					except sqlite3.Error as error:
						log.error(f'Failed to select the next snapshot recording with the error: {repr(error)}')
						sleep(config.database_error_wait)
						continue

					log.info(f'[{recording_index+1} of {num_recordings}] Publishing recording #{recording.Id} of snapshot #{snapshot.Id} {snapshot} (approved = {snapshot.State == Snapshot.APPROVED}).')
					
					title = snapshot.DisplayTitle
					display_metadata = snapshot.DisplayMetadata if config.show_media_metadata else None
					plugin_identifier = '\N{Jigsaw Puzzle Piece}' if snapshot.IsMedia or snapshot.PageUsesPlugins else None
					body_identifiers = [display_metadata, snapshot.ShortDate, snapshot.WaybackUrl, plugin_identifier]
					body = '\n'.join(filter(None, body_identifiers))

					# How the date is formatted depends on the current locale.
					snapshot_type = 'media file' if snapshot.IsMedia else 'web page'
					long_date = snapshot.OldestDatetime.strftime('%B %Y')
					alt_text = f'The {snapshot_type} "{snapshot.Url}" as seen on {long_date} via the Wayback Machine.'
					sensitive = config.flag_sensitive_snapshots and snapshot.IsSensitive
					
					tts_language = f'Text-to-Speech ({snapshot.LanguageName})' if snapshot.LanguageName is not None else 'Text-to-Speech'
					tts_body_identifiers = [snapshot.ShortDate, tts_language]
					tts_body = '\n'.join(filter(None, tts_body_identifiers))
					tts_alt_text = f'An audio recording of the {snapshot_type} "{snapshot.Url}" as seen on {long_date} via the Wayback Machine. Generated using text-to-speech.'

					twitter_media_id, twitter_status_id = publish_to_twitter(recording, title, body, alt_text, sensitive, tts_body, tts_alt_text) if config.enable_twitter else (None, None)
					mastodon_media_id, mastodon_status_id = publish_to_mastodon(recording, title, body, alt_text, sensitive, tts_body, tts_alt_text) if config.enable_mastodon else (None, None)

					if config.delete_files_after_upload:
						delete_file(recording.UploadFilePath)
						
						if recording.TextToSpeechFilePath is not None:
							delete_file(recording.TextToSpeechFilePath)

					try:
						db.execute('UPDATE Snapshot SET State = :state WHERE Id = :id;', {'state': Snapshot.PUBLISHED, 'id': snapshot.Id})

						db.execute(	'''
									UPDATE Recording
									SET
										IsProcessed = :is_processed, PublishTime = :publish_time,
										TwitterMediaId = :twitter_media_id, TwitterStatusId = :twitter_status_id,
										MastodonMediaId = :mastodon_media_id, MastodonStatusId = :mastodon_status_id
									WHERE Id = :id;
									''', {'is_processed': True, 'publish_time': get_current_timestamp(),
										  'twitter_media_id': twitter_media_id, 'twitter_status_id': twitter_status_id,
										  'mastodon_media_id': mastodon_media_id, 'mastodon_status_id': mastodon_status_id,
										  'id': recording.Id})

						# Mark any earlier recordings as processed so the same snapshot isn't published multiple times in
						# cases where there is more than one video file. See the LastCreationTime part in the main query.
						db.execute('UPDATE Recording SET IsProcessed = :is_processed WHERE SnapshotId = :snapshot_id;', {'is_processed': True, 'snapshot_id': snapshot.Id})

						if snapshot.Priority == Snapshot.PUBLISH_PRIORITY:
							db.execute('UPDATE Snapshot SET Priority = :no_priority WHERE Id = :id;', {'no_priority': Snapshot.NO_PRIORITY, 'id': snapshot.Id})

						db.commit()

					except sqlite3.Error as error:
						log.error(f'Failed to update the snapshot\'s status with the error: {repr(error)}')
						db.rollback()
						sleep(config.database_error_wait)
						continue

		except sqlite3.Error as error:
			log.error(f'Failed to connect to the database with the error: {repr(error)}')
		except KeyboardInterrupt:
			pass

		log.info(f'Finished publishing {num_recordings} recordings.')

	if args.max_iterations >= 0:
		publish_recordings(args.max_iterations)
	else:
		log.info(f'Running the publisher with the schedule: {config.scheduler}')
		scheduler.add_job(publish_recordings, args=[config.num_recordings_per_scheduled_batch], trigger='cron', coalesce=True, misfire_grace_time=None, **config.scheduler, timezone='UTC')
		scheduler.start()

	log.info('Terminating the publisher.')