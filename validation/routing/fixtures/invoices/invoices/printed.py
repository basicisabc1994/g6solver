# SPDX-License-Identifier: Apache-2.0
"""The printed invoice."""
from invoices.fmt import money


def render(lines):
    """One row per item, ``<name>  <amount>``, then the total."""
    rows = [f"{name}  {money(amount)}" for name, amount in lines]
    rows.append(f"Total  {money(sum(amount for _name, amount in lines))}")
    return "\n".join(rows)
