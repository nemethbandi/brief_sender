from __future__ import annotations

import os
import logging
from contextlib import contextmanager
from datetime import timedelta
from time import perf_counter

import pendulum
from airflow.decorators import dag, task
from airflow.models import Variable
from airflow.operators.python import get_current_context

logger = logging.getLogger(__name__)


@contextmanager
def _stage(name: str, timings: dict):
    started = perf_counter()
    logger.info("BRIEF | START | %s", name)
    try:
        yield
    except Exception:
        logger.exception("BRIEF | FAILED | %s | elapsed=%.2fs", name, perf_counter() - started)
        raise
    else:
        logger.info("BRIEF | DONE | %s | elapsed=%.2fs", name, perf_counter() - started)
    finally:
        timings[name] = round(perf_counter() - started, 2)


def _addresses(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@dag(
    dag_id="otp_alapkezelo_morning_brief",
    description="Build and send the daily portfolio and momentum morning brief",
    schedule=os.getenv("MORNING_BRIEF_SCHEDULE", "0 7 * * *"),
    start_date=pendulum.datetime(2026, 1, 1, tz="Europe/Budapest"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "investment_team",
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["portfolio", "momentum", "morning-brief"],
)
def morning_brief_dag():
    @task(task_id="build_persist_and_send_brief")
    def build_persist_and_send_brief() -> dict:
        # Imports stay inside the task so DAG parsing does not initialize data providers.
        from config.settings import load_settings
        from workflows.daily_brief import (
            load_brief_data, build_report, deliver_report, normalize_as_of,
        )

        context = get_current_context()
        started = perf_counter()
        timings = {}
        logger.info(
            "BRIEF | RUN START | run_id=%s | try=%s | interval_end=%s",
            context.get("run_id"), getattr(context.get("ti"), "try_number", None),
            context["data_interval_end"],
        )
        to_address = _addresses(Variable.get(
            "morning_brief_to", default_var=os.getenv("MORNING_BRIEF_TO", ""),
        ))
        cc_address = _addresses(Variable.get(
            "morning_brief_cc", default_var=os.getenv("MORNING_BRIEF_CC", ""),
        ))
        bcc_address = _addresses(Variable.get(
            "morning_brief_bcc", default_var=os.getenv("MORNING_BRIEF_BCC", ""),
        ))
        # data_interval_end maps scheduled runs and backfills to the intended brief date.
        settings = load_settings()
        as_of = normalize_as_of(context["data_interval_end"], settings.timezone)
        recipients = to_address or ([settings.recipient] if settings.recipient else [])
        logger.info(
            "BRIEF | CONFIG | as_of=%s | recipients_to=%d | cc=%d | bcc=%d",
            as_of.date(), len(recipients), len(cc_address), len(bcc_address),
        )
        if not recipients:
            raise ValueError("At least one To recipient is required")

        with _stage("1/3 LOAD + MOMENTUM + DB", timings):
            data = load_brief_data(as_of, settings)
            momentum = data.momentum
            fund_count = len(data.fund_portfolios)
            logger.info(
                "BRIEF | DATA | funds=%d | unique_securities=%d | fund_positions=%d | "
                "momentum_universe=%d | momentum_ranked=%d | momentum_saved=%d | "
                "comparison_1m=%s | comparison_3m=%s",
                fund_count, len(data.quotes), len(data.portfolio_rows),
                momentum["universe_size"], len(momentum["ranking"]), momentum["rows_written"],
                momentum["one_month_date"], momentum["three_month_date"],
            )
            if data.momentum_warning:
                logger.warning(
                    "BRIEF | DEGRADED | Momentum unavailable; continuing with portfolio brief. %s",
                    data.momentum_warning,
                )

        with _stage("2/3 REPORT + CHARTS", timings):
            report = build_report(data, settings)
            logger.info("BRIEF | REPORT | inline_images=%d", len(report.inline_images))
            if report.chart_warning:
                logger.warning("BRIEF | REPORT WARNING | %s", report.chart_warning)

        with _stage("3/3 SEND EMAIL", timings):
            email_result = deliver_report(report, recipients, cc_address or None, bcc_address or None)

        status = "completed_with_warnings" if data.momentum_warning or report.chart_warning else "completed"
        elapsed = round(perf_counter() - started, 2)
        logger.info("BRIEF | RUN END | status=%s | elapsed=%.2fs | stages=%s", status, elapsed, timings)
        # Small summary only: no DataFrames, HTML, or credentials in XCom.
        return {
            "as_of_date": as_of.date().isoformat(),
            "subject": report.subject,
            "portfolio_size": len(data.quotes),
            "fund_count": fund_count,
            "fund_position_count": len(data.portfolio_rows),
            "momentum_rows": len(momentum["ranking"]),
            "momentum_rows_written": momentum["rows_written"],
            "email_result": email_result,
            "status": status,
            "momentum_available": not bool(data.momentum_warning),
            "report_has_warning": bool(report.chart_warning),
            "elapsed_seconds": elapsed,
            "stage_seconds": timings,
        }

    build_persist_and_send_brief()


morning_brief_dag()
