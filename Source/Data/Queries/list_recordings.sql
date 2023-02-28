SELECT
	S.Id, S.Depth, S.PageLanguage, S.PageTitle, S.PageUsesPlugins, S.MediaExtension, S.MediaTitle, S.MediaAuthor, S.Url, S.Timestamp,
	SI.IsSensitive, SI.Points,
	R.Id, R.HasAudio, R.CreationTime,
	LCR.UrlHost, LCR.RecordingsSinceSameHost,
	LPR.DaysSinceLastPublished
FROM Snapshot S
INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
INNER JOIN Recording R ON S.Id = R.SnapshotId
LEFT JOIN
(
	SELECT 	SI.UrlHost,
			(SELECT COUNT(*) FROM Recording) - MAX(RRN.RowNum) AS RecordingsSinceSameHost
	FROM Snapshot S
	INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
	INNER JOIN
	(
		SELECT 	R.SnapshotId,
				(ROW_NUMBER() OVER (ORDER BY R.CreationTime)) AS RowNum
		FROM Recording R
	) RRN ON S.Id = RRN.SnapshotId
	GROUP BY SI.UrlHost
) LCR ON SI.UrlHost = LCR.UrlHost
LEFT JOIN
(
	SELECT 	S.UrlKey,
			JulianDay('now') - JulianDay(MAX(R.PublishTime)) AS DaysSinceLastPublished
	FROM Snapshot S
	INNER JOIN Recording R ON S.Id = R.SnapshotId
	GROUP BY S.UrlKey
) LPR ON S.UrlKey = LPR.UrlKey
ORDER BY R.CreationTime;