#!/usr/bin/env python3

"""
	@TODO

	Flash: https://get.adobe.com/flashplayer/about/
	Shockwave: https://www.adobe.com/shockwave/welcome/index.html
	Authorware: https://www.adobe.com/shockwave/welcome/authorwareonly.html
	Silverlight: https://www.microsoft.com/silverlight/iis-smooth-streaming/demo/
	Java + MIDI + MOD: http://www.javaonthebrain.com/java/noids/tpanindex.html
	BGSOUND: https://web.archive.org/web/20070702203805if_/http://www.spacerock.com/htmlref/BGSOUND1.html
	MIDI: https://web.archive.org/web/20021010095601if_/http://www.yamaha-xg.com/mps/index.html
	MIDI Hang: https://web.archive.org/web/19961221002525if_/http://www.geocities.com/Heartland/8055/
	AIFF: https://web.archive.org/web/20010306021445if_/http://www.big.or.jp:80/~frog/others/bbb.html
	RealAudio: http://web.archive.org/web/19991012120206if_/http://www.big.or.jp/~frog/others/plug/hello.ra
	QuickTime Embed Tag: https://web.nmsu.edu/~leti/portfolio/quicktimemovie.html
	Quicktime Object Tag: http://goa103.free.fr/t_63455/media_player.php
	AVI: https://web.archive.org/web/20191030020045if_/http://www.eyeone.com/fun/index_page.jsp?fun_id=332
	MIDI + Flash in different frames: https://web.archive.org/web/20021105045704if_/http://www.yamaha-xg.com/mps/game/3/scene1.htm
	WMV: https://web.archive.org/web/20100323053720if_/http://thirdplanetvideo.com/Flip4MacTestPage.html
	Standalone Media: https://web.archive.org/web/20181106025854if_/http://www.geocities.co.jp/AnimalPark-Pochi/1130/animation.html
	Open New Window: https://web.archive.org/web/20010516004218/http://www.geocities.com/colosseum/8533/
	Crescendo MIDI: https://web.archive.org/web/19961221004112if_/http://www.geocities.com/TimesSquare/8497/index.html
	MIDI + Java + Standalone + Alerts: https://web.archive.org/web/19990222174035if_/http://www.geocities.com/Heartland/Plains/1036/arranco.html
	Frames: https://web.archive.org/web/19961221002554if_/http://www.geocities.com:80/Hollywood/Hills/5988/
	Redirect: https://web.archive.org/web/19990127111318if_/http://www6.geocities.com:80/Heartland/
	Japanese Applet Parameters: http://chutapita.nobody.jp/top/mapdata/zumidan1.html

	@TODO: Docs

	@TODO: Classic Firefox look
	@TODO: The proxy thing
	@TODO: Mastodon support?

	@TODO: Add VRML support via OpenVRML
	@TODO: Create pluginreg.dat dynamically? browse.py -generate
	@TODO: Crescendo tags can sometimes appear type="music/crescendo" song="music.mid"
	@TODO: Java parameters for Japanese sites (requires a Greasemonkey user script since we can't change the environment variable at runtime)
	@TODO: Censor email addresses and phone numbers liek wayback_exe.
	@TODO: Add Shockwave movies to the standalone media list. Check if there's any interesting parameters that should be used.
	
	C++ Windows XP Support for VS 2017 (v141) tools

	Java Versions:
	- 6u7 - last version before the next generation Java Plugin.
	- 7u3 - last version where the security level can be LOW without crashing Firefox 52.
	- 7u17 - last version where the security level can be LOW.
	- 7u45 - last version before the exception site list was added.
	- 8u11 - last version where the security level can be MEDIUM.

	{"url": "http://www.geocities.com/", "timestamp": "19961022173245"},
	{"url": "http://www.geocities.co.jp/", "timestamp": "19980123230306"},
	{"url": "http://www.yahoo.com/", "timestamp": "19961017235908"},
	{"url": "http://www.yahoo.co.jp/", "timestamp": "19961120065342"},
	{"url": "http://www.angelfire.com/", "timestamp": "19961028070227"},
	{"url": "http://tripod.lycos.com/", "timestamp": "20000229091928"},
	{"url": "http://www.fortunecity.com/", "timestamp": "19961227145545"},
	{"url": "http://www.developer.com/", "timestamp": "19970531223941"},
	{"url": "http://www.gamelan.com/", "timestamp": "19961220054054"},
	{"url": "http://www.aol.com/", "timestamp": "19961220155557"},
	{"url": "http://www.cnet.com/", "timestamp": "19961022174919"}
"""

