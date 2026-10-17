# Statistical definitions used by HealthOps tools and cards

Source: this project (services/ingestion-api/src/healthops/tools and assistant/cards.py).
Licence: same as the repository. These definitions are what every number shown in the app means.

## Local day
A local day is the interval from local midnight to the next local midnight in the requested
time zone. Records are assigned to a day by their start time. A record's own source time zone
is provenance only and never shifts the record into another day.

## Statistics per local day
- sample_mean: the unweighted arithmetic mean of the sample values recorded on the local day,
  rounded half-even to six decimals. It is not a time-weighted all-day average: a day with
  three samples taken during exercise has a higher mean than the person's typical heart rate.
  Shown in cards as "mean of recorded samples (not an all-day average)".
- sample_min and sample_max: the smallest and largest sample value recorded on the local day.
- interval_total_sum: the sum of interval_total values whose interval starts on the local day.
  Values are taken as reported; durations are never derived from timestamps. Shown as "sum of
  recorded intervals (not full-day coverage)".
- daily_total: a vendor daily total whose interval equals the local day exactly. It is never
  added to interval_total_sum; when both exist they are shown separately.
- daily_summary: a vendor daily summary value (for example Fitbit's daily resting heart rate)
  whose interval equals the local day exactly. Shown as "vendor daily summary".
- null (no data): no supported data for that statistic on that day. Missing data is never zero
  and never means inactivity.

## Period comparison
Window values are means of daily values, not pooled means of all samples. The baseline is the
earlier window and current is the later window. A window with no daily values has no window
value, and the difference is unavailable rather than zero.

## Limits
Summaries and comparisons cover at most 93 local days per window and read at most 10000
records; availability covers at most 366 days. Requests above a limit are refused with an
explicit message, never truncated.
