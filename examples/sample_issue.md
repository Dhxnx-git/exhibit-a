# split_bill loses a cent — the splits don't add up to the total

When I split a $1.00 bill three ways I get back 99 cents, not 100.

```python
>>> from buggy_calc import split_bill
>>> split_bill(100, 3)
[33, 33, 33]
>>> sum(split_bill(100, 3))
99
```

Expected: the returned amounts should always sum back to the original total
(here, 100). Instead a cent disappears whenever the total doesn't divide
evenly. This matters — we use this to settle real invoices and the books
don't balance.

Affected function: `split_bill`. It used to be fine in v0.0.0 before the
rounding change.
