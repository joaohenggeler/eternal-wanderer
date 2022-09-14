#!/usr/bin/env python3

import binascii
import os
import re
import sqlite3
import string
import sys
import time
import unicodedata
from argparse import ArgumentParser
from base64 import b64decode
from collections import Counter
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

from apscheduler.schedulers import SchedulerNotRunningError # type: ignore
from apscheduler.schedulers.blocking import BlockingScheduler # type: ignore
from selenium.common.exceptions import SessionNotCreatedException, StaleElementReferenceException, WebDriverException # type: ignore
from waybackpy.exceptions import BlockedSiteError, NoCDXRecordFound

from common import Browser, CommonConfig, Database, Snapshot, compose_wayback_machine_snapshot_url, container_to_lowercase, extract_standalone_media_extension_from_url, find_best_wayback_machine_snapshot, find_extra_wayback_machine_snapshot_info, global_rate_limiter, is_url_from_domain, is_wayback_machine_available, parse_wayback_machine_snapshot_url, setup_logger, was_exit_command_entered

class ScoutConfig(CommonConfig):
	""" The configuration that applies to the scout script. """

	# From the config file.
	scheduler: Dict[str, Union[int, str]]
	num_snapshots_per_scheduled_batch: int

	extension_filter: List[str]
	user_script_filter: List[str]
	
	initial_snapshots: List[Dict[str, str]]
	
	ranking_offset: Optional[int]
	min_year: Optional[int]
	max_year: Optional[int]
	max_depth: Optional[int]
	max_required_depth: Optional[int]

	excluded_url_tags: List[str]
	
	store_all_words_and_tags: bool
	
	word_points: Dict[str, int]
	tag_points: Dict[str, int]
	standalone_media_points: int
	
	sensitive_words: Dict[str, bool] # Different from the config data type.
	
	detect_page_language: bool
	language_model_path: str
	tokenize_japanese_text: bool

	def __init__(self):
		super().__init__()
		self.load_subconfig('scout')

		self.scheduler = container_to_lowercase(self.scheduler)

		self.excluded_url_tags = container_to_lowercase(self.excluded_url_tags)
		self.word_points = container_to_lowercase(self.word_points)
		self.tag_points = container_to_lowercase(self.tag_points)
	
		decoded_sensitive_words = {}
		for word in self.sensitive_words:
			try:
				if word.startswith('b64:'):
					word = b64decode(word.removeprefix('b64:')).decode()
				
				decoded_sensitive_words[word.lower()] = True
			except binascii.Error as error:
				log.error(f'Failed to decode the sensitive word "{word}" with the error: {repr(error)}')
		
		self.sensitive_words = decoded_sensitive_words

		self.language_model_path = os.path.abspath(self.language_model_path)

