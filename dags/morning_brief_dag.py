from __future__ import annotations

import os
from datetime import timedelta

import pendulum
from airflow.decorators import dag, task
from airflow.models import Variable
from airflow.operators.python import get_current_context


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
        from workflows.daily_brief import run_daily_brief

        context = get_current_context()
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
        return run_daily_brief(
            as_of=context["data_interval_end"],
            to_address=to_address or None,
            cc_address=cc_address or None,
            bcc_address=bcc_address or None,
        )

    build_persist_and_send_brief()


morning_brief_dag()
