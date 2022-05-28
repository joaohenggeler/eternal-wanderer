SELECT W.Word AS Tag, SUM(SW.Count) AS TotalCount FROM Snapshot S
INNER JOIN SnapshotWord SW ON S.Id = SW.SnapshotId
INNER JOIN Word W ON SW.WordId = W.Id
WHERE W.IsTag
GROUP BY W.Word
ORDER BY TotalCount DESC, W.Word;