import os
import re
import sqlite3
import string
import time
from argparse import ArgumentParser
from collections import Counter
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlparse

from selenium.common.exceptions import SessionNotCreatedException, StaleElementReferenceException, TimeoutException, WebDriverException # type: ignore
from waybackpy import WaybackMachineCDXServerAPI as Cdx
from waybackpy.exceptions import BlockedSiteError, NoCDXRecordFound

from common import Browser, CommonConfig, Database, Snapshot, container_to_lowercase, is_wayback_machine_available, setup_root_logger, wait_for_wayback_machine_rate_limit, was_exit_command_entered

####################################################################################################

class ScoutConfig(CommonConfig):

	# From the config file.
	initial_snapshots: List[Dict[str, str]]
	min_year: Optional[int]
	max_year: Optional[int]
	ignore_year_filter_for_domains: Optional[List[List[str]]] # Different from the config data type.
	max_depth: Optional[int]
	max_required_depth: Optional[int]

	standalone_media_file_extensions: Dict[str, bool] # Different from the config data type.
	standalone_media_points: int
	word_points: Dict[str, int]
	tag_points: Dict[str, int]
	word_filter: Dict[str, bool] # Different from the config data type.

	def __init__(self):
		super().__init__()
		self.load_subconfig('scout')

		if self.ignore_year_filter_for_domains is not None:
			allowed_domains = []
			
			for domain in container_to_lowercase(self.ignore_year_filter_for_domains):
				
				components = domain.split('.')
				components.reverse()
				allowed_domains.append(components)

				if components[0] == '*':
					extra_components = components.copy()
					extra_components.insert(0, '*')
					allowed_domains.append(extra_components)
				
			self.ignore_year_filter_for_domains = allowed_domains

		self.standalone_media_file_extensions = {extension: True for extension in container_to_lowercase(self.standalone_media_file_extensions)}
		self.word_points = container_to_lowercase(self.word_points)
		self.tag_points = container_to_lowercase(self.tag_points)
		self.word_filter = {word: True for word in container_to_lowercase(self.word_filter)}

config = ScoutConfig()
log = setup_root_logger('scout')

parser = ArgumentParser(description='@TODO')
parser.add_argument('max_iterations', nargs='?', type=int, default=-1, help='@TODO')
args = parser.parse_args()

####################################################################################################

log.info('Initializing the scout.')

