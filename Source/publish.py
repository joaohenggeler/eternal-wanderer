#!/usr/bin/env python3

"""
	This script publishes the previously recorded snapshots to Twitter on a set schedule.
	The publisher script uploads each snapshot's MP4 video and generates a tweet with the web page's title, its date, and a link to its Wayback Machine capture.
"""

import os
import sqlite3
import sys
import tempfile
import time
from argparse import ArgumentParser
from datetime import datetime, timezone
from glob import glob
from typing import Dict, Optional, Tuple, Union

import ffmpeg # type: ignore
import tweepy # type: ignore
from apscheduler.schedulers import SchedulerNotRunningError # type: ignore
from apscheduler.schedulers.blocking import BlockingScheduler # type: ignore

from common import TEMPORARY_PATH_PREFIX, CommonConfig, Database, Recording, Snapshot, delete_file, get_current_timestamp, setup_logger, was_exit_command_entered

####################################################################################################

class PublishConfig(CommonConfig):
	""" The configuration that applies to the publisher script. """

	# From the config file.
	scheduler: Dict[str, Union[int, str]]
	num_recordings_per_scheduled_batch: int
	
	publish_to_twitter: bool
	publish_to_mastodon: bool

	require_approval: bool
	flag_sensitive_snapshots: bool
	show_standalone_media_metadata: bool
	reply_with_text_to_speech: bool
	delete_files_after_publish: bool

	twitter_api_key: str
	twitter_api_secret: str
	twitter_access_token: str
	twitter_access_token_secret: str
	
	twitter_max_retries: int
	twitter_retry_wait: int

	twitter_max_post_length: int
	twitter_text_to_speech_segment_duration: int
	twitter_max_text_to_speech_segments: Optional[int]
	
	def __init__(self):
		super().__init__()
		self.load_subconfig('publish')

