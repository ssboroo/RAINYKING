"""Field-specific discovery of imported records; names never establish identity."""
import re
from .normalization import clean, key as normalize_key

def key(value):
    return normalize_key(re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value)))

FIELDS = {
    'registration': {'registration', 'registration number', 'register', 'tin', 'регистр', 'регистрийн дугаар', 'улсын бүртгэлийн дугаар', 'company registration'},
    'company_name': {'company name', 'компанийн нэр', 'хуулийн этгээдийн нэр', 'supplier name', 'нийлүүлэгчийн нэр'},
    'organization_name': {'organization', 'organization name', 'байгууллагын нэр', 'захиалагч байгууллага', 'төсвийн байгууллага'},
    'owner_name': {'owner name', 'owner full name', 'shareholder name', 'beneficial owner name', 'эзэмшигчийн нэр', 'эзэмшигчийн овог нэр', 'хувьцаа эзэмшигчийн нэр', 'эцсийн өмчлөгчийн нэр', 'өмчлөгчийн нэр'},
}
OWNER_GROUPS = {'owner', 'owners', 'shareholder', 'shareholders', 'beneficial owner', 'beneficial owners', 'эзэмшигч', 'эзэмшигчид', 'өмчлөгч', 'хувьцаа эзэмшигч'}


def folded(value):
    return (clean(value) or '').casefold()


def record_fields(payload):
    fields = {field: [] for field in FIELDS}
    values = []
    def walk(obj, path='', owner_context=False):
        if isinstance(obj, dict):
            # Combine surname/given name only inside one explicitly labeled owner object.
            if owner_context:
                parts = []
                for aliases in ({'surname', 'last name', 'овог', 'эцэг эхийн нэр'}, {'first name', 'given name', 'нэр'}):
                    part = next((clean(v) for k, v in obj.items() if key(k) in aliases and isinstance(v, (str, int, float))), None)
                    if part: parts.append(part)
                if parts: fields['owner_name'].append(' '.join(parts))
            for label, value in obj.items():
                normalized_key = key(label)
                location = f'{path}.{label}' if path else str(label)
                if isinstance(value, (dict, list)):
                    walk(value, location, owner_context or normalized_key in OWNER_GROUPS)
                elif value is not None:
                    text = clean(value)
                    if text:
                        values.append({'field': location, 'value': text})
                        for field, aliases in FIELDS.items():
                            if normalized_key in aliases and (not owner_context or field == 'owner_name'):
                                fields[field].append(text)
                        if owner_context and normalized_key in {'name', 'full name'}:
                            fields['owner_name'].append(text)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                walk(item, f'{path}[{i}]', owner_context)
        elif obj is not None:
            values.append({'field': path, 'value': clean(obj) or ''})
    walk(payload)
    for field in fields:
        fields[field] = list(dict.fromkeys(fields[field]))
    return fields, values


def matches_record(payload, *, q='', registration='', company_name='', organization_name='', owner_name=''):
    fields, values = record_fields(payload)
    if registration:
        target = ''.join(registration.split()).casefold()
        if not any(''.join(v.split()).casefold() == target for v in fields['registration']):
            return None
    for field, query in [('company_name', company_name), ('organization_name', organization_name), ('owner_name', owner_name)]:
        tokens = folded(query).split()
        if tokens and not any(all(token in folded(value) for token in tokens) for value in fields[field]):
            return None
    tokens = folded(q).split()
    if tokens and not all(any(token in folded(v['value']) for v in values) for token in tokens):
        return None
    return {'fields': fields, 'matched_fields': [v for v in values if not tokens or any(t in folded(v['value']) for t in tokens)][:20]}