with Database() as db, Browser(headless=True, use_extensions=True, user_script_filter=['Disable Alert Confirm Prompt']) as (browser, driver):

	checked_domains: Dict[str, bool] = {}

	def is_snapshot_domain_ignored_for_year_filter(url_key: str) -> bool:
		result = False

		if config.ignore_year_filter_for_domains:

			# E.g. "com,geocities)/hollywood/hills/5988"
			domain, _ = url_key.lower().split(')')
			
			if domain in checked_domains:
				return checked_domains[domain]

			snapshot_component_list = domain.split(',')

			for allowed_component_list in config.ignore_year_filter_for_domains:
				
				if len(allowed_component_list) > len(snapshot_component_list):
					continue

				for snapshot_component, allowed_component in zip(snapshot_component_list, allowed_component_list):
					if allowed_component != '*' and snapshot_component != allowed_component:
						break
				else:
					result = True
					break

			checked_domains[domain] = result

		return result

	db.create_function('IS_SNAPSHOT_DOMAIN_IGNORED_FOR_YEAR_FILTER', 1, is_snapshot_domain_ignored_for_year_filter)

	try:
		log.info('Inserting the initial Wayback Machine snapshots.')

		initial_snapshots = []
		for page in config.initial_snapshots:
			
			url = page['url']
			timestamp = page['timestamp']
			
			try:
				cdx = Cdx(url=url, filters=['statuscode:200', 'mimetype:text/html'])
				best_snapshot = cdx.near(wayback_machine_timestamp=timestamp)

				cdx.filters.append(f'digest:{best_snapshot.digest}')
				best_snapshot = cdx.oldest()

				initial_snapshots.append({'state': Snapshot.QUEUED, 'depth': 0, 'is_standalone_media': False,
										   'url': best_snapshot.original, 'timestamp': best_snapshot.timestamp,
										   'is_excluded': False, 'url_key': best_snapshot.urlkey, 'digest': best_snapshot.digest})
			except NoCDXRecordFound:
				log.warning(f'Could not find any snapshots for the initial page at "{url}" near {timestamp}.')	
			
			except BlockedSiteError:
				log.warning(f'The snapshot for the initial page at "{url}" near {timestamp} has been excluded from the Wayback Machine.')
				initial_snapshots.append({'state': Snapshot.SCOUTED, 'depth': 0, 'is_standalone_media': False, 'url': url,
										  'timestamp': timestamp, 'is_excluded': True, 'url_key': None, 'digest': None})

			except Exception as error:
				log.error(f'Failed to find a snapshot for the initial page at "{url}" near {timestamp} with the error: {repr(error)}')

				while not is_wayback_machine_available():
					log.warning(f'Waiting {config.unavailable_wayback_machine_wait} seconds for the Wayback Machine to become available again.')
					time.sleep(config.unavailable_wayback_machine_wait)

		db.executemany(	'''
						INSERT OR IGNORE INTO Snapshot (State, Depth, IsStandaloneMedia, Url, Timestamp, IsExcluded, UrlKey, Digest)
						VALUES (:state, :depth, :is_standalone_media, :url, :timestamp, :is_excluded, :url_key, :digest);
						''', initial_snapshots)

		word_and_tag_points = []
		for word, points in config.word_points.items():
			word_and_tag_points.append({'word': word, 'is_tag': False, 'points': points})

		for tag, points in config.tag_points.items():
			word_and_tag_points.append({'word': tag, 'is_tag': True, 'points': points})

		db.executemany(	'''
						INSERT INTO Word (Word, IsTag, Points)
						VALUES (:word, :is_tag, :points)
						ON CONFLICT (Word, IsTag)
						DO UPDATE SET Points = :points;
						''', word_and_tag_points)

		db.execute('INSERT OR REPLACE INTO Config (Name, Value) VALUES (:name, :value);', {'name': 'standalone_media_points', 'value': config.standalone_media_points})

		db.commit()

	except sqlite3.Error as error:
		log.error(f'Could not insert the initial snapshots and word points with the error: {repr(error)}')
		db.rollback()
		raise

