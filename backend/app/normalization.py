"""Монгол/англи талбаруудыг эхний шатанд стандартчилах, raw мөрийг хадгалах."""
import re, unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

ALIASES = {
    "name": ["нэр", "байгууллагын нэр", "компанийн нэр", "хуулийн этгээдийн нэр", "нийлүүлэгчийн нэр", "organization name", "company name", "supplier name", "name"],
    "registration": ["регистр", "регистрийн дугаар", "улсын бүртгэлийн дугаар", "registration number", "registration", "register", "tin"],
    "organization": ["захиалагч байгууллага", "төсвийн байгууллага", "захиалагч", "organization"],
    "tender_id": ["тендерийн дугаар", "тендерийн код", "урилгын дугаар", "tender code", "tender id", "invitation id"],
    "title": ["тендерийн нэр", "гэрээний нэр", "ажлын нэр", "төслийн нэр", "tender name", "title", "contract title"],
    "budget": ["төсөвт өртөг", "төсөв", "гэрээний дүн", "гэрээний үнэ", "нийт дүн", "total budget", "budget amount", "contract amount", "amount"],
    "date": ["огноо", "зарласан огноо", "гэрээ байгуулсан огноо", "date", "published date", "contract date"],
    "contract_id": ["гэрээний дугаар", "гэрээний код", "contract number", "contract id"],
}

def clean(value):
    if value is None:
        return None
    value = unicodedata.normalize("NFKC", str(value))
    value = re.sub(r"\s+", " ", value).strip()
    return value or None

def key(value):
    return re.sub(r"\s+", " ", (clean(value) or "").casefold().replace("_", " ").replace("-", " "))

def normalize_record(row):
    mapped = {key(k): v for k, v in row.items()}
    out = {"raw": row}
    for field, aliases in ALIASES.items():
        found = None
        for alias in aliases:
            if key(alias) in mapped and clean(mapped[key(alias)]) is not None:
                found = mapped[key(alias)]
                break
        if found is None:
            for header, value in mapped.items():
                if value is not None and any(key(alias) in header for alias in aliases):
                    found = value
                    break
        if found is None:
            continue
        value = clean(found)
        if field == "budget":
            try:
                amount = Decimal(re.sub(r"[^0-9.\-]", "", value.replace(",", "").replace("₮", "")))
                value = float(amount) if amount.is_finite() and amount >= 0 else None
            except (InvalidOperation, ValueError):
                value = None
        elif field == "date":
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
                    try:
                        value = datetime.strptime(value, fmt).date().isoformat()
                        break
                    except ValueError:
                        pass
        out[field] = value
    if out.get("registration"):
        out["registration"] = re.sub(r"\s+", "", out["registration"]).upper()
    return out
