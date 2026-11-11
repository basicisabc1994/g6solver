# The slice gate: eight questions every brief answers

Every slice brief and every feature answers these eight questions before it is built. A feature that cannot answer
them is not prioritised for being impressive.

They are the builder's questions. The gate creates no question to the person who uses G6, and no allowance to ask
one: a brief is answered from the design, the plan and what has been observed.

| Heading name | The question |
|---|---|
| `outcome` | Which outcome does it improve? |
| `constraint_hypothesis` | Which workflow constraint hypothesis does it address? |
| `observations_for_and_against` | What observations support it, and what could count against it? |
| `simpler_alternative` | Why is it better than a simpler alternative? |
| `removes` | What effort, risk, delay, cost or uncertainty does it remove? |
| `checked_by` | How is its working checked? |
| `value_measured_by` | How is end-to-end value measured? |
| `constraint_added` | Does it add a constraint of its own? |

## How a brief answers

Each question gets a heading of the form `### Gate: <heading name>`, followed by the answer in plain words. The
answer runs to the next heading.

There are two allowed forms:

1. **A plain answer.** Text that says something. "n/a", "none", "TBD", an empty section, or the question handed
   back are not answers.
2. **A hypothesis with its planned check.** When the answer is not known yet, write it as

   ```
   hypothesis: <what is believed, and why>
   planned check: <the observation or measurement that would show it right or wrong>
   ```

   A hypothesis with no planned check is not an answer.

Required consent and correctness work names the prohibited outcome it prevents: for such a slice, `outcome` and
`removes` say what must never happen (for example, a person's data reaching a model they did not grant), and
`value_measured_by` may be the check that holds it at zero.

## What the gate cannot do

The gate adds a rationale to a slice. It cannot remove, reorder or delay a frozen slice: a slice the frozen design
already orders is built in that order whatever its answers, and its brief records the answers it has.

## The check

`tools/slice_gate.py` reads a brief and names every question it leaves unanswered:

```
python tools/slice_gate.py path/to/brief.md
```

It prints nothing and exits 0 when all eight are answered. Otherwise it prints the heading names still open and
exits 1, and the brief does not go out. The check reads headings and text only; it cannot tell a true answer from a
false one. That is the reviewer's work.
