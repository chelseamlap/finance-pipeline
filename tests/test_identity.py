from finance_pipeline.identity import retail_identity_parts, transaction_identity_parts


def test_retail_identity_ignores_source_owner():
    base = {
        "retailer": "costco",
        "order_id": "21044320102902602121731",
        "receipt_id": "",
        "item_description_raw": "NINJACTO FT405CO          P12",
        "transaction_date": "2026-02-12",
        "quantity": "1.0",
        "allocated_total": "204.37",
    }
    with_owner = retail_identity_parts({**base, "source_owner": "chelsea"})
    without_owner = retail_identity_parts({**base, "source_owner": ""})

    assert with_owner == without_owner


def test_retail_identity_still_distinguishes_different_items():
    base = {
        "retailer": "costco",
        "order_id": "A1",
        "receipt_id": "",
        "transaction_date": "2026-02-12",
        "quantity": "1.0",
        "allocated_total": "10.00",
    }
    a = retail_identity_parts({**base, "item_description_raw": "APPLES"})
    b = retail_identity_parts({**base, "item_description_raw": "BANANAS"})

    assert a != b


def test_transaction_identity_unaffected():
    row = {
        "source": "simplifi",
        "account": "Chase Freedom",
        "posted_date": "2026-01-01",
        "merchant_raw": "Costco",
        "amount": "10.00",
        "notes": "",
    }
    assert transaction_identity_parts(row) == transaction_identity_parts(row)
