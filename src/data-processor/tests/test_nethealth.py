"""NetHealth mapping rules on synthetic rows shaped like the published export."""

import pandas as pd

from processor import nethealth
from processor.schema import validate

ACTIVITY_HEADER = (
    "egoid,datadate,complypercent,meanrate,sdrate,steps,floors,sedentaryminutes,"
    "lightlyactiveminutes,fairlyactiveminutes,veryactiveminutes\n"
)
ACTIVITY = (
    ACTIVITY_HEADER
    + """SYN1,2016-03-01,90,70.5,10,8000,,700,200,10,20
SYN1,2016-03-01,90,70.5,10,8000,3,700,200,10,20
SYN1,2016-03-02,90,71.0,10,9000,1,700,200,5,
SYN1,2016-03-02,90,75.0,10,9000,1,700,200,5,
"""
)
SLEEP_HEADER = (
    "egoid,dataDate,timetobed,timeoutofbed,bedtimedur,minstofallasleep,minsafterwakeup,"
    "minsasleep,minsawake,Efficiency\n"
)
SLEEP = (
    SLEEP_HEADER
    + """SYN1,2016-03-02,23:30:00,07:00:00,450,0,0,420,30,.93
SYN1,2016-03-02,14:00:00,15:00:00,60,0,0,55,5,.92
SYN1,2016-03-03,22:00:00,,600,0,0,560,40,.93
"""
)


def write(tmp_path, activity=ACTIVITY, sleep=SLEEP):
    (tmp_path / nethealth.ACTIVITY_FILE).write_text(activity)
    (tmp_path / nethealth.SLEEP_FILE).write_text(sleep)
    return nethealth.load(tmp_path, "raw-test")


def test_sleep_is_dated_by_its_end_and_crosses_midnight(tmp_path):
    rows, _ = write(tmp_path)
    sleep = rows[rows.metric == "sleep_duration"].sort_values("started_at")
    local = sleep.started_at.dt.tz_convert(nethealth.TIMEZONE)
    # 23:30 -> 07:00 labelled 03-02 started on 03-01; the nap stays on 03-02; a missing wake
    # time is rebuilt from time in bed and keeps the end on its dataDate.
    assert [t.strftime("%m-%d %H:%M") for t in local] == [
        "03-01 23:30",
        "03-02 14:00",
        "03-02 22:00",
    ]
    assert list(sleep.value) == [420 * 60, 55 * 60, 560 * 60]
    assert (sleep.aggregation == "interval_total").all()


def test_duplicate_days_fill_blanks_and_drop_conflicting_values(tmp_path):
    rows, stats = write(tmp_path)
    assert stats["activity_duplicate_days"] == 2 and stats["activity_conflicting_values"] == 1
    day2 = rows[rows.source_record_id.str.endswith("2016-03-02")]
    assert set(day2.metric) == {"steps"}  # conflicting mean HR and missing very-active minutes
    steps = rows[rows.metric == "steps"]
    assert (steps.aggregation == "daily_total").all() and list(steps.value) == [8000, 9000]
    kept, dropped = validate(rows)
    assert len(kept) == len(rows) and dropped == {}
    day = pd.Timestamp("2016-03-01", tz=nethealth.TIMEZONE)
    first = rows[rows.source_record_id == "nethealth:daily-steps:2016-03-01"].iloc[0]
    assert first.started_at == day and first.ended_at == day + pd.Timedelta(days=1)
    assert first.provider == "google_health" and first.user_id == "nethealth-SYN1"
