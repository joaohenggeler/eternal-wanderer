-- Requires the math extension for the POWER() function.
-- Using DB Browser for SQLite: Tools > Load Extension... > "DB Browser for SQLite\extensions\math.dll"
SELECT
	S.Id, S.Depth, S.Priority, S.Url, S.Timestamp,
	PS.Id AS ParentId, PS.PageLanguage AS ParentPageLanguage, PS.PageTitle AS ParentPageTitle, PS.PageUsesPlugins AS ParentPageUsesPlugins, PS.Url AS ParentUrl, PS.Timestamp AS ParentTimestamp,
	PSI.IsSensitive AS ParentIsSensitive, PSI.Points AS ParentPoints,
	IFNULL((CASE WHEN PSI.Points >= 0 THEN 1 ELSE -1 END) * POWER(RANDOM() / 18446744073709551616 + 0.5, 1.0 / (ABS(PSI.Points) + 1 + 0)), 0) AS Rank,
	ROUND(RANDOM() / 18446744073709551616 + 0.5, 2) AS Random01
FROM Snapshot S
LEFT JOIN Snapshot PS ON S.ParentId = PS.Id
LEFT JOIN SnapshotInfo PSI ON PS.Id = PSI.Id
WHERE S.State = 0
ORDER BY S.Priority DESC, Rank DESC
LIMIT 20;