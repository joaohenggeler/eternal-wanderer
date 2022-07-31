SELECT S.*, SI.Points, SI.IsSensitive
FROM Snapshot S
INNER JOIN SnapshotInfo SI ON S.Id = SI.Id
WHERE S.State > 0
ORDER BY S.PageUsesPlugins DESC, SI.Points DESC;