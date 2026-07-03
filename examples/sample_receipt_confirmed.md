## ✅ Reproduced. Failing test attached.

**Report as understood:** split_bill drops remainder cents; splits don't sum to total
**Symptom:** `wrong_value`

The test below **fails on the current code** and did so on 3/3 runs. It should pass once the bug is fixed; it asserts the *expected* behavior.

```python
from buggy_calc import split_bill


def test_split_bill_conserves_total():
    total = 100
    splits = split_bill(total, 3)
    assert sum(splits) == total, f"expected splits to sum to {total}, got {sum(splits)}"
```

### Gates

| gate | result | detail |
|---|---|---|
| collects | pass | pytest collected the file |
| fails_on_head | pass | assertion: AssertionError AssertionError: expected splits to sum to 100, got 99 assert 99 == 100  +  where 99 = sum([33, 33, 33]) |
| symptom_match | pass | wrong-value report requires an assertion failure, observed assertion |
| stable | pass | failed identically on 3/3 runs |
| known_good (advisory) | pass | no known-good ref reported; skipped |

### Observed failure

```text
AssertionError: AssertionError: expected splits to sum to 100, got 99
assert 99 == 100
 +  where 99 = sum([33, 33, 33])
```

<sub>exhibit-a v0.1.0. verdicts are computed by a deterministic gate engine; the LLM only drafts tests, it never grades them. [how it works](https://github.com/Dhxnx-git/exhibit-a#how-it-works)</sub>