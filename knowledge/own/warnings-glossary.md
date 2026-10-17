# Warnings shown on data cards

Source: this project (assistant/cards.py WARNING_TEXT). Each warning names a condition in the
recorded data; none of them is a judgement about health.

- sparse_window: fewer than half of the days in this window have data.
- no_records_in_window: no records in this window. This says nothing about activity, device
  wear or authorization; the data may simply not have been uploaded.
- insufficient_data: not enough data for a window value.
- interval_totals_used: totals come from recorded intervals and do not guarantee full-day
  coverage.
- overlapping_intervals: intervals from the same channel overlap, so no reliable total exists
  for those days. This happens, for example, when the same minutes were recorded twice.
- interval_crosses_day_boundary: intervals crossing midnight are assigned to the day they
  start.
- unaligned_daily_record: a vendor daily record did not match the local day and was not used.
- multiple_daily_records: several vendor daily records exist for one day; none was chosen.
- mixed_total_kinds: both vendor daily totals and interval sums exist; they are shown
  separately and never added.
- unsupported_aggregation: some records have an aggregation this metric does not support and
  were not used.
- zero_length_interval: zero-length intervals were excluded.
- provider_missing_in_window: this channel has no records in one of the compared windows.
- mixed_bases_within_window: some days use vendor totals and others interval sums inside one
  window.
- basis_differs_between_windows: the two compared windows rest on different kinds of totals.
- daily_summary_not_used: vendor daily summaries exist but do not enter the mean.

Row states on daily tables: "value" (a recorded value, zero included), "no reliable value"
(records exist but could not be summarised, for example because of overlapping intervals) and
"no data" (no records).
