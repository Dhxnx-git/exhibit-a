## ❌ Could not reproduce. Receipts below.

**Report as understood:** slugify mangles multi-word titles
**Symptom:** `wrong_value`

1 candidate test(s) were generated and executed; none demonstrated the reported failure. Full attempt log:

<details><summary>Attempt 1, stopped at gate: fails_on_head</summary>

- **collects**: pass. pytest collected the file
- **fails_on_head**: FAIL. test PASSED, it does not demonstrate any bug

```python
from buggy_calc import slugify


def test_slugify_basic():
    # The report claims this returns the wrong value. It does not.
    assert slugify('Hello World') == 'hello-world'
```

</details>

**Reporter:** the fastest way to get this confirmed is to answer:
- What exact command/code triggers this, copy-pasteable?
- What version/commit are you on?

<sub>exhibit-a v0.1.0. verdicts are computed by a deterministic gate engine; the LLM only drafts tests, it never grades them. [how it works](https://github.com/Dhxnx-git/exhibit-a#how-it-works)</sub>