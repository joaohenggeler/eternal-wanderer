SELECT
	S.Id, S.PageLanguage, S.PageTitle, S.PageUsesPlugins, S.MediaExtension, S.MediaTitle, S.MediaAuthor, S.Url, S.Timestamp,
	SI.Points, SI.IsSensitive,
	R.Id, R.CreationTime
FROM Snapshot S
INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
INNER JOIN Recording R ON S.Id = R.SnapshotId
ORDER BY R.CreationTime;