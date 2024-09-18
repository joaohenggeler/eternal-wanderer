#!/usr/bin/env python3

import os
import sqlite3
import sys
import tempfile
from argparse import ArgumentParser
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep
from typing import Optional, Union
from uuid import uuid4

import tweepy # type: ignore
from apscheduler.schedulers import SchedulerNotRunningError # type: ignore
from apscheduler.schedulers.blocking import BlockingScheduler # type: ignore
from mastodon import ( # type: ignore
	Mastodon, MastodonBadGatewayError, MastodonError,
	MastodonGatewayTimeoutError, MastodonNetworkError,
	MastodonServiceUnavailableError,
)
from pytumblr import TumblrRestClient # type: ignore
from tweepy.errors import TweepyException # type: ignore

from common.config import CommonConfig
from common.database import Database
from common.ffmpeg import ffmpeg, FfmpegException
from common.logger import setup_logger
from common.net import is_url_from_domain, tld_extract
from common.recording import Recording
from common.snapshot import Snapshot
from common.util import (
	container_to_lowercase, delete_file,
	was_exit_command_entered,
)

@dataclass
class PublishConfig(CommonConfig):
	""" The configuration that applies to the publisher script. """

	# From the config file.
	scheduler: dict[str, Union[int, str]]
	num_recordings_per_scheduled_batch: int

	require_approval: bool
	reply_with_text_to_speech: bool
	delete_files_after_upload: bool

	enable_twitter: bool
	enable_mastodon: bool
	enable_tumblr: bool

	twitter_api_key: str
	twitter_api_secret: str
	twitter_access_token: str
	twitter_access_token_secret: str

	twitter_api_wait: int
	twitter_max_retries: int
	twitter_retry_wait: int

	twitter_max_status_length: int
	twitter_text_to_speech_segment_duration: int
	twitter_max_text_to_speech_segments: Optional[int]

	mastodon_instance_url: str
	mastodon_access_token: str

	mastodon_max_retries: int
	mastodon_retry_wait: int

	mastodon_max_status_length: int
	mastodon_max_file_size: Optional[int]

	tumblr_api_key: str
	tumblr_api_secret: str
	tumblr_access_token: str
	tumblr_access_token_secret: str

	tumblr_max_retries: int
	tumblr_retry_wait: int

	tumblr_max_status_length: int

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

		if self.tumblr_api_key is None:
			self.tumblr_api_key = os.environ['WANDERER_TUMBLR_API_KEY']

		if self.tumblr_api_secret is None:
			self.tumblr_api_secret = os.environ['WANDERER_TUMBLR_API_SECRET']

		if self.tumblr_access_token is None:
			self.tumblr_access_token = os.environ['WANDERER_TUMBLR_ACCESS_TOKEN']

		if self.tumblr_access_token_secret is None:
			self.tumblr_access_token_secret = os.environ['WANDERER_TUMBLR_ACCESS_TOKEN_SECRET']

