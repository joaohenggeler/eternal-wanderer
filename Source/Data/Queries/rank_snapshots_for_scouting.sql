-- Requires the math extension for the POWER() function.
-- Using DB Browser for SQLite: Tools > Load Extension... > "DB Browser for SQLite\extensions\math.dll"
SELECT
	S.Id, S.Depth, S.Priority, S.Url, S.Timestamp, PSI.ParentPoints,
	IFNULL((CASE WHEN PSI.ParentPoints >= 0 THEN 1 ELSE -1 END) * POWER(RANDOM() / 18446744073709551616 + 0.5, 1.0 / (ABS(PSI.ParentPoints) + 1 + 100)), 0) AS Rank,
	ROUND(RANDOM() / 18446744073709551616 + 0.5, 2) AS Random01
FROM Snapshot S
LEFT JOIN
(
	SELECT T.ChildId, SUM(SI.Points) AS ParentPoints
	FROM Topology T
	INNER JOIN SnapshotInfo SI ON T.ParentId = SI.Id
	WHERE T.ParentId <> T.ChildId
	GROUP BY T.ChildId
) PSI ON S.Id = PSI.ChildId
WHERE S.State = 0
ORDER BY S.Priority DESC, Rank DESC
LIMIT 50;