SELECT S.Id, S.MediaExtension, S.PageTitle, S.PageUsesPlugins, S.Url, S.Timestamp, SI.IsSensitive, SI.Points, R.Id, R.CreationTime
FROM Snapshot S
INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
INNER JOIN Recording R ON S.Id = R.SnapshotId
ORDER BY R.CreationTime;