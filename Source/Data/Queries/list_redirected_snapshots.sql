SELECT
	PS.Id AS ParentId, PS.Url AS ParentUrl, PS.Timestamp AS ParentTimestamp,
	S.Id AS ChildId, S.Url AS ChildUrl, S.Timestamp AS ChildTimestamp
FROM Snapshot S
INNER JOIN Snapshot PS ON S.ParentId = PS.Id
WHERE PS.State = 1
ORDER BY PS.Id;