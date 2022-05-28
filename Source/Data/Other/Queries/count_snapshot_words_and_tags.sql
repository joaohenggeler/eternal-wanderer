SELECT S.Id, S.Url, S.Timestamp, W.Word, W.IsTag, SW.Count FROM Snapshot S
INNER JOIN SnapshotWord SW ON S.Id = SW.SnapshotId
INNER JOIN Word W ON SW.WordId = W.Id
ORDER BY S.Id, W.IsTag, SW.Count DESC;