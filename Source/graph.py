#!/usr/bin/env python3

from argparse import ArgumentParser

from common.database import Database
from common.snapshot import Snapshot

if __name__ == '__main__':

	parser = ArgumentParser(description='Displays information based on the snapshot topology.')
	parser.add_argument('-trace', nargs='?', type=int, metavar='ID', help='Traces the scout path from a given snapshot to a initial page.')
	args = parser.parse_args()

	# @Future: Draw a graph where each node represents a snapshot and generate a word cloud of each page.

	if not any(vars(args).values()):
		parser.error('No arguments provided.')

	with Database() as db:

		if args.trace:

			snapshot_list = []
			next_id = args.trace

			while next_id is not None:

				cursor = db.execute('SELECT * FROM Snapshot WHERE Id = :id;', {'id': next_id})
				row = cursor.fetchone()

				if row is not None:
					snapshot = Snapshot(**row)
					snapshot_list.append(snapshot)
					next_id = snapshot.ParentId
				else:
					print(f'Could not find snapshot #{next_id}.')
					next_id = None

			if snapshot_list:
				print(f'Snapshot #{args.trace} Trace:')

			for snapshot in reversed(snapshot_list):
				print(f'[{snapshot.Depth}] #{snapshot.Id} {snapshot}')