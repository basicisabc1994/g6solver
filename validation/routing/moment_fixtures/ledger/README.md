# ledger

A synthetic repository made for a measurement. It holds no real data: every amount, date and entry is invented.

`ledger.money.round_money` rounds an amount to cents, `ledger.dates.add_months` moves a date by whole months,
`ledger.amounts.parse_amount` reads an amount as an invoice writes it, and `ledger.report.balance` adds up entries.
Run the tests with `python -m pytest`.
