SELECT T.ParentId, S.Url, S.Timestamp, COUNT(*) AS TotalOutLinks
FROM Topology T
INNER JOIN Snapshot S ON T.ParentId = S.Id
GROUP BY T.ParentId
ORDER BY TotalOutLinks DESC;