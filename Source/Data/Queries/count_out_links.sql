SELECT
	T.ParentId,
	S.PageLanguage, S.PageTitle, S.PageUsesPlugins, S.Url, S.Timestamp,
	COUNT(*) AS TotalOutLinks
FROM Topology T
INNER JOIN Snapshot S ON T.ParentId = S.Id
GROUP BY T.ParentId
ORDER BY TotalOutLinks DESC;