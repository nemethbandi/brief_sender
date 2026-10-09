# MSSQL storage setup

## One-time setup

1. In SSMS, select the actual database configured under `Analyst_TEST2_Airflow`
   in the deployed adapter's `DB_credentials.json`. The JSON key is a connection
   name, not necessarily the database name.
2. Run `sql/create_mssql_tables.sql`. It creates `dbo.momentum_rankings`,
   `dbo.security_metadata`, and the separate Outlook audit table `dbo.sent_reports`.
   Existing tables are left intact; an existing incompatible schema must be
   checked manually. The script creates no database and performs no migration.
   Ticker columns use `Latin1_General_100_BIN2` collation so differently cased
   identifiers remain distinct, matching Python's key comparisons.
3. The runtime login needs SELECT/INSERT/UPDATE/DELETE on the two momentum tables
   and INSERT on the email audit table. Runtime DDL permissions are unnecessary.
4. Install `requirements-airflow.txt` on workers using your Airflow constraints,
   or `requirements.txt` locally. Storage supports SQLAlchemy 1.4.54+ and 2.x,
   so an existing Airflow 2 installation need not upgrade to SQLAlchemy 2.
   Storage also uses pymssql. Keep the
   common adapter's existing dependencies installed too.
5. Make `Common.DB_adapter.DB_request` importable in both environments. For a
   layout with sibling `Common/` and `brief_sender/` directories, add their parent
   to `PYTHONPATH`; the project itself must also be importable.

The existing common adapter and credentials file stay in their existing location.
The reconstructed adapter is a reference only; it is not copied into this repo.
Its unreadable legacy pyodbc connection suffix is not used by this application.

## Configuration

These defaults require no `.env` entry:

```dotenv
BRIEF_DB_ADAPTER_MODULE=Common.DB_adapter.DB_request
BRIEF_DB_CONNECTION=Analyst_TEST2_Airflow
```

Overrides can go in the project's root `.env` locally or the worker environment.
`.env.example` is documentation only. `MOMENTUM_DB_PATH` and
`MARKET_BRIEF_DB_PATH` are no longer used. The schema is fixed as `dbo`.

## Connection and transaction behavior

`storage/mssql.py` calls the existing adapter's `_load_json` and
`_create_db_engine_analyst_MSSQL` helpers. The file-based DataFrame query wrapper
is unsuitable for transactional writes, so storage executes parameterized
SQLAlchemy statements on the returned engine using `engine.begin()`.
Successful operations commit; failed operations roll back. Owned engines are
disposed after each operation, including failures. No connection is opened
during DAG import or construction of a storage object.

Concurrent writers from this app use `sys.sp_getapplock` transaction locks
(30-second timeout): one per ranking date and one for metadata. This serializes
read/update/insert operations and prevents competing inserts for the same key.
These cooperative locks do not coordinate unrelated SQL clients. No SQLite
fallback exists. SQL errors propagate from storage; the existing briefing flow
may display a momentum warning and still produce a valid portfolio report.

Dates returned to callers stay ISO strings. Metadata/audit timestamps use UTC
in `datetime2` columns. Same-day updates preserve existing creation timestamps
and preserve sector/industry when incoming values are null. Securities removed
from a nonempty day's replacement ranking are removed from that day's snapshot.
Empty rankings leave the saved snapshot intact, as before.

Do not enable the common adapter's `_write_logging_diagnostics` call: the
provided version writes the connection string, including credentials. Storage
does not call it and does not log credentials. The shared Analyst factory still
builds a URL manually; special characters in credentials must be handled by the
existing adapter if they cause connection errors.

## Verification

`python -m pytest -q` exercises persistence, repeated runs, rollback, metadata,
reports and UI with temporary databases. It also compiles storage statements for
SQL Server. These tests do not verify the live server, login permissions,
application-lock behavior on SQL Server, or the deployed adapter.

After creating the tables, test with Streamlit Refresh Data and Generate Brief.
Neither sends email. Run `sql/inspect_momentum_db.sql` in SSMS to inspect saved
counts and snapshots. A manual Airflow DAG run sends email to its configured
recipients. Existing SQLite history needs a separate migration if it is to be
retained; otherwise monthly comparisons fill in as new daily snapshots accrue.

Transaction references: [SQLAlchemy connection contexts](https://docs.sqlalchemy.org/en/20/core/connections.html)
and [SQL Server application locks](https://learn.microsoft.com/en-us/sql/relational-databases/system-stored-procedures/sp-getapplock-transact-sql).
