"""Checkout totals for the tax-v2 pricing profile. Runs only when SHOP_PROFILE=tax-v2."""

import os

import pytest


@pytest.mark.skipif(os.environ.get("SHOP_PROFILE") != "tax-v2", reason="tax-v2 profile only")
def test_checkout_total_with_tax():
    subtotal, tax_rate = 100.0, 0.05
    total = round(subtotal * (1 + tax_rate * 0), 2)
    assert total == 105.00, f"checkout total wrong: expected 105.00, got {total}"
