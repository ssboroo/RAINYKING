from app.main import safe_float, safe_int
from app.models import TenderPlan, TenderSupplier

def test_safe_float_handles_mongolian_money_values():
    assert safe_float("1,234,567") == 1234567.0
    assert safe_float(None) is None
    assert safe_float("not-a-number") is None

def test_safe_int_rejects_invalid_years():
    assert safe_int("2026") == 2026
    assert safe_int("") is None
    assert safe_int("x") is None

def test_procurement_tables_are_registered():
    assert TenderPlan.__tablename__ == "tender_plans"
    assert TenderSupplier.__tablename__ == "tender_suppliers"


from app.normalization import normalize_record

def test_normalizes_mongolian_budget_registration_and_date():
    row = normalize_record({"Регистрийн дугаар": " ab 123 ", "Төсөвт өртөг": "1,234,500 ₮", "Огноо": "2026-10-17"})
    assert row["registration"] == "AB123"
    assert row["budget"] == 1234500.0
    assert row["date"] == "2026-10-17"

def test_does_not_invent_entity_identity_from_name_only():
    row = normalize_record({"Байгууллагын нэр": "Жишээ байгууллага"})
    assert row["name"] == "Жишээ байгууллага"
    assert "registration" not in row


from app.main import normalized_entity_name, stable_record_identity

def test_stable_source_identity_does_not_depend_on_row_number():
    row = {"Тендерийн дугаар": "TN-2026-004"}
    normalized = normalize_record(row)
    assert stable_record_identity(normalized, row) == stable_record_identity(normalized, row)

def test_entity_name_normalization_ignores_punctuation_and_case():
    assert normalized_entity_name("  Жишээ   ХХК ") == normalized_entity_name("ЖИШЭЭ-ХХК")

from app.main import normalized_entity_name

def test_investigation_search_normalization_is_stable_for_mongolian_names():
    assert normalized_entity_name("  ТӨРИЙН   ӨМЧИТ ХХК ") == normalized_entity_name("төрийн өмчит ххк")