if __name__ == '__main__':

	config = PublishConfig()
	log = setup_logger('publish')

	parser = ArgumentParser(description='Publishes the previously recorded snapshots to Twitter on a set schedule. The publisher script uploads each snapshot\'s MP4 video and generates a tweet with the web page\'s title, its date, and a link to its Wayback Machine capture.')
	parser.add_argument('max_iterations', nargs='?', type=int, default=-1, help='How many snapshots to publish. Omit or set to %(default)s to run forever on a set schedule.')
	args = parser.parse_args()

	if not config.publish_to_twitter and not config.publish_to_mastodon:
		parser.error('The configuration must enable posting to at least one platform.')

	####################################################################################################

	log.info('Initializing the publisher.')

	if config.publish_to_twitter:
		
		try:
			# At the time of writing, you need to use the standard Twitter API version 1.1 to upload videos.
			# This requires having both elevated access and using OAuth 1.0a.
			log.info('Initializing the Twitter API interface.')
			twitter_auth = tweepy.OAuth1UserHandler(config.twitter_api_key, config.twitter_api_secret,
													config.twitter_access_token, config.twitter_access_token_secret)
			twitter_api = tweepy.API(twitter_auth, 	retry_count=config.twitter_max_retries, retry_delay=config.twitter_retry_wait,
													retry_errors=[408, 502, 503, 504], wait_on_rate_limit=True)
		except tweepy.errors.TweepyException as error:
			log.error(f'Failed to initialize the Twitter API interface with the error: {repr(error)}')
			sys.exit(1)

		def publish_to_twitter(recording: Recording, title: str, body: str, alt_text: str, sensitive: bool, language: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
			""" Publishes a snapshot recording and text-to-speech file to Twitter. The video recording is added to the main tweet along
			with a message whose content is generated using the remaining arguments. The text-to-speech file is added as a reply to the
			main tweet. If this file is too long for Twitter's video duration limit, then it's split across multiple replies. """
			
			media_id = None
			post_id = None

			try:
				media = twitter_api.chunked_upload(filename=recording.UploadFilePath, file_type='video/mp4', media_category='TweetVideo')
				media_id = media.media_id

				# At the time of writing, you can't add alt text to videos.
				# See: https://docs.tweepy.org/en/stable/api.html#tweepy.API.create_media_metadata
				if False:
					twitter_api.create_media_metadata(media_id, alt_text)
				
				max_title_length = max(config.twitter_max_post_length - len(body), 0)
				title = title[:max_title_length]
				post = f'{title}\n{body}'

				status = twitter_api.update_status(post, media_ids=[media_id], possibly_sensitive=sensitive)
				post_id = status.id

				log.info(f'Posted the tweet #{post_id} with the media #{media_id} using {len(post)} characters.')

				# Add the text-to-speech file as a reply to the previous tweet. While Twitter has a generous
				# file size limit, the maximum video duration isn't great for the text-to-speech files. To
				# get around this, we'll split the video into segments and chain them together in the replies.
				if config.reply_with_text_to_speech and recording.TextToSpeechFilename is not None:

					temporary_path = tempfile.gettempdir()
					segment_path_format = os.path.join(temporary_path, TEMPORARY_PATH_PREFIX + '%04d.' + recording.TextToSpeechFilename)

					stream = ffmpeg.input(recording.TextToSpeechFilePath)
					stream = stream.output(segment_path_format, c='copy', f='segment', segment_time=config.twitter_text_to_speech_segment_duration, reset_timestamps=1)
					stream = stream.global_args(*config.ffmpeg_global_args)
					stream = stream.overwrite_output()
					
					log.debug(f'Splitting the text-to-speech file with the ffmpeg arguments: {stream.get_args()}')
					stream.run()

					segment_search_path = os.path.join(temporary_path, '*.' + recording.TextToSpeechFilename)
					segment_file_paths = sorted(glob(segment_search_path))
					last_post_id = post_id

					try:
						if config.twitter_max_text_to_speech_segments is None or len(segment_file_paths) <= config.twitter_max_text_to_speech_segments:
							
							for i, segment_path in enumerate(segment_file_paths):
									
								segment_media = twitter_api.chunked_upload(filename=segment_path, file_type='video/mp4', media_category='TweetVideo')
								segment_post = 'Text-to-Speech' if len(segment_file_paths) == 1 else f'Text-to-Speech {i+1} of {len(segment_file_paths)}'
								
								if language is not None:
									segment_post += f' ({language})'
								
								segment_status = twitter_api.update_status(segment_post, in_reply_to_status_id=last_post_id, media_ids=[segment_media.media_id], possibly_sensitive=sensitive)
								last_post_id = segment_status.id

								log.debug(f'Posted the text-to-speech segment #{segment_status.id} with the media #{segment_media.media_id} ({i+1} of {len(segment_file_paths)}).')

							log.info(f'Posted {len(segment_file_paths)} text-to-speech segments.')
						else:
							log.info(f'Skipping {len(segment_file_paths)} text-to-speech segments since it exceeds the limit of {config.twitter_max_text_to_speech_segments} files.')
	
					except tweepy.errors.TweepyException as error:
						log.error(f'Failed to post the text-to-speech segments with the error: {repr(error)}')
					finally:
						for segment_path in segment_file_paths:
							delete_file(segment_path)

			except tweepy.errors.TweepyException as error:
				log.error(f'Failed to post the tweet with the error: {repr(error)}')
			except ffmpeg.Error as error:
				log.error(f'Failed to split the text-to-speech file with the error: {repr(error)}')

			return media_id, post_id

	if config.publish_to_mastodon:
		
		def publish_to_mastodon(recording: Recording, title: str, body: str, alt_text: str, sensitive: bool, language: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
			""" @TODO """
			return None, None

	####################################################################################################

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

						break

					try:
						cursor = db.execute('''
											SELECT S.*, SI.IsSensitive, R.*, R.Id AS RecordingId
											FROM Snapshot S
											INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
											INNER JOIN Recording R ON S.Id = R.SnapshotId
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
						time.sleep(config.database_error_wait)
						continue

					log.info(f'[{recording_index+1} of {num_recordings}] Publishing recording #{recording.Id} for snapshot #{snapshot.Id} {snapshot} (approved = {snapshot.State == Snapshot.APPROVED}).')
					
					title = snapshot.DisplayTitle
					display_metadata = snapshot.DisplayMetadata if config.show_standalone_media_metadata else None
					plugin_identifier = '\N{Jigsaw Puzzle Piece}' if snapshot.IsStandaloneMedia or snapshot.PageUsesPlugins else None
					body = '\n'.join(filter(None, [display_metadata, snapshot.ShortDate, snapshot.WaybackUrl, plugin_identifier]))

					# How the date is formatted depends on the current locale.
					long_date = snapshot.OldestDatetime.strftime('%B %Y')
					alt_text = f'The web page "{snapshot.Url}" as seen on {long_date} via the Wayback Machine.'
					sensitive = config.flag_sensitive_snapshots and snapshot.IsSensitive
					language = snapshot.PageLanguage

					twitter_media_id, twitter_post_id = publish_to_twitter(recording, title, body, alt_text, sensitive, language) if config.publish_to_twitter else (None, None)
					mastodon_media_id, mastodon_post_id = publish_to_mastodon(recording, title, body, alt_text, sensitive, language) if config.publish_to_mastodon else (None, None)

					if config.delete_files_after_publish:
						delete_file(recording.UploadFilePath)
						
						if recording.TextToSpeechFilePath is not None:
							delete_file(recording.TextToSpeechFilePath)

					try:
						db.execute('UPDATE Snapshot SET State = :state WHERE Id = :id;', {'state': Snapshot.PUBLISHED, 'id': snapshot.Id})

						db.execute(	'''
									UPDATE Recording
									SET
										IsProcessed = :is_processed, PublishTime = :publish_time,
										TwitterMediaId = :twitter_media_id, TwitterPostId = :twitter_post_id,
										MastodonMediaId = :mastodon_media_id, MastodonPostId = :mastodon_post_id
									WHERE Id = :id;
									''', {'is_processed': True, 'publish_time': get_current_timestamp(),
										  'twitter_media_id': twitter_media_id, 'twitter_post_id': twitter_post_id,
										  'mastodon_media_id': mastodon_media_id, 'mastodon_post_id': mastodon_post_id,
										  'id': recording.Id})

						if snapshot.Priority == Snapshot.PUBLISH_PRIORITY:
							db.execute('UPDATE Snapshot SET Priority = :no_priority WHERE Id = :id;', {'no_priority': Snapshot.NO_PRIORITY, 'id': snapshot.Id})

						db.commit()

					except sqlite3.Error as error:
						log.error(f'Failed to update the snapshot\'s status with the error: {repr(error)}')
						db.rollback()
						time.sleep(config.database_error_wait)
						continue

		except sqlite3.Error as error:
			log.error(f'Failed to connect to the database with the error: {repr(error)}')
		except KeyboardInterrupt:
			pass

		log.info(f'Finished publishing {num_recordings} recordings.')

	####################################################################################################

	if args.max_iterations >= 0:
		publish_recordings(args.max_iterations)
	else:
		log.info(f'Running the publisher with the schedule: {config.scheduler}')
		scheduler.add_job(publish_recordings, args=[config.num_recordings_per_scheduled_batch], trigger='cron', coalesce=True, misfire_grace_time=None, **config.scheduler, timezone='UTC')
		scheduler.start()

	log.info('Terminating the publisher.')