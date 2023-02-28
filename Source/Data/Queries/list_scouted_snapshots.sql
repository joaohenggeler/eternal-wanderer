SELECT
	S.Id, S.Depth, S.PageLanguage, S.PageTitle, S.PageUsesPlugins, S.MediaExtension, S.ScoutTime, S.Url, S.Timestamp,
	SI.IsSensitive, SI.Points,
	PSI.NumParents, PSI.ParentPoints,
	LSS.UrlHost, LSS.SnapshotsSinceSameHost
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
LEFT JOIN
(
	SELECT 	SI.UrlHost,
			(SELECT COUNT(S.ScoutTime) FROM Snapshot S WHERE NOT S.IsMedia) - MAX(SRN.RowNum) AS SnapshotsSinceSameHost
	FROM Snapshot S
	INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
	INNER JOIN
	(
		SELECT 	S.Id,
				(ROW_NUMBER() OVER (ORDER BY S.ScoutTime)) AS RowNum
		FROM Snapshot S
		WHERE S.ScoutTime IS NOT NULL AND NOT S.IsMedia
	) SRN ON S.Id = SRN.Id
	GROUP BY SI.UrlHost
) LSS ON SI.UrlHost = LSS.UrlHost
WHERE S.State >= 2
ORDER BY S.ScoutTime;