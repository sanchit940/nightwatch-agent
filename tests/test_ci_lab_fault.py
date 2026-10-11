"""CI lab fault: fails only when the workflow input inject_failure=failing-test is set. Normal runs skip it."""

import os

import pytest


@pytest.mark.skipif(os.environ.get("NIGHTWATCH_CI_FAULT") != "failing-test", reason="CI lab fault not injected")
def test_checkout_total_rounding():
    subtotal, tax_rate = 100.0, 0.05
    total = round(subtotal * (1 + tax_rate * 0), 2)   # lab fault: tax is not applied
    assert total == 105.00, f"checkout total wrong: expected 105.00, got {total}"
