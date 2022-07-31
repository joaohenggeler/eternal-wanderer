SELECT
	S.Id, S.PageLanguage, S.PageTitle, S.PageUsesPlugins, S.Url, S.Timestamp,
	W.Word, SUM(SW.Count) AS TotalCount
FROM Snapshot S
INNER JOIN SnapshotWord SW ON S.Id = SW.SnapshotId
INNER JOIN Word W ON SW.WordId = W.Id
WHERE NOT W.IsTag
GROUP BY W.Word
ORDER BY TotalCount DESC, W.Word;