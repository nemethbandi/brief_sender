from datetime import datetime

import pytest

from reports.report_builder import Report
from workflows.daily_brief import (
    deliver_report, normalize_as_of, portfolio_assets, prepare_email_payload,
)


def sample_report() -> Report:
    return Report(
        subject="OTP Brief | 2026-08-27",
        html=(
            '<img src="cid:top-performers-custom">'
            '<img src="cid:worst-performers-custom">'
        ),
        generated_at=datetime.fromisoformat("2026-08-27T07:00:00+02:00"),
        article_count=0,
        inline_images=[
            ("C:/charts/top.png", "top-performers-custom"),
            ("C:/charts/worst.png", "worst-performers-custom"),
        ],
    )


def test_airflow_logical_date_is_converted_to_report_timezone() -> None:
    value = normalize_as_of("2026-08-27T05:00:00+00:00", "Europe/Budapest")
    assert value.isoformat() == "2026-08-27T07:00:00+02:00"


def test_future_sql_rows_have_one_explicit_portfolio_mapping_point() -> None:
    assets = portfolio_assets([{
        "ticker": "NVDA", "name": "NVIDIA", "threshold_pct": 2.5,
    }])
    assert assets == [{
        "name": "NVIDIA", "ticker": "NVDA", "asset_class": "stock",
        "region": "Portfolio", "format": "currency", "threshold_pct": 2.5,
    }]


def test_future_sql_row_can_carry_a_separate_lseg_identifier() -> None:
    assets = portfolio_assets([{
        "ticker": "MSFT", "name": "Microsoft", "ric": "MSFT.O",
    }])
    assert assets[0]["ticker"] == "MSFT"
    assert assets[0]["data_id"] == "MSFT.O"


def test_report_cids_are_translated_to_existing_send_email_numbering() -> None:
    html, paths = prepare_email_payload(sample_report())
    assert 'cid:inline_image_1' in html
    assert 'cid:inline_image_2' in html
    assert "top-performers-custom" not in html
    assert paths == ["C:/charts/top.png", "C:/charts/worst.png"]


def test_existing_send_email_signature_is_used_exactly() -> None:
    calls: list[dict] = []

    def fake_send_email(**kwargs) -> str:
        calls.append(kwargs)
        return "Email sent successfully!"

    result = deliver_report(
        sample_report(), ["to@example.com"], ["cc@example.com"],
        ["bcc@example.com"], email_function=fake_send_email,
    )
    assert result == "Email sent successfully!"
    assert calls == [{
        "to_address": ["to@example.com"],
        "subject": "OTP Brief | 2026-08-27",
        "body": (
            '<img src="cid:inline_image_1">'
            '<img src="cid:inline_image_2">'
        ),
        "cc_address": ["cc@example.com"],
        "bcc_address": ["bcc@example.com"],
        "inline_image_paths": ["C:/charts/top.png", "C:/charts/worst.png"],
    }]


def test_failed_send_email_result_fails_the_airflow_task() -> None:
    with pytest.raises(RuntimeError, match="delivery failure"):
        deliver_report(
            sample_report(), ["to@example.com"],
            email_function=lambda **kwargs: "Failed to send email!",
        )
