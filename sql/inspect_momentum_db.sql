-- MSSQL read-only inspection. Select the configured Analyst_TEST2_Airflow
-- database in SSMS before running. STRING_AGG requires SQL Server 2017+.
SELECT DB_NAME() AS database_name, SYSUTCDATETIME() AS checked_at_utc;

SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'dbo'
  AND TABLE_NAME IN ('momentum_rankings', 'security_metadata', 'sent_reports')
ORDER BY TABLE_NAME, ORDINAL_POSITION;

-- 6. Osszes momentumrekord, ticker es elmentett nap
SELECT
    COUNT(*) AS total_rows,
    COUNT(DISTINCT ticker) AS distinct_tickers,
    COUNT(DISTINCT as_of_date) AS saved_dates,
    MIN(as_of_date) AS first_saved_date,
    MAX(as_of_date) AS latest_saved_date
FROM dbo.momentum_rankings;


-- 7. Naponta hany momentumrekord van elmentve?
-- A legfrissebb nap jelenik meg legfelul.
SELECT
    as_of_date,
    COUNT(*) AS row_count,
    MIN(rank) AS best_rank,
    MAX(rank) AS worst_rank,
    COUNT(DISTINCT ticker) AS distinct_tickers
FROM dbo.momentum_rankings
GROUP BY as_of_date
ORDER BY as_of_date DESC;


-- 8. A legfrissebb elmentett napi teljes ranking
SELECT
    as_of_date,
    rank,
    ticker,
    ROUND(momentum_score, 4) AS momentum_score,
    ROUND(return_6m * 100.0, 2) AS return_6m_pct,
    ROUND(return_12m * 100.0, 2) AS return_12m_pct,
    ROUND(vol_6m * 100.0, 2) AS vol_6m_pct,
    ROUND(vol_12m * 100.0, 2) AS vol_12m_pct,
    sector,
    industry,
    created_at
FROM dbo.momentum_rankings
WHERE as_of_date = (SELECT MAX(as_of_date) FROM dbo.momentum_rankings)
ORDER BY rank;


-- 9. A legfrissebb napi Top 25
SELECT
    rank,
    ticker,
    ROUND(momentum_score, 4) AS momentum_score,
    ROUND(return_6m * 100.0, 2) AS return_6m_pct,
    ROUND(return_12m * 100.0, 2) AS return_12m_pct,
    COALESCE(sector, 'Unknown') AS sector,
    COALESCE(industry, 'Unknown') AS industry
FROM dbo.momentum_rankings
WHERE as_of_date = (SELECT MAX(as_of_date) FROM dbo.momentum_rankings)
  AND rank <= 25
ORDER BY rank;


-- 10. Legfrissebb Top 100 szektoreloszlasa
SELECT
    COALESCE(sector, 'Unknown') AS sector,
    COUNT(*) AS ticker_count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS share_pct
FROM dbo.momentum_rankings
WHERE as_of_date = (SELECT MAX(as_of_date) FROM dbo.momentum_rankings)
  AND rank <= 100
GROUP BY COALESCE(sector, 'Unknown')
ORDER BY ticker_count DESC, sector;


-- 11. Elozo elmentett nap Top 25 listaja
WITH saved_dates AS (
    SELECT DISTINCT TOP (2) as_of_date
    FROM dbo.momentum_rankings
    ORDER BY as_of_date DESC
), previous_date AS (
    SELECT MIN(as_of_date) AS as_of_date
    FROM saved_dates
)
SELECT
    r.as_of_date,
    r.rank,
    r.ticker,
    ROUND(r.momentum_score, 4) AS momentum_score,
    COALESCE(r.sector, 'Unknown') AS sector
FROM dbo.momentum_rankings AS r
JOIN previous_date AS p ON p.as_of_date = r.as_of_date
WHERE r.rank <= 25
ORDER BY r.rank;


