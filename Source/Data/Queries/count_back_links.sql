SELECT
	T.ChildId,
	S.PageLanguage, S.PageTitle, S.PageUsesPlugins, S.Url, S.Timestamp,
	COUNT(*) AS TotalBackLinks
FROM Topology T
INNER JOIN Snapshot S ON T.ChildId = S.Id
GROUP BY T.ChildId
ORDER BY TotalBackLinks DESC;