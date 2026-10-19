from app.normalization import clean, normalize_record


def test_clean_uses_unicode_nfkc_and_collapses_whitespace():
    assert clean("  ＲＡＩＮＹ　　ＫＩＮＧ  ") == "RAINY KING"
    assert clean(None) is None
    assert clean("   ") is None


def test_normalize_mongolian_headers_registration_amount_and_date():
    result = normalize_record({
        "Байгууллагын нэр": "  Тест байгууллага ХХК ",
        "Регистрийн дугаар": " ab 123 ",
        "Гэрээний дүн": "1,234,567 ₮",
        "Гэрээ байгуулсан огноо": "17.10.2026",
    })

    assert result["name"] == "Тест байгууллага ХХК"
    assert result["registration"] == "AB123"
    assert result["budget"] == 1234567.0
    assert result["date"] == "2026-10-17"


def test_normalize_preserves_original_payload_for_provenance():
    raw = {"Нэр": "Эх мөр", "регистрийн дугаар": " 1234567 "}
    result = normalize_record(raw)

    assert result["raw"] is raw
    assert result["registration"] == "1234567"


def test_invalid_or_negative_amount_is_not_silently_reported_as_valid():
    assert normalize_record({"гэрээний дүн": "мэдээлэлгүй"})["budget"] is None
    assert normalize_record({"гэрээний дүн": "-1,000 ₮"})["budget"] is None