-- 12. Valtozas a legfrissebb es az elozo elmentett Top 25 kozott
-- status: NEW, EXITED vagy REMAINED
WITH ordered_dates AS (
    SELECT
        as_of_date,
        DENSE_RANK() OVER (ORDER BY as_of_date DESC) AS date_order
    FROM dbo.momentum_rankings
    GROUP BY as_of_date
), current_top AS (
    SELECT r.ticker, r.rank
    FROM dbo.momentum_rankings AS r
    JOIN ordered_dates AS d ON d.as_of_date = r.as_of_date
    WHERE d.date_order = 1 AND r.rank <= 25
), previous_top AS (
    SELECT r.ticker, r.rank
    FROM dbo.momentum_rankings AS r
    JOIN ordered_dates AS d ON d.as_of_date = r.as_of_date
    WHERE d.date_order = 2 AND r.rank <= 25
), all_tickers AS (
    SELECT ticker FROM current_top
    UNION
    SELECT ticker FROM previous_top
)
SELECT
    a.ticker,
    p.rank AS previous_rank,
    c.rank AS current_rank,
    CASE
        WHEN p.ticker IS NULL THEN 'NEW'
        WHEN c.ticker IS NULL THEN 'EXITED'
        ELSE 'REMAINED'
    END AS status,
    CASE
        WHEN p.rank IS NOT NULL AND c.rank IS NOT NULL THEN p.rank - c.rank
        ELSE NULL
    END AS rank_change
FROM all_tickers AS a
LEFT JOIN previous_top AS p ON p.ticker = a.ticker
LEFT JOIN current_top AS c ON c.ticker = a.ticker
ORDER BY
    CASE
        WHEN p.ticker IS NULL THEN 1
        WHEN c.ticker IS NULL THEN 2
        ELSE 3
    END,
    COALESCE(c.rank, p.rank);


-- 13. Hianyos vagy gyanus napi snapshotok
-- Akkor erdemes megnezni, ha egy nap sorainak szama jelentosen elter a tobbitol.
WITH daily_counts AS (
    SELECT as_of_date, COUNT(*) AS row_count
    FROM dbo.momentum_rankings
    GROUP BY as_of_date
), average_count AS (
    SELECT AVG(CAST(row_count AS float)) AS avg_rows
    FROM daily_counts
)
SELECT
    d.as_of_date,
    d.row_count,
    ROUND(a.avg_rows, 2) AS average_rows,
    ROUND(d.row_count * 100.0 / NULLIF(a.avg_rows, 0), 2) AS percent_of_average
FROM daily_counts AS d
CROSS JOIN average_count AS a
WHERE d.row_count < a.avg_rows * 0.8
ORDER BY d.as_of_date DESC;


-- 14. Duplikalt rangok egy napon belul (normal esetben nincs talalat)
SELECT
    as_of_date,
    rank,
    COUNT(*) AS occurrences,
    STRING_AGG(CAST(ticker AS nvarchar(max)), N', ') AS tickers
FROM dbo.momentum_rankings
GROUP BY as_of_date, rank
HAVING COUNT(*) > 1
ORDER BY as_of_date DESC, rank;


-- 15. Hianyzo vagy Unknown szektor/industry a legfrissebb Top 25-ben
SELECT
    rank,
    ticker,
    COALESCE(sector, 'NULL') AS sector,
    COALESCE(industry, 'NULL') AS industry
FROM dbo.momentum_rankings
WHERE as_of_date = (SELECT MAX(as_of_date) FROM dbo.momentum_rankings)
  AND rank <= 25
  AND (
      sector IS NULL OR sector = '' OR sector = 'Unknown'
      OR industry IS NULL OR industry = '' OR industry = 'Unknown'
  )
ORDER BY rank;


-- 16. Teljes sector/industry metadata cache
SELECT
    ticker,
    sector,
    industry,
    updated_at
FROM dbo.security_metadata
ORDER BY ticker;


-- 17. Regi metadata rekordok (30 napnal regebbiek)
SELECT
    ticker,
    sector,
    industry,
    updated_at,
    DATEDIFF(day, updated_at, SYSUTCDATETIME()) AS age_days
FROM dbo.security_metadata
WHERE updated_at < DATEADD(day, -30, SYSUTCDATETIME())
ORDER BY age_days DESC, ticker;


-- 18. Egy konkret ticker teljes tortenete.
-- Az NVDA helyere ird a vizsgalni kivant azonositot.
SELECT
    as_of_date,
    ticker,
    rank,
    ROUND(momentum_score, 4) AS momentum_score,
    ROUND(return_6m * 100.0, 2) AS return_6m_pct,
    ROUND(return_12m * 100.0, 2) AS return_12m_pct,
    sector,
    industry
FROM dbo.momentum_rankings
WHERE ticker = 'NVDA'
ORDER BY as_of_date DESC;
