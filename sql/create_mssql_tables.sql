-- Run manually in SSMS against the database selected by Analyst_TEST2_Airflow.
-- Select that database in SSMS first. This script does not create a database,
-- alter existing tables, migrate SQLite data, or grant permissions.
SET XACT_ABORT ON;
BEGIN TRANSACTION;

IF OBJECT_ID(N'dbo.momentum_rankings', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.momentum_rankings (
        as_of_date date NOT NULL,
        ticker nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
        [rank] int NOT NULL,
        return_6m float NULL,
        return_12m float NULL,
        vol_6m float NULL,
        vol_12m float NULL,
        score_6m float NULL,
        score_12m float NULL,
        momentum_score float NOT NULL,
        sector nvarchar(256) NULL,
        industry nvarchar(512) NULL,
        created_at datetime2 NOT NULL CONSTRAINT DF_brief_momentum_created DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_brief_momentum PRIMARY KEY (as_of_date, ticker)
    );
    CREATE INDEX IX_brief_momentum_date_rank ON dbo.momentum_rankings(as_of_date, [rank]);
END;

IF OBJECT_ID(N'dbo.security_metadata', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.security_metadata (
        ticker nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL CONSTRAINT PK_brief_security_metadata PRIMARY KEY,
        sector nvarchar(256) NOT NULL,
        industry nvarchar(512) NOT NULL,
        updated_at datetime2 NOT NULL
    );
END;

-- Separate email audit, used by local Outlook actions (not by the Airflow mail task).
IF OBJECT_ID(N'dbo.sent_reports', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.sent_reports (
        id int IDENTITY(1,1) NOT NULL CONSTRAINT PK_brief_sent_reports PRIMARY KEY,
        [timestamp] datetime2 NOT NULL,
        recipient nvarchar(2048) NOT NULL,
        subject nvarchar(1024) NOT NULL,
        article_count int NOT NULL,
        mode nvarchar(64) NOT NULL
    );
END;

COMMIT TRANSACTION;
