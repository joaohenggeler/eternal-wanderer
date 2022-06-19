#!/usr/bin/env python3

"""
	This script traverses web pages archived by the Wayback Machine (snapshots) and collects metadata from their content and from the CDX API.
	The scout script prioritizes pages that were manually added by the user through the configuration file as well as pages whose parent snapshot
	contains specific words and plugin media.
"""

import os
import re
import sqlite3
import string
import time
from argparse import ArgumentParser
from collections import Counter
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, unquote, urlparse

from selenium.common.exceptions import SessionNotCreatedException, StaleElementReferenceException, WebDriverException # type: ignore
from waybackpy.exceptions import BlockedSiteError, NoCDXRecordFound

from common import Browser, CommonConfig, Database, Snapshot, compose_wayback_machine_snapshot_url, container_to_lowercase, find_best_wayback_machine_snapshot, find_wayback_machine_snapshot_last_modified_time, is_url_available, is_wayback_machine_available, parse_wayback_machine_snapshot_url, setup_logger, was_exit_command_entered

####################################################################################################

class ScoutConfig(CommonConfig):
	""" The configuration that applies to the scout script. """

	# From the config file.
	extension_filter: List[str]
	user_script_filter: List[str]
	
	initial_snapshots: List[Dict[str, str]]
	min_year: Optional[int]
	max_year: Optional[int]
	max_depth: Optional[int]
	max_required_depth: Optional[int]

	standalone_media_points: int
	word_points: Dict[str, int]
	tag_points: Dict[str, int]
	sensitive_words: Dict[str, bool] # Different from the config data type.
	store_all_words_and_tags: bool

	def __init__(self):
		super().__init__()
		self.load_subconfig('scout')

		self.word_points = container_to_lowercase(self.word_points)
		self.tag_points = container_to_lowercase(self.tag_points)
		self.sensitive_words = {word: True for word in container_to_lowercase(self.sensitive_words)}

