# SPDX-License-Identifier: Apache-2.0
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

def credit_amount(value):
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
