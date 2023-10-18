SELECT
	S.Id, S.Url, S.Timestamp,
	SFT.Count AS FontTags, SFW.Count AS FontWords, SDW.Count AS DownloadWords
FROM Snapshot S
INNER JOIN
(
	SELECT S.Id, SW.Count
	FROM Snapshot S
	INNER JOIN SnapshotWord SW ON S.Id = SW.SnapshotId
	INNER JOIN Word W ON SW.WordId = W.Id
	WHERE W.IsTag AND W.Word = 'font'
	GROUP BY S.Id
) SFT ON S.Id = SFT.Id
INNER JOIN
(
	SELECT S.Id, SW.Count
	FROM Snapshot S
	INNER JOIN SnapshotWord SW ON S.Id = SW.SnapshotId
	INNER JOIN Word W ON SW.WordId = W.Id
	WHERE NOT W.IsTag AND W.Word = 'font'
	GROUP BY S.Id
) SFW ON S.Id = SFW.Id
INNER JOIN
(
	SELECT S.Id, SW.Count
	FROM Snapshot S
	INNER JOIN SnapshotWord SW ON S.Id = SW.SnapshotId
	INNER JOIN Word W ON SW.WordId = W.Id
	WHERE NOT W.IsTag AND W.Word = 'download'
	GROUP BY S.Id
) SDW ON S.Id = SDW.Id
ORDER BY S.Id;