if __name__ == '__main__':

	config = ScoutConfig()
	log = setup_logger('scout')

	parser = ArgumentParser(description='Traverses web pages archived by the Wayback Machine (snapshots) and collects metadata from their content and from the CDX API. The scout script prioritizes pages that were manually added by the user through the configuration file as well as pages whose parent snapshot contains specific words and plugin media.')
	parser.add_argument('max_iterations', nargs='?', type=int, default=-1, help='How many snapshots to scout. Omit or set to %(default)s to run forever.')
	parser.add_argument('-skip', action='store_true', help='Whether to skip the initial snapshots specified in the configuration file.')
	args = parser.parse_args()

	####################################################################################################

	log.info('Initializing the scout.')

	# We don't want any extensions or user scripts that change the HTML document, but we do need to use the Greasemonkey extension with a user script
	# that disables the alert(), confirm(), and prompt() JavaScript functions. This prevents the UnexpectedAlertPresentException, which would otherwise
	# make it impossible to scrape pages that use those functions.
	# E.g. https://web.archive.org/web/19990222174035if_/http://www.geocities.com/Heartland/Plains/1036/arranco.html
	with Database() as db, Browser(headless=True, use_extensions=True, extension_filter=config.extension_filter, user_script_filter=config.user_script_filter) as (browser, driver):

		if not args.skip:
			try:
				log.info('Inserting the initial Wayback Machine snapshots.')

				initial_snapshots = []
				for page in config.initial_snapshots:
					
					url = page['url']
					timestamp = page['timestamp']
					
					try:
						log.debug(f'Locating the initial snapshot at "{url}" near {timestamp}.')
						best_snapshot, is_standalone_media, file_extension = find_best_wayback_machine_snapshot(timestamp=timestamp, url=url)
						last_modified_time = find_wayback_machine_snapshot_last_modified_time(best_snapshot.archive_url)

						state = Snapshot.SCOUTED if is_standalone_media else Snapshot.QUEUED

						initial_snapshots.append({ 'state': state, 'depth': 0, 'is_excluded': False, 'is_standalone_media': is_standalone_media,
												   'file_extension': file_extension, 'url': best_snapshot.original, 'timestamp': best_snapshot.timestamp,
												   'last_modified_time': last_modified_time, 'url_key': best_snapshot.urlkey, 'digest': best_snapshot.digest})
					except NoCDXRecordFound:
						log.warning(f'Could not find the initial snapshot at "{url}" near {timestamp}.')	
					
					except BlockedSiteError:
						log.warning(f'The initial snapshot at "{url}" near {timestamp} has been excluded from the Wayback Machine.')
						initial_snapshots.append({'state': Snapshot.SCOUTED, 'depth': 0, 'is_excluded': True, 'is_standalone_media': None,
												  'file_extension': None, 'url': url, 'timestamp': timestamp, 'last_modified_time': None,
												  'url_key': None, 'digest': None})

					except Exception as error:
						log.error(f'Failed to find the initial snapshot at "{url}" near {timestamp} with the error: {repr(error)}')

						while not is_wayback_machine_available():
							log.warning(f'Waiting {config.unavailable_wayback_machine_wait} seconds for the Wayback Machine to become available again.')
							time.sleep(config.unavailable_wayback_machine_wait)

				db.executemany(	'''
								INSERT OR IGNORE INTO Snapshot (State, Depth, IsExcluded, IsStandaloneMedia, FileExtension, Url, Timestamp, LastModifiedTime, UrlKey, Digest)
								VALUES (:state, :depth, :is_excluded, :is_standalone_media, :file_extension, :url, :timestamp, :last_modified_time, :url_key, :digest);
								''', initial_snapshots)

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
				log.error(f'Could not insert the initial snapshots and word points with the error: {repr(error)}')
				db.rollback()
				raise

		else:
			log.info('Skipping the initial snapshots at the user\'s request.')

	####################################################################################################

		try:
			# This is an oversimplified regular expression, but it works for our case since we are
			# going to ask the CDX API what the real URLs are. The purpose of this pattern is to
			# minimize the amount of requests to this endpoint.
			URL_REGEX = re.compile(r'https?://.+', re.IGNORECASE)

			num_iterations = 0
			while True:

				if args.max_iterations >= 0 and num_iterations >= args.max_iterations:
					log.info(f'Stopping after running {args.max_iterations} times.')
					break

				num_iterations += 1

				if was_exit_command_entered():
					log.info('Stopping at the user\'s request.')
					break

				try:
					cursor = db.execute('''
										SELECT 	S.*,
												CAST(MIN(SUBSTR(S.Timestamp, 1, 4), IFNULL(SUBSTR(S.LastModifiedTime, 1, 4), '9999')) AS INTEGER) AS OldestYear
										FROM Snapshot S
										LEFT JOIN Snapshot PS ON S.ParentId = PS.Id
										LEFT JOIN SnapshotInfo PSI ON PS.Id = PSI.Id
										WHERE S.State = :queued_state
											AND NOT S.IsStandaloneMedia
											AND NOT S.IsExcluded
											AND NOT IS_URL_DOMAIN_EXCLUDED(S.UrlKey)
											AND (:min_year IS NULL OR OldestYear >= :min_year)
											AND (:max_year IS NULL OR OldestYear <= :max_year)
											AND (:max_depth IS NULL OR S.Depth <= :max_depth)
										ORDER BY
											S.Priority DESC,
											(S.Depth = 0) DESC,
											(:max_required_depth IS NULL OR S.Depth <= :max_required_depth) DESC,
											IFNULL(PS.UsesPlugins, FALSE) DESC,
											IFNULL(PSI.Points, 0) DESC,
											RANDOM()
										LIMIT 1;
										''', {'queued_state': Snapshot.QUEUED, 'min_year': config.min_year, 'max_year': config.max_year,
											  'max_depth': config.max_depth, 'max_required_depth': config.max_required_depth})
					
					row = cursor.fetchone()
					if row is not None:
						snapshot = Snapshot(**dict(row))
					else:
						log.info('Ran out of snapshots to scout.')
						break

				except sqlite3.Error as error:
					log.error(f'Failed to select the next snapshot with the error: {repr(error)}')
					time.sleep(config.database_error_wait)
					continue
				
				try:
					log.info(f'Scouting snapshot #{snapshot.Id} {snapshot} located at a depth of {snapshot.Depth} pages.')
					original_window = driver.current_window_handle
					browser.go_to_wayback_url(snapshot.WaybackUrl)
				except SessionNotCreatedException:
					log.warning('Terminated the WebDriver session abruptly.')
					break
				except WebDriverException as error:
					log.error(f'Failed to load the snapshot with the error: {repr(error)}')
					continue

				url_list: List[Tuple[str, Optional[str]]] = []

				try:
					for _ in browser.switch_through_frames():

						# Retrieve links from all href attributes.
						# This is useful for snapshots that use tags other than <a> to link to other pages.
						# E.g. https://web.archive.org/web/19961220170231if_/http://www.geocities.com/NorthPole/
						element_list = driver.find_elements_by_xpath(r'//*[@href]')
						for element in element_list:

							try:
								# From testing, the attribute name is case insensitive
								# and the tag name is always lowercase.
								url = element.get_attribute('href')
							except StaleElementReferenceException:
								log.debug('Skipping stale element.')
								continue

							if url:

								wayback_timestamp = None

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
								is_archive_org = parts.hostname is not None and (parts.hostname == 'archive.org' or parts.hostname.endswith('.archive.org'))
	
								if is_valid and not is_archive_org:

									url_list.append((url, wayback_timestamp))

									# Retrieve any additional URLs that show up in the query string.
									# We'll allow Internet Archive URLs here since we know they weren't
									# autogenerated. Note that the (oversimplified) URL regex already
									# checks if it's valid for our purposes.

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

				except SessionNotCreatedException:
					log.warning('Terminated the WebDriver session abruptly.')
					break
				except WebDriverException as error:
					log.error(f'Failed to retrieve the snapshot\'s page elements with the error: {repr(error)}')
					continue
				
				# Remove any duplicates to minimize the amount of requests to the CDX API.
				url_list = list(dict.fromkeys(url_list))

				child_snapshots = []
				for url, wayback_timestamp in url_list:

					timestamp = wayback_timestamp or snapshot.Timestamp
					
					try:
						log.debug(f'Locating the next snapshot at "{url}" near {timestamp}.')
						best_snapshot, is_standalone_media, file_extension = find_best_wayback_machine_snapshot(timestamp=timestamp, url=url)
						last_modified_time = find_wayback_machine_snapshot_last_modified_time(best_snapshot.archive_url)

						state = Snapshot.SCOUTED if is_standalone_media else Snapshot.QUEUED

						child_snapshots.append({'parent_id': snapshot.Id, 'state': state, 'depth': snapshot.Depth + 1, 'is_excluded': False,
												'is_standalone_media': is_standalone_media, 'file_extension': file_extension,
												'url': best_snapshot.original, 'timestamp': best_snapshot.timestamp,
												'last_modified_time': last_modified_time, 'url_key': best_snapshot.urlkey,
												'digest': best_snapshot.digest})
					except NoCDXRecordFound:
						pass
					except BlockedSiteError:
						log.warning(f'The next snapshot at "{url}" near {timestamp} has been excluded from the Wayback Machine.')
						child_snapshots.append({'parent_id': snapshot.Id, 'state': Snapshot.QUEUED, 'depth': snapshot.Depth + 1, 'is_excluded': True,
												'is_standalone_media': None, 'file_extension': None, 'url': url, 'timestamp': timestamp,
												'last_modified_time': None, 'url_key': None, 'digest': None})
					except Exception as error:
						log.error(f'Failed to find the next snapshot at "{url}" near {timestamp} with the error: {repr(error)}')

						while not is_wayback_machine_available():
							log.warning(f'Waiting {config.unavailable_wayback_machine_wait} seconds for the Wayback Machine to become available again.')
							time.sleep(config.unavailable_wayback_machine_wait)

				log.info(f'Found {len(child_snapshots)} valid snapshots out of {len(url_list)} links.')
		
				try:
					# Find the URL to every frame so we can analyze the original pages without any changes.
					# Otherwise, we would be counting tags inserted by the Wayback Machine.
					frame_url_list = [frame_url for frame_url in browser.switch_through_frames()]

					word_and_tag_counter: Counter = Counter()
					uses_plugins = False
					title = driver.title

					# The same frame may show up more than once, which is fine since that's what the user
					# sees meaning we want to count duplicate words and tags in this case.
					for frame_url in frame_url_list:

						# Convert the URL to the unmodified archived page.
						wayback_parts = parse_wayback_machine_snapshot_url(frame_url)
						
						if wayback_parts is not None:
							wayback_parts = wayback_parts._replace(Modifier=Snapshot.IDENTICAL_MODIFIER)
							raw_wayback_url = compose_wayback_machine_snapshot_url(parts=wayback_parts)
						else:
							raw_wayback_url = compose_wayback_machine_snapshot_url(timestamp=snapshot.Timestamp, modifier=Snapshot.IDENTICAL_MODIFIER, url=frame_url)

						# Avoid counting words and tags from 404 Wayback Machine pages.
						# Redirects are allowed here since the frame's timestamp is
						# inherited from the main page's snapshot, meaning that in the
						# vast majority of cases we're going to be redirected to the
						# nearest archived copy (if one exists). Redirected pages keep
						# their modifier.
						# E.g. https://web.archive.org/web/19970702100947if_/http://www.informatik.uni-rostock.de:80/~knorr/homebomb.html
						if not is_url_available(raw_wayback_url, allow_redirects=True):
							log.warning(f'Skipping the frame "{raw_wayback_url}" since it was not archived by the Wayback Machine.')
							continue

						browser.go_to_wayback_url(raw_wayback_url, allow_redirects=True)

						page_text = driver.execute_script('return window.document.documentElement.innerText;')
						for word in page_text.lower().split():
							
							word = word.strip(string.punctuation)
							if word:
								if config.store_all_words_and_tags or word in config.word_points:
									word_and_tag_counter[(word, False)] += 1

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
						uses_plugins = uses_plugins or any(driver.find_elements_by_tag_name(tag) for tag in ['object', 'embed', 'applet', 'bgsound'])

					browser.close_all_windows_except(original_window)

					log.debug(f'Words and tags found: {word_and_tag_counter}')

				except SessionNotCreatedException:
					log.warning('Terminated the WebDriver session abruptly.')
					break
				except WebDriverException as error:
					log.error(f'Failed to analyze the snapshot\'s page with the error: {repr(error)}')
					continue

				try:
					db.executemany(	'''
									INSERT OR IGNORE INTO Snapshot (ParentId, State, Depth, IsExcluded, IsStandaloneMedia, FileExtension, Url, Timestamp, LastModifiedTime, UrlKey, Digest)
									VALUES (:parent_id, :state, :depth, :is_excluded, :is_standalone_media, :file_extension, :url, :timestamp, :last_modified_time, :url_key, :digest);
									''', child_snapshots)

					topology = [{'parent_id': child['parent_id'], 'url': child['url'], 'timestamp': child['timestamp']} for child in child_snapshots]
					db.executemany(	'''
									INSERT OR IGNORE INTO Topology (ParentId, ChildId)
									VALUES (:parent_id, (SELECT Id FROM Snapshot WHERE Url = :url AND Timestamp = :timestamp))
									''', topology)

					if config.store_all_words_and_tags:
						word_and_tag_values = [{'word': word, 'is_tag': is_tag} for word, is_tag in word_and_tag_counter]
						db.executemany('INSERT OR IGNORE INTO Word (Word, IsTag) VALUES (:word, :is_tag);', word_and_tag_values)

					word_and_tag_count = [{'snapshot_id': snapshot.Id, 'word': word, 'is_tag': is_tag, 'count': count} for (word, is_tag), count in word_and_tag_counter.items()]
					db.executemany(	'''
									INSERT OR REPLACE INTO SnapshotWord (SnapshotId, WordId, Count)
									VALUES (:snapshot_id, (SELECT Id FROM Word WHERE Word = :word AND IsTag = :is_tag), :count)
									''', word_and_tag_count)

					db.execute( '''
								UPDATE Snapshot
								SET State = :scouted_state, UsesPlugins = :uses_plugins, Title = :title
								WHERE Id = :id;
								''', {'scouted_state': Snapshot.SCOUTED, 'uses_plugins': uses_plugins, 'title': title, 'id': snapshot.Id})

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

	log.info('Terminating the scout.')