####################################################################################################

	try:
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
									SELECT S.*, CAST(SUBSTR(S.Timestamp, 1, 4) AS INTEGER) AS Year
									FROM Snapshot S
									LEFT JOIN Snapshot PS ON S.ParentId = PS.Id
									LEFT JOIN SnapshotScore PSS ON PS.Id = PSS.Id
									WHERE S.State = :queued_state
										AND NOT S.IsStandaloneMedia
										AND NOT S.IsExcluded
										AND (:min_year IS NULL OR Year >= :min_year OR IS_SNAPSHOT_DOMAIN_IGNORED_FOR_YEAR_FILTER(S.UrlKey))
										AND (:max_year IS NULL OR Year <= :max_year OR IS_SNAPSHOT_DOMAIN_IGNORED_FOR_YEAR_FILTER(S.UrlKey))
										AND (:max_depth IS NULL OR S.Depth <= :max_depth)
									ORDER BY
										S.Priority DESC,
										(S.Depth = 0) DESC,
										(:max_required_depth IS NULL OR S.Depth <= :max_required_depth) DESC,
										IFNULL(PS.UsesPlugins, FALSE) DESC,
										IFNULL(PSS.Points, 0) DESC,
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
				wait_for_wayback_machine_rate_limit()
				log.info(f'Scouting snapshot #{snapshot.Id} {snapshot} located at a depth of {snapshot.Depth} pages.')
				
				original_window = driver.current_window_handle
				driver.get(snapshot.WaybackUrl)

				while not browser.is_current_url_valid_wayback_machine_page():
					log.warning(f'Waiting {config.unavailable_wayback_machine_wait} seconds for the Wayback Machine to become available again.')
					time.sleep(config.unavailable_wayback_machine_wait)
					driver.get(snapshot.WaybackUrl)

			except SessionNotCreatedException:
				log.warning('Terminated the WebDriver session abruptly.')
				break
			except TimeoutException:
				log.warning('Timed out the WebDriver while loading the snapshot.')
				continue
			except WebDriverException as error:
				log.error(f'Failed to load the snapshot with the error: {repr(error)}')
				continue

			url_list: List[Tuple[str, bool]] = []

			try:
				for _ in browser.switch_through_frames():

					element_list = driver.find_elements_by_xpath(r'//*[@href or @src]')
					for element in element_list:

						try:
							# From testing, the attribute name is case insensitive
							# and the tag name is always lowercase.
							href = element.get_attribute('href')
							src = element.get_attribute('src')
							tag_name = element.tag_name
						except StaleElementReferenceException:
							continue

						for url in filter(None, [href, src]):

							parts = urlparse(url)
							is_valid = parts.scheme in ['http', 'https'] and parts.netloc != ''
							is_archive_org = parts.hostname is not None and (parts.hostname == 'archive.org' or parts.hostname.endswith('.archive.org'))

							if is_valid and not is_archive_org:

								_, file_extension = os.path.splitext(parts.path)
								is_standalone = tag_name == 'a' and file_extension.strip('.').lower() in config.standalone_media_file_extensions
								url_list.append((url, is_standalone))

								# Retrieve any additional URLs that show up in the query string.
								# We'll allow Internet Archive URLs here since we know they weren't
								# autogenerated. Note that the (oversimplified) URL regex already
								# checks if it's valid for our purposes.

								def is_extra_url_standalone(extra_urL: str) -> bool:
									extra_parts = urlparse(extra_urL)
									_, extra_file_extension = os.path.splitext(extra_parts.path)
									return extra_file_extension.strip('.').lower() in config.standalone_media_file_extensions

								query_dict = parse_qs(parts.query)
								for key, value_list in query_dict.items():
									for value in value_list:	
										
										extra_url_list = URL_REGEX.findall(value)
										for extra_urL in extra_url_list:
											is_extra_standalone = is_extra_url_standalone(extra_urL)
											url_list.append((extra_urL, is_extra_standalone))

								# For cases that have a query string without any key-value pairs.
								# E.g. "http://example.com/?http://other.com".
								if parts.query and not query_dict:
									
									extra_url_list = URL_REGEX.findall(parts.query)
									for extra_urL in extra_url_list:
										is_extra_standalone = is_extra_url_standalone(extra_urL)
										url_list.append((extra_urL, is_extra_standalone))

			except SessionNotCreatedException:
				log.warning('Terminated the WebDriver session abruptly.')
				break
			except WebDriverException as error:
				log.error(f'Failed to retrieve the snapshot\'s page elements with the error: {repr(error)}')
				continue
			
			url_list = list(dict.fromkeys(url_list))

			child_snapshots = []
			for url, is_standalone in url_list:

				state = Snapshot.SCOUTED if is_standalone else Snapshot.QUEUED
				mime_type_filter = r'!mimetype:text/.*' if is_standalone else r'mimetype:text/html'
				
				try:
					cdx = Cdx(url=url, filters=['statuscode:200', mime_type_filter])
					best_snapshot = cdx.near(wayback_machine_timestamp=snapshot.Timestamp)
					
					cdx.filters.append(f'digest:{best_snapshot.digest}')
					best_snapshot = cdx.oldest()

					child_snapshots.append({'parent_id': snapshot.Id, 'state': state, 'depth': snapshot.Depth + 1,
											'is_standalone_media': is_standalone, 'url': best_snapshot.original,
											'timestamp': best_snapshot.timestamp, 'is_excluded': False,
											'url_key': best_snapshot.urlkey, 'digest': best_snapshot.digest})
				except NoCDXRecordFound:
					pass
				except BlockedSiteError:
					log.warning(f'The next snapshot at "{url}" near {snapshot.Timestamp} has been excluded from the Wayback Machine.')
					child_snapshots.append({'parent_id': snapshot.Id, 'state': state, 'depth': snapshot.Depth + 1,
											'is_standalone_media': is_standalone, 'url': url, 'timestamp': snapshot.Timestamp,
											'is_excluded': True, 'url_key': None, 'digest': None})
				except Exception as error:
					log.error(f'Failed to find the next snapshot at "{url}" near {snapshot.Timestamp} with the error: {repr(error)}')

					# http://C:/cgi-bin/v2/gefeedback/PID=86009337312043871309418,09418&AGENT=aolnet
					# "Snapshot returned by CDX API has 5 properties instead of expected 7 properties.\nProblematic Snapshot"
					while not is_wayback_machine_available():
						log.warning(f'Waiting {config.unavailable_wayback_machine_wait} seconds for the Wayback Machine to become available again.')
						time.sleep(config.unavailable_wayback_machine_wait)

			log.info(f'Found {len(child_snapshots)} snapshots.')
	
			try:
				word_and_tag_count: Counter = Counter()
				title = driver.title
				uses_plugins = False
				is_filtered = False

				for _ in browser.switch_through_frames():

					page_text = driver.execute_script('return window.document.documentElement.innerText;')
					for word in page_text.lower().split():
						
						word = word.strip(string.punctuation)
						if word:

							if word in config.word_points:
								word_and_tag_count[(word, False)] += 1

							if word in config.word_filter:
								is_filtered = True

					for tag in config.tag_points:
						tag_list = driver.find_elements_by_tag_name(tag)
						word_and_tag_count[(tag, True)] += len(tag_list)

					# <input value="http://www.geocities.com/Hollywood/Hills/5988/random.mid" onfocus="this.focus();this.select();">
					uses_plugins = uses_plugins or any(driver.find_elements_by_tag_name(tag) for tag in ['object', 'embed', 'applet', 'bgsound'])

				if is_filtered:
					log.warning('The snapshot contains one or more filtered words.')

				browser.close_all_windows_except(original_window)

			except SessionNotCreatedException:
				log.warning('Terminated the WebDriver session abruptly.')
				break
			except WebDriverException as error:
				log.error(f'Failed to analyze the snapshot\'s page with the error: {repr(error)}')
				continue

			try:
				db.executemany(	'''
								INSERT OR IGNORE INTO Snapshot (ParentId, State, Depth, IsStandaloneMedia, Url, Timestamp, IsExcluded, UrlKey, Digest)
								VALUES (:parent_id, :state, :depth, :is_standalone_media, :url, :timestamp, :is_excluded, :url_key, :digest);
								''', child_snapshots)

				topology = [{'parent_id': child['parent_id'], 'url': child['url'], 'timestamp': child['timestamp'], 'digest': child['digest']} for child in child_snapshots]
				db.executemany(	'''
								INSERT OR IGNORE INTO Topology (ParentId, ChildId)
								VALUES (:parent_id, (SELECT Id FROM Snapshot WHERE Url = :url AND Timestamp = :timestamp AND Digest = :digest))
								''', topology)

				word_and_tag_points = [{'snapshot_id': snapshot.Id, 'word': word, 'is_tag': is_tag, 'count': count} for (word, is_tag), count in word_and_tag_count.items() if count > 0]
				db.executemany(	'''
								INSERT OR REPLACE INTO SnapshotWord (SnapshotId, WordId, Count)
								VALUES (:snapshot_id, (SELECT Id FROM Word WHERE Word = :word AND IsTag = :is_tag), :count)
								''', word_and_tag_points)

				db.execute( '''
							UPDATE Snapshot
							SET State = :scouted_state, Title = :title, UsesPlugins = :uses_plugins, IsFiltered = :is_filtered
							WHERE Id = :id;
							''', {'scouted_state': Snapshot.SCOUTED, 'title': title, 'uses_plugins': uses_plugins, 'is_filtered': is_filtered, 'id': snapshot.Id})

				db.execute( '''
							UPDATE Snapshot
							SET UsesPlugins = :uses_plugins, IsFiltered = :is_filtered
							WHERE IsStandaloneMedia AND ParentId = :parent_id;
							''', {'uses_plugins': True, 'is_filtered': is_filtered, 'parent_id': snapshot.Id})

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