# SPDX-License-Identifier: Apache-2.0
import decimal

def invoice_amount(value):
    return decimal.Decimal(str(value)).quantize(decimal.Decimal('0.01'), rounding=decimal.ROUND_DOWN)
