-- Requires the math extension for the POWER() function.
-- In DB Browser for SQLite: Tools > Load Extension... > "DB Browser for SQLite\extensions\math.dll"
SELECT
	S.Id, S.MediaExtension, S.PageTitle, S.PageUsesPlugins, S.Url, S.Timestamp, SI.IsSensitive, SI.Points,
	CASE WHEN SI.Points >= 0 THEN POWER(RANDOM() / 18446744073709551616 + 0.5, 1.0 / MAX(SI.Points, 3000, 1)) ELSE SI.Points END AS Rank,
	ROUND(RANDOM() / 18446744073709551616 + 0.5, 2) AS Random01
FROM Snapshot S
INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
WHERE S.State = 2
ORDER BY Rank DESC;