if __name__ == '__main__':

	parser = ArgumentParser(description='Traverses web pages archived by the Wayback Machine (snapshots) and collects metadata from their content and from the CDX API. The scout script prioritizes pages that were manually added by the user through the configuration file as well as pages whose parent snapshot contains specific words and plugin media.')
	parser.add_argument('max_iterations', nargs='?', type=int, default=-1, help='How many snapshots to scout. Omit or set to %(default)s to run forever on a set schedule.')
	parser.add_argument('-initial', action='store_true', help='Enqueue every initial snapshot specified in the configuration file before scouting.')
	args = parser.parse_args()

	config = ScoutConfig()
	log = setup_logger('scout')

	log.info('Initializing the scout.')

	if config.detect_page_language:
		import fasttext # type: ignore
		log.info(f'Loading the FastText model "{config.language_model_path}".')
		language_model = fasttext.load_model(config.language_model_path)

	if config.tokenize_japanese_text:
		import fugashi # type: ignore
		log.info('Initializing the Japanese text tagger.')
		japanese_tagger = fugashi.Tagger()
		
		for info in japanese_tagger.dictionary_info:
			log.info(f'Found the Japanese dictionary: {info}')

	def find_child_snapshot(parent_snapshot: Optional[Snapshot], url: str, timestamp: str) -> Optional[dict]:
		""" Retrieves a snapshot's metadata from the Wayback Machine. """

		result = None

		if parent_snapshot is None:
			parent_id = None
			depth = 0
		else:
			parent_id = parent_snapshot.Id
			depth = parent_snapshot.Depth + 1

		while True:

			retry = False

			try:
				log.debug(f'Locating the snapshot at "{url}" near {timestamp}.')
				best_snapshot, is_standalone_media, media_extension = find_best_wayback_machine_snapshot(timestamp=timestamp, url=url)
				last_modified_time = find_extra_wayback_machine_snapshot_info(best_snapshot.archive_url)

				state = Snapshot.SCOUTED if is_standalone_media else Snapshot.QUEUED

				result = {'parent_id': parent_id, 'depth': depth, 'state': state, 'is_excluded': False,
						  'is_standalone_media': is_standalone_media, 'media_extension': media_extension,
						  'url': best_snapshot.original, 'timestamp': best_snapshot.timestamp,
						  'last_modified_time': last_modified_time, 'url_key': best_snapshot.urlkey,
						  'digest': best_snapshot.digest}
			except NoCDXRecordFound:
				pass
			
			except BlockedSiteError:
				log.warning(f'The snapshot at "{url}" near {timestamp} has been excluded from the Wayback Machine.')
				result = {'parent_id': parent_id, 'depth': depth, 'state': Snapshot.QUEUED, 'is_excluded': True,
						  'is_standalone_media': None, 'media_extension': None, 'url': url, 'timestamp': timestamp,
						  'last_modified_time': None, 'url_key': None, 'digest': None}
			
			except Exception as error:
				log.error(f'Failed to find the snapshot at "{url}" near {timestamp} with the error: {repr(error)}')

				while not is_wayback_machine_available():
					retry = True
					log.warning(f'Waiting {config.unavailable_wayback_machine_wait} seconds for the Wayback Machine to become available again.')
					time.sleep(config.unavailable_wayback_machine_wait)
			
			finally:
				if retry:
					continue
				else:
					break

		return result

	scheduler = BlockingScheduler()

	def scout_snapshots(num_snapshots: int) -> None:
		""" Scouts a given number of snapshots in a single batch. """
		
		log.info(f'Scouting {num_snapshots} snapshots.')

		# We don't want any extensions or user scripts that change the HTML document, but we do need to use the Greasemonkey extension with a user script
		# that disables the alert(), confirm(), and prompt() JavaScript functions. This prevents the UnexpectedAlertPresentException, which would otherwise
		# make it impossible to scrape pages that use those functions.
		# E.g. https://web.archive.org/web/19990222174035if_/http://www.geocities.com/Heartland/Plains/1036/arranco.html
		with Database() as db, Browser(headless=True, use_extensions=True, extension_filter=config.extension_filter, user_script_filter=config.user_script_filter) as (browser, driver):

			if args.initial:
				try:
					log.info('Inserting the initial Wayback Machine snapshots.')

					initial_snapshot_list = []
					for page in config.initial_snapshots:
						
						url = page['url']
						timestamp = page['timestamp']
						
						log.info(f'Inserting the initial snapshot at "{url}" near {timestamp}.')
						initial_snapshot = find_child_snapshot(None, url, timestamp)

						if initial_snapshot is not None:
							initial_snapshot_list.append(initial_snapshot)
						else:
							log.warning(f'Could not find the initial snapshot at "{url}" near {timestamp}.')

					db.executemany(	'''
									INSERT OR IGNORE INTO Snapshot (State, Depth, IsExcluded, IsStandaloneMedia, MediaExtension, Url, Timestamp, LastModifiedTime, UrlKey, Digest)
									VALUES (:state, :depth, :is_excluded, :is_standalone_media, :media_extension, :url, :timestamp, :last_modified_time, :url_key, :digest);
									''', initial_snapshot_list)

					db.commit()

				except sqlite3.Error as error:
					log.error(f'Failed to insert the initial snapshots with the error: {repr(error)}')
					db.rollback()
					raise

			else:
				log.info('Skipping the initial snapshots at the user\'s request.')

			try:
				log.info('Updating the word and tag attributes.')

				# Removing words that are no longer associated with any snapshot is handy when we change
				# certain options (e.g. toggling Japanese text tokenization from one execution to another).
				# This step is skipped for any words that were added via the configuration file.
				cursor = db.execute('''
									DELETE FROM Word
									WHERE Id IN
									(
										SELECT W.Id
										FROM Word W
										LEFT JOIN SnapshotWord SW ON W.Id = SW.WordId
										WHERE SW.WordId IS NULL AND NOT (W.Points <> 0 OR W.IsSensitive)
									);
									''')

				log.info(f'Deleted {cursor.rowcount} words that were no longer associated with a snapshot.')

				db.execute('UPDATE Word SET Points = 0, IsSensitive = FALSE;')

				word_and_tag_points = []
				
				for word, points in config.word_points.items():
					word_and_tag_points.append({'word': word, 'is_tag': False, 'points': points})

				for tag, points in config.tag_points.items():
					word_and_tag_points.append({'word': tag, 'is_tag': True, 'points': points})

				# Do an upsert instead of replacing to avoid messing with the primary keys of previously inserted words.
				db.executemany(	'''
								INSERT INTO Word (Word, IsTag, Points)
								VALUES (:word, :is_tag, :points)
								ON CONFLICT (Word, IsTag)
								DO UPDATE SET Points = :points;
								''', word_and_tag_points)
				
				sensitive_words = []
				
				for word in config.sensitive_words:
					sensitive_words.append({'word': word, 'is_tag': False, 'is_sensitive': True})

				db.executemany(	'''
								INSERT INTO Word (Word, IsTag, IsSensitive)
								VALUES (:word, :is_tag, :is_sensitive)
								ON CONFLICT (Word, IsTag)
								DO UPDATE SET IsSensitive = :is_sensitive;
								''', sensitive_words)

				db.execute('INSERT OR REPLACE INTO Config (Name, Value) VALUES (:name, :value);', {'name': 'standalone_media_points', 'value': config.standalone_media_points})

				db.commit()

			except sqlite3.Error as error:
				log.error(f'Failed to update the word and tag attributes with the error: {repr(error)}')
				db.rollback()
				raise

			def invalidate_snapshot(snapshot: Snapshot) -> None:
				""" Invalidates a snapshot that couldn't be scouted correctly due to a WebDriver error. """

				try:
					db.execute('UPDATE Snapshot SET State = :invalid_state WHERE Id = :id;', {'invalid_state': Snapshot.INVALID, 'id': snapshot.Id})
					db.commit()
				except sqlite3.Error as error:
					log.error(f'Failed to invalidate the snapshot {snapshot} with the error: {repr(error)}')
					db.rollback()
					time.sleep(config.database_error_wait)

			def check_snapshot_redirection(snapshot: Snapshot) -> bool:
				""" Checks if a snapshot was redirected. If so, the snapshot is invalidated and no information is extracted from its page.
				While the snapshot is skipped, the page we were redirected to will be added to the queue. The one exception are pages from
				the Wayback Machine that aren't snapshots. """

				redirected, url, timestamp = browser.was_wayback_url_redirected(snapshot.WaybackUrl)

				if redirected:
					try:
						log.warning(f'Skipping the snapshot since it was redirected to "{url}".')
						db.execute('UPDATE Snapshot SET State = :invalid_state WHERE Id = :id;', {'invalid_state': Snapshot.INVALID, 'id': snapshot.Id})
					
						# See example #4 in was_wayback_url_redirected().
						if not is_url_from_domain(url, 'web.archive.org'):

							child_snapshot = find_child_snapshot(snapshot, url, timestamp)

							if child_snapshot is not None:
								db.execute(	'''
											INSERT OR IGNORE INTO Snapshot (ParentId, Depth, State, IsExcluded, IsStandaloneMedia, MediaExtension, Url, Timestamp, LastModifiedTime, UrlKey, Digest)
											VALUES (:parent_id, :depth, :state, :is_excluded, :is_standalone_media, :media_extension, :url, :timestamp, :last_modified_time, :url_key, :digest);
											''', child_snapshot)
							else:
								log.warning(f'Could not find the redirected snapshot at "{url}" near {timestamp}.')

						db.commit()

					except sqlite3.Error as error:
						log.error(f'Failed to update the redirected snapshot with the error: {repr(error)}')
						db.rollback()
						time.sleep(config.database_error_wait)

				return redirected

			try:
				# This is an oversimplified regular expression, but it works for our case since we are
				# going to ask the CDX API what the real URLs are. The purpose of this pattern is to
				# minimize the amount of requests to this endpoint.
				URL_REGEX = re.compile(r'https?://.+', re.IGNORECASE)
				
				page_text_delimiters = []
				for i in range(sys.maxunicode + 1):
					try:
						char = chr(i)
						is_letter = unicodedata.category(char).startswith('L')
						if not is_letter:
							page_text_delimiters.append(char)
					except ValueError:
						pass
				
				log.debug(f'Found {len(page_text_delimiters)} page text delimiters out of {sys.maxunicode} Unicode code points.')
				PAGE_TEXT_DELIMITER_REGEX = re.compile('|'.join(re.escape(delimiter) for delimiter in page_text_delimiters))

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
											SELECT 	S.*,
													CAST(MIN(SUBSTR(S.Timestamp, 1, 4), IFNULL(SUBSTR(S.LastModifiedTime, 1, 4), '9999')) AS INTEGER) AS OldestYear,
													RANK_SNAPSHOT_BY_POINTS(PSI.Points, :ranking_offset) AS Rank,
													PSI.Points AS ParentPoints
											FROM Snapshot S
											LEFT JOIN Snapshot PS ON S.ParentId = PS.Id
											LEFT JOIN SnapshotInfo PSI ON PS.Id = PSI.Id
											WHERE
												S.State = :queued_state
												AND NOT S.IsStandaloneMedia
												AND NOT S.IsExcluded
												AND (:min_year IS NULL OR OldestYear >= :min_year)
												AND (:max_year IS NULL OR OldestYear <= :max_year)
												AND (:max_depth IS NULL OR S.Depth <= :max_depth)
												AND IS_URL_KEY_ALLOWED(S.UrlKey)
											ORDER BY
												S.Priority DESC,
												CASE WHEN S.Depth <= :max_required_depth THEN S.Depth ELSE (SELECT MAX(Depth) + 1 FROM Snapshot) END,
												Rank DESC
											LIMIT 1;
											''', {'ranking_offset': config.ranking_offset, 'queued_state': Snapshot.QUEUED, 'min_year': config.min_year,
												  'max_year': config.max_year, 'max_depth': config.max_depth, 'max_required_depth': config.max_required_depth})
						
						row = cursor.fetchone()
						if row is not None:
							snapshot = Snapshot(**dict(row))
							browser.set_fallback_encoding_for_snapshot(snapshot)
							parent_points = row['ParentPoints']
						else:
							log.info('Ran out of snapshots to scout.')
							break

					except sqlite3.Error as error:
						log.error(f'Failed to select the next snapshot with the error: {repr(error)}')
						time.sleep(config.database_error_wait)
						continue
					
					# Due to the way snapshots are labelled, it's possible that a regular page
					# will be marked as standalone media and vice versa. Let's look at both cases:
					# - If it's actually a regular page, then it will be skipped since we don't
					# scout standalone media.
					# - If it's actually standalone media, then the browser will download the file
					# and the current URL won't change. This can be caught below since we always
					# set the current URL to a blank page before navigating to the Wayback Machine.

					try:
						log.info(f'[{snapshot_index+1} of {num_snapshots}] Scouting snapshot #{snapshot.Id} {snapshot} located at a depth of {snapshot.Depth} pages and whose parent has {parent_points} points.')
						browser.go_to_wayback_url(snapshot.WaybackUrl)
					except SessionNotCreatedException as error:
						log.warning(f'Terminated the WebDriver session abruptly with the error: {repr(error)}')
						break
					except WebDriverException as error:
						log.error(f'Failed to load the snapshot with the error: {repr(error)}')
						invalidate_snapshot(snapshot)
						continue

					# Skip downloads, i.e., regular pages that were mislabeled as standalone media.
					# When this happens, we have to wait for the WebDriver to time out.
					#
					# E.g. https://web.archive.org/web/20060321063750if_/http://www.thekidfrombrooklyn.com/movies/PoundCake_02_06.wmv
					# This video file was stored in the Wayback Machine with the text/plain media type.
					if driver.current_url == Browser.BLANK_URL:
						try:
							log.warning(f'Skipping the snapshot since it was mislabeled as standalone media.')
							
							media_extension = extract_standalone_media_extension_from_url(snapshot.Url)
							db.execute(	'UPDATE Snapshot SET State = :scouted_state, IsStandaloneMedia = :is_standalone_media, MediaExtension = :media_extension WHERE Id = :id;',
										{'scouted_state': Snapshot.SCOUTED, 'is_standalone_media': True, 'media_extension': media_extension, 'id': snapshot.Id})
							
							if snapshot.Priority == Snapshot.SCOUT_PRIORITY:
								db.execute('UPDATE Snapshot SET Priority = :no_priority WHERE Id = :id;', {'no_priority': Snapshot.NO_PRIORITY, 'id': snapshot.Id})
							
							db.commit()
						
						except sqlite3.Error as error:
							log.error(f'Failed to update the mislabeled snapshot with the error: {repr(error)}')
							db.rollback()
							time.sleep(config.database_error_wait)
						finally:
							continue

					raw_frame_url_list = []
					word_and_tag_counter: Counter = Counter()

					url_list: List[Tuple[str, Optional[str]]] = []
					
					try:
						# Checking for redirects should only be done in this block since we're going to navigate to
						# each individual frame below when counting tags. We'll do this before and after counting
						# words because the snapshot could have been redirected during this process. If it wasn't
						# redirected here, we'll assume it won't happen when visiting each frame's page for the tags.
						# For that case, we'd need to check the redirection status for each frame, meaning we'd have
						# to decide whether we wanted to skip the entire snapshot just because one frame was redirected.
						if check_snapshot_redirection(snapshot):
							continue

						# Analyze the page and its frames by using the Wayback Machine iframe modifier. This makes it
						# so the tags use absolute URLs instead of relative ones, making it easier to collect them.
						#
						# This modifier has one disadvantage, which is that the tags inserted by the Wayback Machine
						# would also be counted by our script, even though they're not part of the original page. As
						# such, we'll count them below using the identical modifier instead.
						#
						# The same frame may show up more than once, meaning the script will count duplicate words
						# and tags. This is fine since that's what the user sees.
						#
						# We'll avoid counting words (and later tags) from 404 Wayback Machine pages by skipping any
						# missing snapshots. Keeping the Wayback Machine URL format is also necessary when counting
						# tags later on in the script.
						frame_text_list = []
						for i, frame_url in enumerate(browser.traverse_frames(format_wayback_urls=True, skip_missing=True)):

							# Retrieve links from all href attributes.
							# This is useful for snapshots that use tags other than <a> to link to other pages.
							# E.g. https://web.archive.org/web/19961220170231if_/http://www.geocities.com/NorthPole/
							element_list = driver.find_elements_by_xpath(r'//*[@href]')
							for element in element_list:

								try:
									tag_name = element.tag_name
									url = element.get_attribute('href')
								except StaleElementReferenceException:
									log.warning('Skipping stale element.')
									continue

								if tag_name in config.excluded_url_tags:
									continue

								if url:

									wayback_timestamp = None

									# Links to Wayback Machine snapshots are allowed, but instead of storing that URL,
									# we'll extract the archived snapshot's URL.
									# E.g. https://web.archive.org/web/20110519051847if_/http://www.bloggerheads.com/archives/2005/01/the_evolution_o/
									# Which links to https://web.archive.org/web/20110519051847if_/http://web.archive.org/web/20031105100709/http://www.turboforce3d.com/annoying/index.htm
									wayback_parts = parse_wayback_machine_snapshot_url(url)
									if wayback_parts is not None:
										url = wayback_parts.Url
										wayback_timestamp = wayback_parts.Timestamp

									# Checking for valid URLs using netloc only makes sense if it was properly decoded.
									# E.g. "http%3A//www.geocities.com/Hollywood/Hills/5988/main.html" would result in
									# an empty netloc instead of "www.geocities.com".
									url = unquote(url)
									parts = urlparse(url)
									is_valid = parts.scheme in ['http', 'https'] and parts.netloc != ''
									is_archive_org = is_url_from_domain(parts, 'archive.org')
		
									if is_valid and not is_archive_org:

										# Handle URLs with non-HTTP schemes (FTP, Gopher, etc). In these cases, the
										# snapshot URL uses a different format.
										# E.g. https://web.archive.org/web/19970617032419if_/http://www.acer.net/document/InternetViaMail/elmgophe.htm
										# Which links to http://19970617032419/gopher://cwis.usc.edu/
										# Where the Gopher URL "gopher://cwis.usc.edu/" is converted to "http://cwis.usc.edu/".
										if parts.netloc == snapshot.Timestamp:
											# We can't remove the port directly so we have to change the entire netloc.
											new_parts = urlparse(parts.path.lstrip('/'))
											new_parts = new_parts._replace(scheme='http', netloc=new_parts.hostname or '')
											url = urlunparse(new_parts)
											parts = urlparse(url)

										url_list.append((url, wayback_timestamp))

										# One advantage of using the Wayback Machine timestamp is that
										# any extra URLs that appear in the snapshot's URL will also
										# use the correct timestamp.

										# Retrieve any additional URLs that show up in the query string.
										# We'll allow all Internet Archive URLs here since we know they
										# weren't autogenerated. Note that the (oversimplified) URL regex
										# already checks if it's valid for our purposes.
										query_dict = parse_qs(parts.query)
										for key, value_list in query_dict.items():
											for value in value_list:	
												
												extra_url_list = URL_REGEX.findall(value)
												for extra_urL in extra_url_list:
													url_list.append((extra_urL, wayback_timestamp))

										# For cases that have a query string without any key-value pairs.
										# E.g. "http://example.com/?http://other.com".
										if parts.query and not query_dict:
											
											extra_url_list = URL_REGEX.findall(parts.query)
											for extra_urL in extra_url_list:
												url_list.append((extra_urL, wayback_timestamp))
							
							# Convert the URL to the unmodified page archive.
							wayback_parts = parse_wayback_machine_snapshot_url(frame_url)
							if wayback_parts is not None:
								wayback_parts = wayback_parts._replace(Modifier=Snapshot.IDENTICAL_MODIFIER)
								raw_frame_url = compose_wayback_machine_snapshot_url(parts=wayback_parts)
								raw_frame_url_list.append(raw_frame_url)
							else:
								assert False, f'The frame URL "{frame_url}" was not formatted properly.'

							# Retrieve every word on the frame.
							frame_text = driver.execute_script('return document.documentElement.innerText;')
							frame_text_list.append(frame_text)
							split_text = PAGE_TEXT_DELIMITER_REGEX.split(frame_text.lower())
							
							for text in filter(None, split_text):
								
								# Tokenizing Japanese text works best if Firefox can autodetect the correct character
								# encoding for legacy pages that don't specify one. We'll do this by setting the
								# "intl.charset.detector" preference to "ja_parallel_state_machine, which tells the
								# browser to use a heuristic for these type of pages. Otherwise, we'd be storing
								# garbage in the database. This also applies to retrieving the page's title.
								#
								# For other languages (but also some Japanese pages), we'll tell Firefox to use an
								# encoding that was autodetected by the Wayback Machine as a fallback. This is done
								# in practice by setting the "intl.charset.fallback.override" preference to this
								# guessed encoding. See set_fallback_encoding_for_snapshot().
								#
								# These two preferences should ensure that the content in most pages is displayed
								# and retrieved correctly. For specific edge cases, we'll allow the user to set the
								# encoding via each snapshot's options. Note also that using the correct encoding
								# affects the page language detection and the text-to-speech voice selection.
								#
								# See:
								# - https://www-archive.mozilla.org/projects/intl/chardet.html
								# - https://udn.realityripple.com/docs/Web/Guide/Localizations_and_character_encodings
								# - https://groups.google.com/g/mozilla.dev.platform/c/TCiODi3Fea4
								#
								# E.g.
								# - Requires detector: https://web.archive.org/web/19990424053506if_/http://geochat00.geocities.co.jp/
								# - Does not require detector: https://web.archive.org/web/19980123230614if_/http://www.geocities.co.jp:80/Milkyway/
								# - Requires the fallback encoding: https://web.archive.org/web/19991011153317if_/http://www.geocities.com/Athens/Delphi/1240/midigr.htm
								if config.tokenize_japanese_text:
									
									# In order to avoid tokenizing non-Japanese text, we would need to determine
									# if there's any Japanese text in a string. There were some solutions that
									# used regex and Unicode blocks, but for the sake of consistency we'll use
									# the fugashi library to do this by checking if a word is unknown.
									word_list = [word.surface for word in japanese_tagger(text) if not word.is_unk]
									
									# If we weren't able to split the text into two or more words, just store the
									# entire string. Checking for one word is probably redundant, but let's do it
									# anyways just to be sure that nothing was removed from the text.
									if len(word_list) < 2:
										word_list = [text]
								else:
									word_list = [text]

								for word in word_list:
									if config.store_all_words_and_tags or word in config.word_points:
										word_and_tag_counter[(word, False)] += 1
					
						if check_snapshot_redirection(snapshot):
							continue

						if config.detect_page_language:
							# The fastText library requires a single sentence.
							page_text = '. '.join(frame_text_list).replace('\n', ' ')
							prediction = language_model.predict(page_text)
							# E.g. (('__label__en',), array([0.97309864])) -> "en"
							page_language = prediction[0][0].removeprefix('__label__')
							confidence = prediction[1][0] * 100
							log.debug(f'Detected the page language "{page_language}" with {confidence:.2f}% confidence.')
						else:
							page_language = None

					except SessionNotCreatedException as error:
						log.warning(f'Terminated the WebDriver session abruptly with the error: {repr(error)}')
						break
					except WebDriverException as error:
						log.error(f'Failed to retrieve the snapshot\'s page elements with the error: {repr(error)}')
						invalidate_snapshot(snapshot)
						continue
			
					log.debug(f'Found {len(raw_frame_url_list)} valid frames.')

					# Remove any duplicates to minimize the amount of requests to the CDX API.
					url_list = list(dict.fromkeys(url_list))

					try:
						# Analyze the page and its frames by using the Wayback Machine identical modifier.
						# The main advantage of this modifier is that it excludes the tags inserted by the
						# Wayback Machine.

						# Keep the previous plugin status for cases where we were only able to determine it
						# while recording the snapshot (see the example below).
						page_title = driver.title
						page_uses_plugins = bool(snapshot.PageUsesPlugins)
		
						for raw_frame_url in raw_frame_url_list:

							# Redirects are expected here since the frame's timestamp is inherited from the
							# root page's snapshot. See traverse_frames() for more details.
							browser.go_to_wayback_url(raw_frame_url)

							# Retrieve tag word on the frame.
							if config.store_all_words_and_tags:
								element_list = driver.find_elements_by_xpath(r'//*')
								for element in element_list:
									tag = element.tag_name
									word_and_tag_counter[(tag, True)] += 1
							else:
								for tag in config.tag_points:
									tag_list = driver.find_elements_by_tag_name(tag)
									if len(tag_list) > 0:
										word_and_tag_counter[(tag, True)] += len(tag_list)

							# This method for checking if a page uses plugins is pretty good, but it can miss a few pages that embed plugin media
							# in an awkward way. E.g. https://web.archive.org/web/19961221002554if_/http://www.geocities.com:80/Hollywood/Hills/5988/
							# Which does this: <input value="http://www.geocities.com/Hollywood/Hills/5988/random.mid" onfocus="this.focus();this.select();">
							# We're able to catch these edge cases in the recorder script.
							page_uses_plugins = page_uses_plugins or any(driver.find_elements_by_tag_name(tag) for tag in ['object', 'embed', 'applet', 'app', 'bgsound'])

						browser.close_all_windows()

					except SessionNotCreatedException as error:
						log.warning(f'Terminated the WebDriver session abruptly with the error: {repr(error)}')
						break
					except WebDriverException as error:
						# E.g. https://web.archive.org/web/19990117002229if_/http://www.geocities.com:80/cgi-bin/homestead/mbrlookup
						log.error(f'Failed to analyze the snapshot\'s page with the error: {repr(error)}')
						invalidate_snapshot(snapshot)
						continue

					log.debug(f'Words and tags found: {word_and_tag_counter}')

					child_snapshot_list = []
					for url, wayback_timestamp in url_list:

						timestamp = wayback_timestamp or snapshot.Timestamp
						child_snapshot = find_child_snapshot(snapshot, url, timestamp)
					
						if child_snapshot is not None:
							child_snapshot_list.append(child_snapshot)

					log.info(f'Found {len(child_snapshot_list)} valid snapshots out of {len(url_list)} links.')
			
					try:
						db.executemany(	'''
										INSERT OR IGNORE INTO Snapshot (ParentId, Depth, State, IsExcluded, IsStandaloneMedia, MediaExtension, Url, Timestamp, LastModifiedTime, UrlKey, Digest)
										VALUES (:parent_id, :depth, :state, :is_excluded, :is_standalone_media, :media_extension, :url, :timestamp, :last_modified_time, :url_key, :digest);
										''', child_snapshot_list)

						topology = [{'parent_id': child['parent_id'], 'url': child['url'], 'timestamp': child['timestamp']} for child in child_snapshot_list]
						db.executemany(	'''
										INSERT OR IGNORE INTO Topology (ParentId, ChildId)
										VALUES (:parent_id, (SELECT Id FROM Snapshot WHERE Url = :url AND Timestamp = :timestamp))
										''', topology)

						if config.store_all_words_and_tags:
							word_and_tag_values = [{'word': word, 'is_tag': is_tag} for word, is_tag in word_and_tag_counter]
							db.executemany('INSERT OR IGNORE INTO Word (Word, IsTag) VALUES (:word, :is_tag);', word_and_tag_values)

						db.execute('DELETE FROM SnapshotWord WHERE SnapshotId = :snapshot_id;', {'snapshot_id': snapshot.Id})

						word_and_tag_count = [{'snapshot_id': snapshot.Id, 'word': word, 'is_tag': is_tag, 'count': count} for (word, is_tag), count in word_and_tag_counter.items()]
						db.executemany(	'''
										INSERT INTO SnapshotWord (SnapshotId, WordId, Count)
										VALUES (:snapshot_id, (SELECT Id FROM Word WHERE Word = :word AND IsTag = :is_tag), :count)
										''', word_and_tag_count)

						db.execute( '''
									UPDATE Snapshot
									SET State = :scouted_state, PageLanguage = :page_language, PageTitle = :page_title, PageUsesPlugins = :page_uses_plugins
									WHERE Id = :id;
									''', {'scouted_state': Snapshot.SCOUTED, 'page_language': page_language, 'page_title': page_title, 'page_uses_plugins': page_uses_plugins,'id': snapshot.Id})

						if snapshot.Priority == Snapshot.SCOUT_PRIORITY:
							db.execute('UPDATE Snapshot SET Priority = :no_priority WHERE Id = :id;', {'no_priority': Snapshot.NO_PRIORITY, 'id': snapshot.Id})
						
						db.commit()

					except sqlite3.Error as error:
						log.error(f'Failed to insert the next snapshots with the error: {repr(error)}')
						db.rollback()
						time.sleep(config.database_error_wait)
						continue

			except KeyboardInterrupt:
				log.warning('Detected a keyboard interrupt when these should not be used to terminate the scout due to a bug when using both Windows and the Firefox WebDriver.')

			log.info(f'Finished scouting {num_snapshots} snapshots.')

	if args.max_iterations >= 0:
		scout_snapshots(args.max_iterations)
	else:
		log.info(f'Running the scout with the schedule: {config.scheduler}')
		scheduler.add_job(scout_snapshots, args=[config.num_snapshots_per_scheduled_batch], trigger='cron', coalesce=True, misfire_grace_time=None, **config.scheduler, timezone='UTC')
		scheduler.start()

	log.info('Terminating the scout.')