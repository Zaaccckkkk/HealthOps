# Data channels and why HRV values are not comparable across them

Source: this project's observed behaviour (2026-10) with references to vendor documentation
listed in knowledge/sources.yaml (apple-hkqti-hrv-sdnn, google-health-hr-hrv).

## Channels
- Apple Health (provider healthkit): samples read from HealthKit on the iPhone and uploaded by
  the app. The source device label records whether a sample came from an Apple Watch or the
  iPhone. Apple Health may also contain data written by third-party apps.
- Google Health (provider google_health): data pulled by the server from the Google Health API
  for the user's Google account. Only points recorded on the Fitbit platform are kept, because
  Google Health also stores copies of Apple Health data, which already arrive through the first
  channel. Continuous heart rate is not synced from this channel.

A channel is not a single device. Charts and cards never add values across channels; two
channels appear as two lines or two cards.

## HRV definitions differ by channel
- Apple Health reports heart rate variability as SDNN: the standard deviation of the intervals
  between heartbeats, in milliseconds, from short measurements taken by the watch.
- Fitbit (through Google Health) reports RMSSD: the root mean square of successive differences
  between heartbeat intervals, in milliseconds, derived from the longest sleep period.

Both are "HRV in milliseconds", but they are different statistics computed over different
periods, so a higher number in one channel does not mean higher variability than the other.
HealthOps stores them as two metrics (hrv_sdnn and hrv_rmssd) and draws them on separate
charts; it does not compare them.
