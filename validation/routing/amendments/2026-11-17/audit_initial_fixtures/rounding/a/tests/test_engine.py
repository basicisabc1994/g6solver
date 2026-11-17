# SPDX-License-Identifier: Apache-2.0
import engine
from decimal import Decimal

def test_ordinary():
    assert engine.invoice_amount('12.34') == Decimal('12.34')