if __name__ == '__main__':

	parser = ArgumentParser(description='Publishes the previously recorded snapshots on Twitter, Mastodon, and Tumblr on a set schedule. The publisher script uploads the recordings and generates posts with the web page\'s title, its date, and a link to its Wayback Machine capture.')
	parser.add_argument('max_iterations', nargs='?', type=int, default=-1, help='How many snapshots to publish. Omit or set to %(default)s to run forever on a set schedule.')
	args = parser.parse_args()

	config = PublishConfig()
	log = setup_logger('publish')

	if not config.enable_twitter and not config.enable_mastodon and not config.enable_tumblr:
		parser.error('The configuration must enable publishing to at least one platform.')

	log.info('Initializing the publisher.')

	@dataclass(frozen=True)
	class Post:
		recording: Recording

		title: str
		body: str
		html_body: str
		alt_text: str

		tts_body: str
		tts_alt_text: str

		sensitive: bool
		emojis: str
		tags: list[str]

	if config.enable_twitter:

		try:
			# At the time of writing, you need to use the standard Twitter API version 1.1 to upload videos.
			# This requires having elevated access and using OAuth 1.0a. You also need to use version 2 of
			# the API to create tweets.
			log.info('Initializing the Twitter API interface.')

			twitter_auth = tweepy.OAuth1UserHandler(
				consumer_key=config.twitter_api_key,
				consumer_secret=config.twitter_api_secret,
				access_token=config.twitter_access_token,
				access_token_secret=config.twitter_access_token_secret,
			)

			twitter_api_v1 = tweepy.API(
				twitter_auth,
				retry_count=config.twitter_max_retries,
				retry_delay=config.twitter_retry_wait,
				retry_errors=[408, 502, 503, 504],
				wait_on_rate_limit=True,
			)

			twitter_api_v2 = tweepy.Client(
				consumer_key=config.twitter_api_key,
				consumer_secret=config.twitter_api_secret,
				access_token=config.twitter_access_token,
				access_token_secret=config.twitter_access_token_secret,
				wait_on_rate_limit=True,
			)

		except TweepyException as error:
			log.error(f'Failed to initialize the Twitter API interface with the error: {repr(error)}')
			sys.exit(1)

		def publish_to_twitter(post: Post) -> tuple[Optional[int], Optional[int]]:
			""" Publishes a snapshot recording and text-to-speech file on Twitter. The video recording is added to the main post along
			with a message whose content is generated using the remaining arguments. The text-to-speech file is added as a reply to the
			main post. If this file is too long for Twitter's video duration limit, then it's split across multiple replies. """

			log.info('Publishing on Twitter.')

			media_id = None
			status_id = None

			try:
				media = twitter_api_v1.chunked_upload(filename=str(post.recording.UploadFilePath), file_type='video/mp4', media_category='TweetVideo')
				media_id = media.media_id

				# At the time of writing, you can't add alt text to videos.
				# See: https://docs.tweepy.org/en/stable/api.html#tweepy.API.create_media_metadata
				if False:
					sleep(config.twitter_api_wait)
					twitter_api_v1.create_media_metadata(media_id, post.alt_text)

				# Note that emojis count as two characters on Twitter.
				max_title_length = max(config.twitter_max_status_length - len('\n') - len(post.body) - 2 * len(post.emojis), 0)
				text = post.title[:max_title_length] + '\n' + post.body

				sleep(config.twitter_api_wait)
				response = twitter_api_v2.create_tweet(text=text, media_ids=[media_id])
				status_id = int(response.data['id'])

				log.info(f'Posted the recording status #{status_id} with the media #{media_id} using {len(text)} characters.')

				# Add the text-to-speech file as a reply to the previous tweet. While Twitter has a generous
				# file size limit, the maximum video duration isn't great for the text-to-speech files. To
				# get around this, we'll split the video into segments and chain them together in the replies.
				if config.reply_with_text_to_speech and post.recording.TextToSpeechFilename is not None:

					temporary_path = Path(tempfile.gettempdir())
					segment_path_format = temporary_path / (CommonConfig.TEMPORARY_PATH_PREFIX + '%04d.' + post.recording.TextToSpeechFilename)

					input_args = ['-i', post.recording.TextToSpeechFilePath]
					output_args = [
						'-c', 'copy',
						'-f', 'segment',
						'-segment_time', config.twitter_text_to_speech_segment_duration,
						'-reset_timestamps', 1,
						segment_path_format,
					]

					log.debug(f'Splitting the text-to-speech file with the FFmpeg arguments: {input_args + output_args}')
					ffmpeg(*input_args, *output_args)

					segment_file_paths = sorted(temporary_path.glob('*.' + post.recording.TextToSpeechFilename))
					last_status_id = status_id

					try:
						if config.twitter_max_text_to_speech_segments is None or len(segment_file_paths) <= config.twitter_max_text_to_speech_segments:

							for i, segment_path in enumerate(segment_file_paths, start=1):

								sleep(config.twitter_api_wait)
								tts_media = twitter_api_v1.chunked_upload(filename=str(segment_path), file_type='video/mp4', media_category='TweetVideo')
								tts_media_id = tts_media.media_id

								# See above.
								if False:
									sleep(config.twitter_api_wait)
									twitter_api_v1.create_media_metadata(tts_media_id, post.tts_alt_text)

								segment_body = post.tts_body

								if len(segment_file_paths) > 1:
									segment_body += f'\n{i} of {len(segment_file_paths)}'

								max_title_length = max(config.twitter_max_status_length - len('\n') - len(segment_body), 0)
								tts_text = post.title[:max_title_length] + '\n' + segment_body

								sleep(config.twitter_api_wait)
								tts_response = twitter_api_v2.create_tweet(text=tts_text, in_reply_to_tweet_id=last_status_id, media_ids=[tts_media_id])
								last_status_id = int(tts_response.data['id'])

								log.debug(f'Posted the text-to-speech status #{last_status_id} with the media #{tts_media_id} ({i} of {len(segment_file_paths)}) using {len(tts_text)} characters.')

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
			except FfmpegException as error:
				log.error(f'Failed to split the text-to-speech file with the error: {repr(error)}')

			return media_id, status_id

	if config.enable_mastodon:

		try:
			log.info('Initializing the Mastodon API interface.')
			mastodon_api = Mastodon(access_token=config.mastodon_access_token, api_base_url=config.mastodon_instance_url)
		except MastodonError as error:
			log.error(f'Failed to initialize the Mastodon API interface with the error: {repr(error)}')
			sys.exit(1)

		def publish_to_mastodon(post: Post) -> tuple[Optional[int], Optional[int]]:
			""" Publishes a snapshot recording and text-to-speech file on a given Mastodon instance. The video recording is added to the
			main post along with a message whose content is generated using the remaining arguments. The text-to-speech audio is added
			as a reply to the main post. This function can optionally attempt to reduce the recording size before uploading it. If the
			file exceeds the user-defined size limit, then it will be skipped. """

			log.info('Publishing on Mastodon.')

			def reduce_video_size(path: Path) -> Path:
				""" Reduces a video's file size. """

				# Closing the file right away makes it easier to delete it later.
				output_file = NamedTemporaryFile(mode='wb', prefix=CommonConfig.TEMPORARY_PATH_PREFIX, suffix='.mp4', delete=False)
				output_file.close()

				input_args = ['-i', path]
				output_args = ['-vf', 'fps=30', output_file.name]

				log.debug(f'Reducing the video size with the FFmpeg arguments: {input_args + output_args}')
				ffmpeg(*input_args, *output_args)

				return Path(output_file.name)

			def extract_audio(path: Path) -> Path:
				""" Extracts the audio from a video file. """

				# Closing the file right away makes it easier to delete it later.
				output_file = NamedTemporaryFile(mode='wb', prefix=CommonConfig.TEMPORARY_PATH_PREFIX, suffix='.mp3', delete=False)
				output_file.close()

				input_args = ['-i', path]
				output_args = [output_file.name]

				log.debug(f'Extracting the audio with the FFmpeg arguments: {input_args + output_args}')
				ffmpeg(*input_args, *output_args)

				return Path(output_file.name)

			def try_media_post(path: Path, **kwargs) -> int:
				""" Posts a media file to the Mastodon instance, retrying if it fails with a 502, 503, or 504 HTTP error. """

				for i in range(config.mastodon_max_retries):
					try:
						media = mastodon_api.media_post(str(path), **kwargs)
						break
					except (MastodonNetworkError, MastodonBadGatewayError, MastodonServiceUnavailableError, MastodonGatewayTimeoutError) as error:
						log.warning(f'Retrying the media post operation ({i+1} of {config.mastodon_max_retries}) after failing with the error: {repr(error)}')
						sleep(config.mastodon_retry_wait)
				else:
					raise

				return media.id

			def try_status_post(text: str, **kwargs) -> int:
				""" Posts a status to the Mastodon instance, retrying if it fails with a 502, 503, or 504 HTTP error. """

				idempotency_key = str(uuid4())

				for i in range(config.mastodon_max_retries):
					try:
						status = mastodon_api.status_post(text, idempotency_key=idempotency_key, **kwargs)
						break
					except (MastodonNetworkError, MastodonBadGatewayError, MastodonServiceUnavailableError, MastodonGatewayTimeoutError) as error:
						log.warning(f'Retrying the status post operation ({i+1} of {config.mastodon_max_retries}) after failing with the error: {repr(error)}')
						sleep(config.mastodon_retry_wait)
				else:
					raise

				return status.id

			media_id = None
			status_id = None

			recording_path = None
			tts_path = None

			try:
				# Unlike with Twitter, uploading videos to Mastodon can be trickier due to hosting costs.
				# We'll try to reduce the file size while also having a maximum size limit.
				recording_path = reduce_video_size(post.recording.UploadFilePath)
				recording_file_size = os.path.getsize(recording_path)

				if config.mastodon_max_file_size is None or recording_file_size <= config.mastodon_max_file_size:

					media_id = try_media_post(recording_path, mime_type='video/mp4', description=post.alt_text)

					max_title_length = max(config.mastodon_max_status_length - len('\n') - len(post.body), 0)
					text = post.title[:max_title_length] + '\n' + post.body

					status_id = try_status_post(text, media_ids=[media_id], sensitive=post.sensitive)

					log.info(f'Posted the recording status #{status_id} with the media #{media_id} ({recording_file_size / 10 ** 6:.1f} MB) using {len(text)} characters.')

					try:
						if config.reply_with_text_to_speech and post.recording.TextToSpeechFilePath is not None:

							tts_path = extract_audio(post.recording.TextToSpeechFilePath)
							tts_file_size = os.path.getsize(tts_path)

							if config.mastodon_max_file_size is None or tts_file_size <= config.mastodon_max_file_size:

								tts_media_id = try_media_post(tts_path, mime_type='audio/mpeg', description=post.tts_alt_text)

								max_title_length = max(config.mastodon_max_status_length - len('\n') - len(post.tts_body), 0)
								tts_text = post.title[:max_title_length] + '\n' + post.tts_body

								tts_status_id = try_status_post(tts_text, in_reply_to_id=status_id, media_ids=[tts_media_id], sensitive=post.sensitive)

								log.info(f'Posted the text-to-speech status #{tts_status_id} with the media #{tts_media_id} ({tts_file_size / 10 ** 6:.1f} MB) using {len(tts_text)} characters.')
							else:
								log.info(f'Skipping the text-to-speech audio since its size ({tts_file_size / 10 ** 6:.1f}) exceeds the limit of {config.mastodon_max_file_size / 10 ** 6} MB.')

					except MastodonError as error:
						log.error(f'Failed to post the text-to-speech audio with the error: {repr(error)}')
				else:
					log.info(f'Skipping the recording since its size ({recording_file_size / 10 ** 6:.1f}) exceeds the limit of {config.mastodon_max_file_size / 10 ** 6} MB.')

			except MastodonError as error:
				log.error(f'Failed to post the recording status with the error: {repr(error)}')
			except OSError as error:
				log.error(f'Failed to determine the video file size with the error: {repr(error)}')
			except FfmpegException as error:
				log.error(f'Failed to process the video file with the error: {repr(error)}')
			finally:
				if recording_path is not None:
					delete_file(recording_path)

				if tts_path is not None:
					delete_file(tts_path)

			return media_id, status_id

	if config.enable_tumblr:

		try:
			log.info('Initializing the Tumblr API interface.')
			tumblr_api = TumblrRestClient(config.tumblr_api_key, config.tumblr_api_secret,
										  config.tumblr_access_token, config.tumblr_access_token_secret)
			info = tumblr_api.info()
			tumblr_blog_name = info['user']['name']
		except Exception as error:
			log.error(f'Failed to initialize the Tumblr API interface with the error: {repr(error)}')
			sys.exit(1)

		def publish_to_tumblr(post: Post) -> Optional[int]:
			""" Publishes a snapshot recording on Tumblr. The video recording is added to the main post along
			with a message whose content is generated using the remaining arguments. Unlike the Twitter and
			Mastodon counterparts, posting text-to-speech files is not supported. It's also not possible to
			mark a post as sensitive or add any alt text. This function also adds tags to the post. """

			log.info('Publishing on Tumblr.')

			status_id = None

			try:
				max_title_length = max(config.tumblr_max_status_length - len('<br>') - len(post.html_body), 0)
				text = post.title[:max_title_length] + '<br>' + post.html_body

				for i in range(config.tumblr_max_retries):

					# The official Tumblr library doesn't have any package-specific exceptions so
					# we'll catch all of them to be safe.
					# See: https://www.tumblr.com/docs/en/api/v2#post--create-a-new-blog-post-legacy
					response = tumblr_api.create_video(tumblr_blog_name, tags=post.tags, caption=text, data=str(post.recording.UploadFilePath))
					status_id = response.get('id')

					if status_id is not None:
						log.info(f'Posted the recording status #{status_id} using {len(text)} characters.')
						break
					else:
						status_code = response['meta']['status']

						if status_code in [408, 502, 503, 504]:
							log.warning(f'Retrying the status post operation ({i+1} of {config.tumblr_max_retries}) after failing with the error: {repr(response)}')
							sleep(config.tumblr_retry_wait)
							continue
						else:
							raise Exception(f'Tumblr Response: {response}')
				else:
					raise Exception(f'Tumblr Response: {response}')

			except Exception as error:
				log.error(f'Failed to post the recording status with the error: {repr(error)}')

			return status_id

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
												SELECT R.SnapshotId, MAX(R.CreationTime) AS LastCreationTime
												FROM Recording R
												WHERE NOT R.IsProcessed
												GROUP BY R.SnapshotId
											) LCR ON S.Id = LCR.SnapshotId AND R.CreationTime = LCR.LastCreationTime
											WHERE
												(S.State = :approved_state OR (S.State = :recorded_state AND NOT :require_approval))
												AND NOT R.IsProcessed
											ORDER BY S.Priority DESC, R.CreationTime
											LIMIT 1;
											''',
											{'approved_state': Snapshot.APPROVED, 'recorded_state': Snapshot.RECORDED,
											 'require_approval': config.require_approval})

						row = cursor.fetchone()
						if row is not None:
							# Avoid naming conflicts with each table's primary key.
							del row['Id']
							snapshot = Snapshot(**row, Id=row['SnapshotId'])
							recording = Recording(**row, Id=row['RecordingId'])

							assert snapshot.IsSensitive is not None, 'The IsSensitive column is not being computed properly.'
							config.apply_snapshot_options(snapshot)
						else:
							log.info('Ran out of recordings to publish.')
							break

					except sqlite3.Error as error:
						log.error(f'Failed to select the next snapshot recording with the error: {repr(error)}')
						sleep(config.database_error_wait)
						continue

					log.info(f'[{recording_index+1} of {num_recordings}] Publishing recording #{recording.Id} of snapshot #{snapshot.Id} {snapshot} (approved = {snapshot.State == Snapshot.APPROVED}).')

					url = snapshot.Url
					wayback_url = snapshot.WaybackUrl

					# Use the parent snapshot's URL if the media file is a YouTube video since these have extremely long URLs.
					if snapshot.IsMedia and snapshot.MediaExtension == 'mp4':
						try:
							cursor = db.execute('''
												SELECT S.*, SI.IsSensitive
						   						FROM Snapshot S
						   						INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
						   						WHERE S.Id = :parent_id;
						   						''',
												{'parent_id': snapshot.ParentId})

							row = cursor.fetchone()
							if row is not None:
								parent_snapshot = Snapshot(**row)

								if is_url_from_domain(parent_snapshot.Url, 'youtube.com'):
									log.info(f'Using the YouTube URL from the parent snapshot #{parent_snapshot.Id} {parent_snapshot}.')
									url = parent_snapshot.Url
									wayback_url = parent_snapshot.WaybackUrl

								assert parent_snapshot.IsSensitive is not None, 'The IsSensitive column is not being computed properly.'
							else:
								log.warning(f'Could not find the parent of snapshot {snapshot}.')

						except sqlite3.Error as error:
							log.warning(f'Could not find the parent of snapshot {snapshot} with the error: {repr(error)}')

					media_emoji = '\N{DVD}' if snapshot.IsMedia else ('\N{Jigsaw Puzzle Piece}' if snapshot.PageUsesPlugins else None)
					sensitive_emoji = '\N{No One Under Eighteen Symbol}' if snapshot.IsSensitive else None
					audio_emoji = '\N{Speaker With Three Sound Waves}' if recording.HasAudio else None
					emojis = ' '.join(filter(None, [media_emoji, sensitive_emoji, audio_emoji, *snapshot.Emojis]))

					body = '\n'.join(filter(None, [snapshot.DisplayMetadata, snapshot.ShortDate, wayback_url, emojis]))

					# We have to format the link ourselves since the Tumblr API treats the post as HTML by default.
					snapshot_type = 'media file' if snapshot.IsMedia else 'web page'
					html_wayback_url = f'<a href="{wayback_url}">Archived {snapshot_type.title()}</a>'
					html_body = '<br>'.join(filter(None, [snapshot.DisplayMetadata, snapshot.ShortDate, html_wayback_url, emojis]))

					# How the date is formatted depends on the current locale.
					long_date = snapshot.OldestDatetime.strftime('%B %Y')
					alt_text = f'The {snapshot_type} "{url}" as seen on {long_date} via the Wayback Machine.'

					tts_language = f'Text-to-Speech ({snapshot.LanguageName})' if snapshot.LanguageName is not None else 'Text-to-Speech'
					tts_body = '\n'.join(filter(None, [snapshot.ShortDate, tts_language]))
					tts_alt_text = f'An audio recording of the {snapshot_type} "{url}" as seen on {long_date} via the Wayback Machine. Generated using text-to-speech.'

					extract = tld_extract(url)
					year = str(snapshot.OldestDatetime.year)
					tags = [extract.domain, year, *snapshot.Tags]

					post = Post(
						recording=recording,

						title=snapshot.DisplayTitle,
						body=body,
						html_body=html_body,
						alt_text=alt_text,

						tts_body=tts_body,
						tts_alt_text=tts_alt_text,

						sensitive=snapshot.IsSensitive,
						emojis=emojis,
						tags=tags,
					)

					twitter_media_id, twitter_status_id = publish_to_twitter(post) if config.enable_twitter else (None, None)
					mastodon_media_id, mastodon_status_id = publish_to_mastodon(post) if config.enable_mastodon else (None, None)
					tumblr_status_id = publish_to_tumblr(post) if config.enable_tumblr else None

					if config.delete_files_after_upload:
						delete_file(recording.UploadFilePath)

						if recording.TextToSpeechFilePath is not None:
							delete_file(recording.TextToSpeechFilePath)

					try:
						db.execute('UPDATE Snapshot SET State = :state WHERE Id = :id;', {'state': Snapshot.PUBLISHED, 'id': snapshot.Id})

						db.execute(	'''
									UPDATE Recording
									SET
										IsProcessed = TRUE, PublishTime = CURRENT_TIMESTAMP,
										TwitterMediaId = :twitter_media_id, TwitterStatusId = :twitter_status_id,
										MastodonMediaId = :mastodon_media_id, MastodonStatusId = :mastodon_status_id,
										TumblrStatusId = :tumblr_status_id
									WHERE Id = :id;
									''',
									{'twitter_media_id': twitter_media_id, 'twitter_status_id': twitter_status_id,
									 'mastodon_media_id': mastodon_media_id, 'mastodon_status_id': mastodon_status_id,
									 'tumblr_status_id': tumblr_status_id,
									 'id': recording.Id})

						# Mark any earlier recordings as processed so the same snapshot isn't published multiple times in
						# cases where there is more than one video file. See the LastCreationTime part in the main query.
						db.execute('UPDATE Recording SET IsProcessed = TRUE WHERE SnapshotId = :snapshot_id;', {'snapshot_id': snapshot.Id})

						if snapshot.PriorityName == 'Publish':
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