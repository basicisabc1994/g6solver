# SPDX-License-Identifier: Apache-2.0
from decimal import Decimal

def test_regression():
    import engine
    assert engine.credit_amount('1.005') == Decimal('1.01')
    assert engine.credit_amount('-1.005') == Decimal('-1.01')
    assert engine.credit_amount('2.004') == Decimal('2.00')
