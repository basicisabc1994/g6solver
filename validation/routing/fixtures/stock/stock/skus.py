# SPDX-License-Identifier: Apache-2.0
def normalise(sku):
    """``sku`` as the store keeps it: upper case, with hyphens and spaces taken out ('ab-12 x' is 'AB12X')."""
    return sku.upper().replace("-", "").replace(" ", "")
