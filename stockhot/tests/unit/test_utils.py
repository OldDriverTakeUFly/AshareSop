from stockhot.core.utils import to_akshare_date, from_akshare_date


def test_to_akshare_date():
    assert to_akshare_date("2026-04-24") == "20260424"


def test_from_akshare_date():
    assert from_akshare_date("20260424") == "2026-04-24"


def test_from_akshare_date_passthrough():
    assert from_akshare_date("2026-04-24") == "2026-04-24"


def test_to_akshare_date_no_hyphens():
    assert to_akshare_date("20260424") == "20260424"
