#!/usr/bin/env python3

"""
	@TODO
"""

import os
import sqlite3
import sys
import time
from argparse import ArgumentParser
from datetime import datetime, timezone
from typing import Dict, Union, cast
from urllib.parse import urlparse

import tweepy # type: ignore
from apscheduler.schedulers import SchedulerNotRunningError # type: ignore
from apscheduler.schedulers.blocking import BlockingScheduler # type: ignore

from common import CommonConfig, Database, Recording, Snapshot, delete_file, get_current_timestamp, setup_root_logger, was_exit_command_entered

####################################################################################################

class UploadConfig(CommonConfig):

	# From the config file.
	scheduler: Dict[str, Union[int, str]]
	num_snapshots_per_scheduled_batch: int
	require_approval: bool

	twitter_api_key: str
	twitter_api_secret: str
	twitter_access_token: str
	twitter_access_token_secret: str
	
	twitter_max_retries: int
	twitter_retry_wait: int

	max_tweet_length: int
	mark_filtered_snapshots_as_sensitive: bool
	delete_video_after_upload: bool

	def __init__(self):
		super().__init__()
		self.load_subconfig('upload')

config = UploadConfig()
log = setup_root_logger('upload')

parser = ArgumentParser(description='@TODO')
parser.add_argument('max_iterations', nargs='?', type=int, default=-1, help='@TODO')
args = parser.parse_args()

####################################################################################################

log.info('Initializing the uploader.')

try:
	auth = tweepy.OAuth1UserHandler(config.twitter_api_key, config.twitter_api_secret,
									config.twitter_access_token, config.twitter_access_token_secret)
	api = tweepy.API(auth, 	retry_count=config.twitter_max_retries, retry_delay=config.twitter_retry_wait,
							retry_errors=[408, 502, 503, 504], wait_on_rate_limit=True)
except tweepy.errors.TweepyException as error:
	log.error(f'Failed to create the Twitter API interface with the error: {repr(error)}')
	sys.exit(1)

scheduler = BlockingScheduler()

def upload_snapshot_recording(num_snapshots: int) -> None:
	
	try:
		with Database() as db:

			for snapshot_index in range(num_snapshots):

				if was_exit_command_entered():
					log.info('Stopping at the user\'s request.')
					
					try:
						scheduler.shutdown(wait=False)
					except SchedulerNotRunningError:
						pass

					break

				try:
					cursor = db.execute('''
										SELECT S.*, R.*, R.Id AS RecordingId
										FROM Snapshot S
										INNER JOIN Recording R ON S.Id = R.SnapshotId
										WHERE (S.State = :approved_state OR (S.State = :recorded_state AND NOT :require_approval))
											AND NOT R.IsProcessed
										ORDER BY
											S.Priority DESC,
											R.CreationTime
										LIMIT 1;
										''', {'approved_state': Snapshot.APPROVED, 'recorded_state': Snapshot.RECORDED,
											  'require_approval': config.require_approval})
					
					row = cursor.fetchone()
					if row is not None:
						row = dict(row)
						del row['Id']
						snapshot = Snapshot(**row, Id=row['SnapshotId'])
						recording = Recording(**row, Id=row['RecordingId'])
					else:
						log.info('Ran out of snapshots to upload.')
						break
				except sqlite3.Error as error:
					log.error(f'Failed to select the next snapshot recording with the error: {repr(error)}')
					time.sleep(config.database_error_wait)
					continue

				try:
					log.info(f'[{snapshot_index+1} of {num_snapshots}] Uploading recording #{recording.Id} for snapshot #{snapshot.Id} {snapshot} (approved = {snapshot.State == Snapshot.APPROVED}).')

					media = api.chunked_upload(filename=recording.UploadFilePath, file_type='video/mp4', media_category='TweetVideo')
					media_id = media.media_id

					if config.delete_video_after_upload:
						delete_file(recording.UploadFilePath)

					snapshot_datetime = datetime.strptime(snapshot.Timestamp, '%Y%m%d%H%M%S')			
					# date = snapshot_datetime.strftime('%B %Y')
					# api.create_media_metadata(media_id, f'The web page "{snapshot.Url}" as seen on {date} via the Wayback Machine.')
					
					date = snapshot_datetime.strftime('%b %Y')
					required_text = f'\n{date}\n{snapshot.WaybackUrl}'
					required_length = len(required_text)

					if snapshot.UsesPlugins:
						emoji = '\N{jigsaw puzzle piece}'
						required_text += '\n' + emoji
						required_length += len('\n') + len(emoji) * 2

					title = cast(str, snapshot.Title)
					if not title:
						parts = urlparse(snapshot.Url)
						title = os.path.basename(parts.path)

					max_title_length = max(config.max_tweet_length - required_length, 0)
					tweet = title[:max_title_length] + required_text
					sensitive = config.mark_filtered_snapshots_as_sensitive and snapshot.IsFiltered

					status = api.update_status(tweet, media_ids=[media_id], possibly_sensitive=sensitive)
					
					tweet = status.text.replace('\n', ' ')
					log.info(f'Published the snapshot\'s tweet using {len(tweet)} characters (truncated = {status.truncated}): "{tweet}".')

					if 'media' in status.extended_entities:
						for entity in status.extended_entities['media']:
							
							if entity['id'] == media_id:
								
								if 'video_info' in entity and 'variants' in entity['video_info']:
									for i, info in enumerate(entity['video_info']['variants']):
										url = info.get('url')
										log.info(f'Uploaded video variant #{i+1}: "{url}".')
								
								break

					db.execute('UPDATE Snapshot SET State = :state WHERE Id = :id;', {'state': Snapshot.UPLOADED, 'id': snapshot.Id})

					db.execute('UPDATE Recording SET IsProcessed = :is_processed, UploadTime = :upload_time, MediaId = :media_id, TweetId = :tweet_id WHERE Id = :id;',
							   {'is_processed': True, 'upload_time': get_current_timestamp(), 'media_id': media_id, 'tweet_id': status.id, 'id': recording.Id})

					if snapshot.Priority == Snapshot.UPLOAD_PRIORITY:
						db.execute('UPDATE Snapshot SET Priority = :no_priority WHERE Id = :id;', {'no_priority': Snapshot.NO_PRIORITY, 'id': snapshot.Id})

					db.commit()

				except sqlite3.Error as error:
					log.error(f'Failed to update the snapshot\'s status with the error: {repr(error)}')
					db.rollback()
					time.sleep(config.database_error_wait)
					continue
				except tweepy.errors.TweepyException as error:
					log.error(f'Failed to publish the tweet with the error: {repr(error)}')
					continue

	except sqlite3.Error as error:
		log.error(f'Failed to connect to the database with the error: {repr(error)}')
	except KeyboardInterrupt:
		pass

####################################################################################################

if args.max_iterations >= 0:
	upload_snapshot_recording(args.max_iterations)
else:
	scheduler.add_job(upload_snapshot_recording, args=[config.num_snapshots_per_scheduled_batch], trigger='cron', coalesce=True, **config.scheduler, timezone='UTC')
	scheduler.start()

log.info('Terminating the uploader.')