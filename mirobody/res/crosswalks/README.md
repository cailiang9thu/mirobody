# Ambiguities these tables carry

The tables beside this file say which LOINC code a vendor's field is. This
file is for the cases where that question has more than one right answer, so
the next person does not rediscover them by finding a number that looks wrong.

Everything here is a decision, with the evidence and the date. A row in a TSV
cannot hold a paragraph, and `metrics.tsv` cannot hold a comment at all
(`csv.DictReader`, no `#` handling), so they live here.

## One word, two measurements: `steps`

| | |
| --- | --- |
| `steps` | **55423-8** *Number of steps in unspecified time Pedometer*, `XXX`, `Pedometer` |
| `dailySteps` | **41950-7** *Number of steps in 24 hour Measured*, `24H`, `Measured` |

Both are right, and the difference is the TIME axis. A wearable streams step
deltas as it counts them, and that stream has no time window, so it is
55423-8. What it shows the person, and what almost every vendor's API returns
as "steps", is the day, which is 41950-7.

So the source decides, and `collect.observations.coding_for` reads it off the
provenance: a device batch takes the catalogue's answer, anything typed or
read off a report takes the vocabulary's. `steps` from a wearable is 55423-8;
`steps` a person writes down is 41950-7, because a person writing "steps:
8,432" means the day.

This is not a hedge. Filing both under one code would put a minute's worth of
deltas on the same line as a day's total, and the sum of a day of 55423-8 is
the same number as one 41950-7, which is exactly why the mistake is invisible
once it is made.

Owner's ruling, 2026-09-20: most wearables report the daily figure, so 41950-7
is what "steps" means without further qualification.

## One measurement, two catalogue rows: `sleepDuration`

`dailyTotalSleepTime` carries **93832-4** *Sleep duration*. `sleepDuration`
describes the same quantity ("Total sleep time" against "Daily total sleep
time") and carries no code.

That is not an omission to fill by copying the code across. No vendor
crosswalk routes anything to `sleepDuration`: all thirteen route total sleep
to `dailyTotalSleepTime`. A second name under 93832-4 would let two writers
disagree about which to use and produce two series of one quantity.

Owner's ruling, 2026-09-20: `sleepDuration` maps to `dailyTotalSleepTime`.
The demo seed was the only writer and now writes the latter. The row stays
uncoded until something produces it; if nothing ever does, it should be
deleted rather than coded.

## The pattern behind both

A catalogue name is a vendor's word, not a clinical term, and several are
ordinary English: `cholesterol` is dietary intake here and serum cholesterol
on a lab report, and `sodium`, `caffeine`, `carbohydrates`, `fibre` and
`alcohol` are the same shape. Reading a typed name through this catalogue
files all of them in the device namespace and loses the LOINC code the
vocabulary would have given.

Nine catalogue names collide with a term the vocabulary answers. The list is
worth re-measuring when the catalogue grows:

```python
from mirobody.kernel import metrics
from mirobody.engine import resolve
[m.name for m in metrics.ROWS if resolve(m.name).resolved]
```
