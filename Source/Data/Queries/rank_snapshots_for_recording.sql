-- Requires the math extension for the POWER() function.
-- Using DB Browser for SQLite: Tools > Load Extension... > "DB Browser for SQLite\extensions\math.dll"
SELECT
	S.Id, S.Depth, S.Priority, S.PageLanguage, S.PageTitle, S.PageUsesPlugins, S.MediaExtension, S.Url, S.Timestamp, SI.IsSensitive, SI.Points,
	(CASE WHEN SI.Points >= 0 THEN 1 ELSE -1 END) * POWER(RANDOM() / 18446744073709551616 + 0.5, 1.0 / (ABS(SI.Points) + 1 + 1500)) AS Rank,
	ROUND(RANDOM() / 18446744073709551616 + 0.5, 2) AS Random01
FROM Snapshot S
INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
WHERE S.State = 2 AND (NOT S.IsStandaloneMedia OR S.MediaExtension IN ('swf', 'dcr', 'wrl', 'wrz', 'mid', 'mod', 's3m', 'xm', 'aif', 'aiff', 'au', 'avi', 'flac', 'flv', 'mkv', 'mov', 'mp3', 'mp4', 'mpeg', 'mpg', 'ogg', 'ra', 'ram', 'rm', 'wav', 'webm', 'wma', 'wmv'))
ORDER BY S.Priority DESC, Rank DESC;