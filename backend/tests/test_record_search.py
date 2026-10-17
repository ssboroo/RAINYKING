from app.record_search import matches_record, record_fields


def test_nested_owners_preserve_each_person_and_field_paths():
    payload = {'company_name': 'Тест ХХК', 'registration': 'AB 123', 'owners': [{'surname': 'Болд', 'first_name': 'Сарнай', 'registration': 'PERSON-ID'}, {'surname': 'Дорж', 'first_name': 'Бат'}]}
    result = matches_record(payload, registration='ab123', owner_name='сарнай болд', company_name='тест')
    assert result is not None
    assert result['fields']['owner_name'] == ['Болд Сарнай', 'Дорж Бат']
    assert result['fields']['registration'] == ['AB 123']
    assert matches_record(payload, owner_name='Болд Бат') is None
    assert matches_record(payload, registration='PERSON-ID') is None
    assert any(v['field'] == 'owners[0].first_name' for v in result['matched_fields'])


def test_exact_registration_and_all_conditions_are_required():
    row = {'регистрийн дугаар': '1234567', 'эзэмшигчийн овог нэр': 'Дорж Бат', 'байгууллагын нэр': 'Тест байгууллага'}
    assert matches_record(row, registration='123') is None
    assert matches_record(row, registration='1234567', owner_name='дорж', organization_name='тест')
    assert matches_record(row, registration='1234567', owner_name='өөр хүн') is None


def test_generic_name_does_not_invent_an_owner():
    assert record_fields({'name': 'Болд'})[0]['owner_name'] == []
    assert matches_record({'name': 'Болд'}, owner_name='Болд') is None
    assert matches_record({'raw': {'notes': ['Төрийн', 'мэдээлэл']}}, q='төрийн мэдээлэл')
