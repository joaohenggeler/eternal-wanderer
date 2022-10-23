-- Requires the math extension for the POWER() function.
-- Using DB Browser for SQLite: Tools > Load Extension... > "DB Browser for SQLite\extensions\math.dll"
SELECT
	S.Id, S.Depth, S.Priority, S.PageLanguage, S.PageTitle, S.PageUsesPlugins, S.MediaExtension, S.Url, S.Timestamp, SI.IsSensitive, SI.Points,
	(CASE WHEN SI.Points >= 0 THEN 1 ELSE -1 END) * POWER(RANDOM() / 18446744073709551616 + 0.5, 1.0 / (ABS(SI.Points) + 1 + 100)) AS Rank,
	ROUND(RANDOM() / 18446744073709551616 + 0.5, 2) AS Random01
FROM Snapshot S
INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
WHERE S.State = 2 AND (NOT S.IsMedia OR S.MediaExtension IN ('spl', 'swf', 'dcr', 'dir', 'dxr', 'wrl', 'wrz', 'mid', 'midi', 'it', 'itz', 'mdz', 'med', 'mod', 's3m', 's3z', 'xm', 'xmz', 'sid', '3g2', '3gp', '3gpp', '3gpp2', 'aac', 'aif', 'aifc', 'aiff', 'amr', 'asf', 'asx', 'au', 'avi', 'awb', 'dif', 'divx', 'dv', 'flac', 'fli', 'flv', 'gsm', 'kar', 'm3u', 'm4a', 'm4v', 'mka', 'mkv', 'mov', 'mp2', 'mp3', 'mp4', 'mpe', 'mpeg', 'mpega', 'mpg', 'mpg4', 'mpga', 'mpv', 'mxf', 'mxu', 'oga', 'ogg', 'ogv', 'opus', 'pls', 'ra', 'ram', 'rm', 'snd', 'swfl', 'vob', 'wav', 'webm', 'wm', 'wma', 'wmv', 'wmx', 'wvx', 'xspf'))
ORDER BY S.Priority DESC, Rank DESC
LIMIT 50;