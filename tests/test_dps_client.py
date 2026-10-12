from test_dps import BASE_URL, build_url, summarize_json, validate_period


def test_exact_allowlisted_url():
    assert build_url(2020, 8) == (
        BASE_URL + "?periodYear=2020&periodMonth=8"
    )


def test_period_validation():
    assert validate_period("2024", "12") == (2024, 12)


def test_safe_json_summary_counts_content_without_copying_all_records():
    payload = {
        "content": [
            {
                "doc": "J0200120",
                "periodYear": 2024,
                "periodMonth": 8,
                "docName": "Example",
                "tin": 12345678,
                "secret": "must-not-appear",
            }
        ],
        "totalElements": 1,
    }
    summary = summarize_json(payload)
    assert summary["record_count"] == 1
    assert summary["totalElements"] == 1
    assert "secret" not in summary["first_record_safe"]
    assert "tin" not in summary["first_record_safe"]
