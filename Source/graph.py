#!/usr/bin/env python3

from argparse import ArgumentParser

from common.database import Database
from common.snapshot import Snapshot

if __name__ == '__main__':

	parser = ArgumentParser(description='Displays information based on the snapshot topology.')
	parser.add_argument('-trace', type=int, metavar='ID', help='Traces the scout path from a given snapshot to a initial page.')
	parser.add_argument('-next', type=int, metavar='N', help='Lists the next snapshots to be published. Use -1 to show all snapshots.')
	args = parser.parse_args()

	# @Future: Draw a graph where each node represents a snapshot and generate a word cloud of each page.

	if not any(vars(args).values()):
		parser.error('No arguments provided.')

	with Database() as db:

		if args.trace is not None:

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


		if args.next is not None:

			from publish import PublishConfig
			config = PublishConfig()

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
					   			LIMIT :limit;
								''',
								{'approved_state': Snapshot.APPROVED, 'recorded_state': Snapshot.RECORDED,
								 'require_approval': config.require_approval, 'limit': args.next})

			snapshot_list = cursor.fetchall()

			if snapshot_list:
				print(f'Next {len(snapshot_list)} Snapshots:')
			else:
				print('No snapshots to publish.')

			for i, row in enumerate(snapshot_list, start=1):
				del row['Id']
				snapshot = Snapshot(**row, Id=row['SnapshotId'])
				print(f'[{i}] #{snapshot.Id} {snapshot} (priority = {snapshot.Priority}, options = {snapshot.Options})')