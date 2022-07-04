-- Requires the math extension for the POWER() function.
-- In DB Browser for SQLite: Tools > Load Extension... > "DB Browser for SQLite\extensions\math.dll"
SELECT
	S.Id, S.IsStandaloneMedia, S.FileExtension, S.UsesPlugins, SI.IsSensitive, S.Url, S.Timestamp, SI.Points,
	(RANDOM() / 18446744073709551616 + 0.5) AS Random01,
	POWER((RANDOM() / 18446744073709551616 + 0.5), (4000 / (SI.Points + 1))) AS Rank
FROM Snapshot S
INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
WHERE S.State = 2 AND SI.Points >= 0
ORDER BY Rank DESC;