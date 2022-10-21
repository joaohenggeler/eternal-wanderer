SELECT
	S.*, SI.Points, SI.IsSensitive,
	PSI.NumParents, PSI.ParentPoints
FROM Snapshot S
INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
LEFT JOIN
(
	SELECT T.ChildId, COUNT(*) AS NumParents, SUM(SI.Points) AS ParentPoints
	FROM Topology T
	INNER JOIN SnapshotInfo SI ON T.ParentId = SI.Id
	WHERE T.ParentId <> T.ChildId
	GROUP BY T.ChildId
) PSI ON S.Id = PSI.ChildId
WHERE S.State > 0
ORDER BY
	S.PageUsesPlugins DESC,
	SI.Points DESC,
	ParentPoints DESC,
	NumParents DESC;