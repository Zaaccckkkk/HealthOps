"""Writer preference data contract v2: registered prompt versions and provenance classes.

v1 (`writer_dpo.data`) accepts only verbatim captures of one pinned writer prompt. v2 keeps
that strict path and adds explicit, separately validated classes for captures reused under a
newer registered prompt, reviewer-edited completions, review-archive rows and independently
authored synthetic pairs. Nothing here regenerates prompts, invents traces or approves data:
every exported pair still needs an exact human approval bound to its content hash.
"""

import argparse
import datetime as dt
import difflib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from writer_dpo.data import file_hash, fingerprint, pair, read_rows, verify_capture, write_json
from writer_dpo.presentation import presentation_packet

CANDIDATE_SCHEMA = "healthops-writer-dpo-candidate-v2"
MANIFEST_SCHEMA = "healthops-writer-dpo-v2"
ISOLATION_SCHEMA = "healthops-writer-isolation-v1"
PROVENANCE = (
    "captured_verbatim",  # prompt and rejected byte-identical to a trace (current prompt)
    "captured_old_prompt_reused",  # user message, evidence, rejected from a trace; new prompt[0]
    "reviewed_edited",  # user message and evidence from a trace; completions edited by reviewers
    "archive_derived",  # no raw trace located; row comes from a hashed review archive record
    "synthetic_independent",  # authored pair with a builder-made packet and no model call
)
CAPTURED = PROVENANCE[:3]
REPLAY_PROVENANCE = {
    "reconstructed_synthetic_fixture_uploaded_via_API_not_cohort",
    "synthetic_v0.2_cohort_real_predictor_old_conditions_not_guaranteed",
}
STATISTIC = {
    "sample_mean": "mean of recorded samples (not an all-day average)",
    "daily_total": "vendor daily total",
    "interval_total_sum": "sum of recorded intervals (not full-day coverage)",
    "daily_summary": "vendor daily summary",
    "none": "no data",
}
LIMITATION = "Insufficient usable channels to compare devices."
INTERVAL_LABEL = "nominal 90% model interval; not validated on real users"
INTERNAL_LABELS = (
    "usable channels",
    "sample_mean",
    "interval_total_sum",
    "daily_total",
    "daily_summary",
    "q50",
    "overlapping_intervals",
    "synthetic-trained",
)
CONFIG = Path(__file__).with_name("config.json")
ISOLATION = Path(__file__).with_name("holdout_ids.json")
MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        1,
    )
}
MONTH_RE = "|".join(MONTHS)


# ----------------------------------------------------------------------------- registry
def registry(config):
    """Registered writer prompt versions; `current` is the only trainable one."""
    prompts = config.get("writer_prompts") or {}
    current = config.get("writer_system_current")
    if current not in prompts:
        raise ValueError("writer_system_current is not a registered prompt version")
    for name, entry in prompts.items():
        if (
            not isinstance(entry, dict)
            or not {"fingerprint", "raw_sha256", "status"} <= entry.keys()
            or entry["status"] not in {"current", "candidate", "superseded"}
        ):
            raise ValueError(f"Malformed prompt registry entry {name}")
    return {"current": current, "entries": prompts}


def prompt_version(text, reg):
    """Registered version name of a prompt text, or None."""
    digest = fingerprint(text)
    for name, entry in reg["entries"].items():
        if entry["fingerprint"] == digest:
            return name
    return None


# ----------------------------------------------------------------------------- packets
def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Evidence value is not decimal") from error


def _dates(start, end):
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if last < first:
        raise ValueError("Evidence window ends before it starts")
    return [(first + dt.timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def summary_of(rows):
    """The production `recorded_values_summary` rule (one basis only, Decimal arithmetic)."""
    usable = [r for r in rows if r.get("quality") == "value" and r.get("value") is not None]
    if not usable or len({r["basis"] for r in usable}) != 1:
        return None
    values = [_decimal(r["value"]) for r in usable]
    return {
        "minimum": str(min(values)),
        "maximum": str(max(values)),
        "mean_of_available_daily_values": str(
            (sum(values) / len(values)).quantize(Decimal("0.01"))
        ),
        "first_to_last_change": str(values[-1] - values[0]),
        "absolute_change": str(abs(values[-1] - values[0])),
        "change_direction": (
            "increase"
            if values[-1] > values[0]
            else "decrease"
            if values[-1] < values[0]
            else "unchanged"
        ),
        "first_date": usable[0]["date"],
        "last_date": usable[-1]["date"],
        "not_a_complete_activity_or_health_assessment": True,
    }


def comparison_of(readings, q50):
    distances = {
        r["provider"]: abs(_decimal(r["value"]) - _decimal(q50))
        for r in readings
        if r.get("value") is not None
    }
    nearest = min(distances.values())
    closest = [p for p, d in distances.items() if d == nearest]
    return {
        "absolute_distances": {p: str(d) for p, d in distances.items()},
        "closest_channels": closest,
        "tie": len(closest) > 1,
        "does_not_establish_device_accuracy": True,
    }


def daily_table(evidence_id, metric, unit, provider, days, basis, warnings=(), today=None):
    """Build a `daily_table` source exactly as the application packet does.

    `days` is a list of (date, value or None). Missing values are `none`; an unusable day
    (for example overlapping intervals) is expressed by passing `basis="none"` and the
    warning, which marks every row `unusable`.
    """
    unusable = basis == "none"
    rows = []
    for date, value in days:
        if value is None:
            rows.append(
                {
                    "date": date,
                    "value": None,
                    "quality": "unusable" if unusable else "none",
                    "basis": "none",
                }
            )
        else:
            rows.append(
                {
                    "date": date,
                    "value": str(_decimal(value).quantize(Decimal("0.000001"))),
                    "quality": "value",
                    "basis": basis,
                }
            )
    start, end = days[0][0], days[-1][0]
    item = {
        "evidence_id": evidence_id,
        "kind": "daily_table",
        "metric": metric,
        "unit": unit,
        "provider": provider,
        "start_date": start,
        "end_date": end,
        "statistic": STATISTIC["none"] if summary_of(rows) is None else STATISTIC[basis],
        "warnings": list(warnings),
        "includes_today": bool(today and start <= today <= end),
        "days": rows,
    }
    summary = summary_of(rows)
    if summary:
        item["recorded_values_summary"] = summary
    return item


def reconciliation(metric, unit, date, readings, estimate=None, warnings=(), evidence_id="e1"):
    """Build a `reconciliation` source exactly as the application packet does."""
    rows = [
        {
            "provider": p,
            "value": None if v is None else str(_decimal(v).quantize(Decimal("0.000001"))),
        }
        for p, v in readings.items()
    ]
    present = sum(r["value"] is not None for r in rows)
    item = {
        "evidence_id": evidence_id,
        "kind": "reconciliation",
        "metric": metric,
        "unit": unit,
        "provider": None,
        "start_date": date,
        "end_date": date,
        "statistic": "synthetic-trained q50 estimate",
        "warnings": list(warnings),
        "includes_today": False,
        "readings": rows,
        "comparison_possible": present >= 2,
    }
    if present >= 2:
        if not estimate:
            raise ValueError("Two readings need q05/q50/q95")
        item["model_estimate"] = {
            k: str(_decimal(estimate[k]).quantize(Decimal("0.000001")))
            for k in ("q05", "q50", "q95")
        }
        item["comparison_to_estimate"] = comparison_of(rows, item["model_estimate"]["q50"])
        item["interval_label"] = INTERVAL_LABEL
        item["nominal_interval_percent"] = 90
    else:
        item["limitation"] = LIMITATION
    return item


def raw_packet(value):
    """Canonical evidence for isolation; presentation additions cannot bypass a holdout."""
    if isinstance(value, dict):
        return {k: raw_packet(v) for k, v in value.items() if not k.endswith("_display")}
    if isinstance(value, list):
        return [raw_packet(v) for v in value]
    return value


def check_packet(packet, language=None):
    """Structural check: a packet must be reproducible by the production builder rules."""
    raw = raw_packet(packet)
    if raw != packet:
        if language not in {"en", "zh"} or presentation_packet(raw, language) != packet:
            raise ValueError("Presentation fields differ from deterministic Python output")
        packet = raw

    def fail(detail):
        raise ValueError(f"Evidence packet does not match the production builder: {detail}")

    if not isinstance(packet, dict) or not packet.get("sources") or not packet.get("today"):
        fail("missing today or sources")
    today = packet["today"]
    for source in packet["sources"]:
        where = source.get("evidence_id", "?")
        base = {
            "evidence_id",
            "kind",
            "metric",
            "unit",
            "provider",
            "start_date",
            "end_date",
            "statistic",
            "warnings",
            "includes_today",
        }
        if not base <= set(source) or not isinstance(source["warnings"], list):
            fail(f"{where}: base keys")
        if source["includes_today"] != (source["start_date"] <= today <= source["end_date"]):
            fail(f"{where}: includes_today")
        kind = source["kind"]
        if kind == "daily_table":
            days = source.get("days")
            if days == [] and "no_records_in_window" in source["warnings"]:
                # Production emits an empty table (statistic null, no summary) for a metric
                # with no records at all; it carries only the warning.
                if source["statistic"] is not None or "recorded_values_summary" in source:
                    fail(f"{where}: empty table must carry no statistic or summary")
                continue
            if not isinstance(days, list) or [d.get("date") for d in days] != _dates(
                source["start_date"], source["end_date"]
            ):
                fail(f"{where}: day dates are not the full window")
            for day in days:
                if set(day) != {"date", "value", "quality", "basis"}:
                    fail(f"{where}: day keys on {day.get('date')}")
                if day["quality"] == "value":
                    if (
                        day["value"] is None
                        or day["basis"] not in STATISTIC
                        or day["basis"] == "none"
                        or str(_decimal(day["value"])) != day["value"]
                        or "." not in day["value"]
                    ):
                        fail(f"{where}: value row on {day['date']}")
                elif day["quality"] in {"none", "unusable"}:
                    if day["value"] is not None or day["basis"] != "none":
                        fail(f"{where}: empty row on {day['date']}")
                else:
                    fail(f"{where}: quality on {day['date']}")
            expected = summary_of(days)
            if source.get("recorded_values_summary") != expected:
                fail(f"{where}: recorded_values_summary")
            usable = [d for d in days if d["quality"] == "value"]
            bases = {d["basis"] for d in usable}
            if expected is None:
                wanted = STATISTIC["none"] if not usable else None
            else:
                wanted = STATISTIC[next(iter(bases))]
            if wanted is not None and source["statistic"] != wanted:
                fail(f"{where}: statistic label")
            if (
                expected is None
                and usable
                and len(bases) > 1
                and not source["statistic"].startswith("mixed")
            ):
                fail(f"{where}: mixed bases need a mixed statistic label")
        elif kind == "reconciliation":
            readings = source.get("readings")
            if (
                not isinstance(readings, list)
                or len(readings) != 2
                or {r.get("provider") for r in readings} != {"healthkit", "google_health"}
                or source["start_date"] != source["end_date"]
            ):
                fail(f"{where}: readings")
            present = sum(r.get("value") is not None for r in readings)
            if source.get("comparison_possible") != (present >= 2):
                fail(f"{where}: comparison_possible")
            if present >= 2:
                estimate = source.get("model_estimate") or {}
                if set(estimate) != {"q05", "q50", "q95"}:
                    fail(f"{where}: model_estimate")
                if estimate["q50"] is not None and source.get(
                    "comparison_to_estimate"
                ) != comparison_of(readings, estimate["q50"]):
                    fail(f"{where}: comparison_to_estimate")
                if source.get("interval_label") != INTERVAL_LABEL:
                    fail(f"{where}: interval_label")
            elif source.get("limitation") != LIMITATION or "model_estimate" in source:
                fail(f"{where}: single-source limitation")
        elif kind == "comparison":
            if not {"baseline", "current", "difference", "direction"} <= set(source):
                fail(f"{where}: comparison keys")
        else:
            fail(f"{where}: unsupported kind {kind}")


# ----------------------------------------------------------------------------- answers
def _hm(seconds):
    minutes = int((_decimal(seconds) + 30) // 60)
    return minutes // 60, minutes % 60


def _allowed(packet):
    numbers, durations, dates = set(), set(), set()

    def add(value):
        try:
            d = _decimal(value)
        except ValueError:
            return
        numbers.add(str(d.normalize()))
        for q in ("1", "0.1", "0.01"):
            numbers.add(str(d.quantize(Decimal(q)).normalize()))

    for s in packet["sources"]:
        dates.update({s["start_date"], s["end_date"]})
        for day in s.get("days", []):
            dates.add(day["date"])
            if day["value"] is not None:
                add(day["value"])
                if s["unit"] == "s":
                    durations.add(_hm(day["value"]))
        for key, value in (s.get("recorded_values_summary") or {}).items():
            if key.endswith("date"):
                dates.add(value)
            elif isinstance(value, str):
                add(value)
                if s["unit"] == "s" and key in {"minimum", "maximum"}:
                    durations.add(_hm(value))
        for reading in s.get("readings", []):
            if reading.get("value") is not None:
                add(reading["value"])
        for value in (s.get("model_estimate") or {}).values():
            add(value)
        for value in (s.get("comparison_to_estimate") or {}).get("absolute_distances", {}).values():
            add(value)
        for key in ("baseline", "current"):
            if isinstance(s.get(key), dict):
                add(s[key].get("value"))
                dates.update({s[key].get("start_date"), s[key].get("end_date")})
        if "difference" in s:
            add(s["difference"])
    return numbers, durations, {d for d in dates if d}


def _day_known(dates, month, day, year=None):
    return any(
        int(d[5:7]) == int(month) and int(d[8:10]) == int(day) and (not year or d[:4] == year)
        for d in dates
    )


def answer_warnings(answer, packet):
    """Review warnings: facts in the answer that the packet cannot support, plus style rules.

    These are reported, never silently accepted: an approval must acknowledge the exact list.
    """
    numbers, durations, dates = _allowed(packet)
    years = {d[:4] for d in dates}
    out, text = [], answer
    for h, m in re.findall(r"(\d+) hours? (\d+) minutes?", text) + re.findall(
        r"(\d+)\u5c0f\u65f6(\d+)\u5206\u949f", text
    ):
        if (int(h), int(m)) not in durations:
            out.append(f"duration {h}h{m}m not derivable from evidence seconds")
    text = re.sub(r"\d+ hours? \d+ minutes?|\d+\u5c0f\u65f6\d+\u5206\u949f", " ", text)
    for h in re.findall(r"(\d+) hours\b", text):
        if (int(h), 0) not in durations:
            out.append(f"duration {h}h not derivable from evidence seconds")
    text = re.sub(r"\d+ hours\b", " ", text)
    for iso in re.findall(r"\d{4}-\d{2}-\d{2}", text):
        out.append(f"ISO date {iso} in prose")
    text = re.sub(r"\d{4}-\d{2}-\d{2}", " ", text)
    pair_re = rf"({MONTH_RE}) (\d{{1,2}})\b and (\d{{1,2}})\b(?!\.\d)"
    for month, d1, d2 in re.findall(pair_re, text):
        for day in (d1, d2):
            if not _day_known(dates, MONTHS[month], day):
                out.append(f"date {month} {day} not in evidence")
    text = re.sub(pair_re, " ", text)
    for month, day, year in re.findall(rf"({MONTH_RE}) (\d{{1,2}})\b(?:, (\d{{4}}))?", text):
        if not _day_known(dates, MONTHS[month], day, year or None):
            out.append(f"date {month} {day} {year} not in evidence".replace("  ", " "))
    text = re.sub(rf"({MONTH_RE}) \d{{1,2}}(?:, \d{{4}})?", " ", text)
    for month, d1, d2 in re.findall(r"(\d{1,2})\u6708(\d{1,2})\u65e5\u81f3(\d{1,2})\u65e5", text):
        for day in (d1, d2):
            if not _day_known(dates, month, day):
                out.append(f"date {month}\u6708{day}\u65e5 not in evidence")
    text = re.sub(r"\d{1,2}\u6708\d{1,2}\u65e5\u81f3\d{1,2}\u65e5", " ", text)
    for year, month, day in re.findall(r"(?:(\d{4})\u5e74)?(\d{1,2})\u6708(\d{1,2})\u65e5", text):
        if not _day_known(dates, month, day, year or None):
            out.append(f"date {year}\u5e74{month}\u6708{day}\u65e5 not in evidence")
    text = re.sub(r"(?:\d{4}\u5e74)?\d{1,2}\u6708\d{1,2}\u65e5", " ", text)
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        value = token.replace(",", "")
        try:
            norm = str(Decimal(value).normalize())
        except InvalidOperation:
            continue
        if norm not in numbers and value not in years:
            out.append(f"number {token} not in evidence")
    if any(s["unit"] == "s" for s in packet["sources"]) and re.search(
        r"\bseconds\b|\u79d2", answer
    ):
        out.append("raw seconds in prose")
    for label in INTERNAL_LABELS:
        if label in answer:
            out.append(f"internal label {label!r} in prose")
    if re.search(r"\be\d+\.(healthkit|google_health)\b", answer):
        out.append("evidence id in prose")
    return out


def diff_spans(a, b):
    """Word-level (character-level for CJK) number of differing spans between two answers."""
    cjk = re.search(r"[\u4e00-\u9fff]", a + b)
    ta, tb = (list(a), list(b)) if cjk else (a.split(), b.split())
    matcher = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    return sum(1 for op in matcher.get_opcodes() if op[0] != "equal")


# ----------------------------------------------------------------------------- isolation
def evidence_signature(packet):
    """Metric/provider/window shape of a packet; equal signatures are near-duplicates."""
    return fingerprint(
        sorted(
            [s["metric"], s["provider"] or "", s["start_date"], s["end_date"]]
            for s in packet["sources"]
        )
    )


def _question(prompt):
    return json.loads(prompt[1]["content"])["user_request"]


def isolation_entry(ident, kind, prompt, packet, source):
    """A hash-only record of a case that must never become a training preference."""
    return {
        "id": ident,
        "kind": kind,
        "question_sha256": fingerprint(_question(prompt)),
        "evidence_fingerprint": fingerprint(packet),
        "evidence_signature": evidence_signature(packet),
        "source": source,
    }


def load_isolation(path):
    doc = json.loads(Path(path).read_text())
    if doc.get("schema") != ISOLATION_SCHEMA or not doc.get("entries"):
        raise ValueError("Isolation list is missing or malformed")
    kinds = {"regression_test", "validation", "blocked"}
    index = {"id": {}, "question_sha256": {}, "evidence_fingerprint": {}, "evidence_signature": {}}
    for entry in doc["entries"]:
        if entry.get("kind") not in kinds or set(index) - set(entry):
            raise ValueError("Isolation entry malformed")
        for key in index:
            index[key].setdefault(entry[key], []).append((entry["id"], entry["kind"]))
    return index


def check_isolation(row, index):
    """A candidate may not be, share evidence with, or closely mirror an isolated case."""
    hits = []
    packet = row["evidence_packet"]
    for key, value in (
        ("id", row["id"]),
        ("question_sha256", fingerprint(_question(row["prompt"]))),
        ("evidence_fingerprint", fingerprint(raw_packet(packet))),
        ("evidence_signature", evidence_signature(packet)),
    ):
        for ident, kind in index[key].get(value, []):
            if (
                kind == "validation"
                and row.get("proposed_split") == "validation"
                and ident == row["id"]
            ):
                continue  # the validation set is allowed to contain its own rows
            if kind == "blocked" and key == "evidence_signature":
                # Blocked rows are excluded for the writer contract, not as evaluation data:
                # identity, question and exact evidence still block, a shared window shape does not.
                continue
            hits.append(f"{key} matches isolated {kind} {ident}")
    if hits:
        raise ValueError("Isolated case leakage: " + "; ".join(hits))


# ----------------------------------------------------------------------------- provenance
def _load_trace(path, row):
    doc = json.loads(Path(path).read_text())
    if "input_messages" in doc:
        if doc.get("provenance") not in REPLAY_PROVENANCE:
            raise ValueError("Unrecognized synthetic replay provenance")
        return doc["input_messages"], doc["raw_model_output"]
    case = doc.get("case") or {}
    if case.get("split") != "development" or case.get("data_kind") != "synthetic":
        raise ValueError("Only synthetic development captures are accepted")
    calls = [
        c
        for c in doc["turns"][-1]["model_calls"]
        if len(c["messages"]) == 2 and c["messages"][1] == row["prompt"][1] and not c.get("tools")
    ]
    if len(calls) != 1:
        raise ValueError("Expected one tool-free writer call matching the user message")
    return calls[0]["messages"], calls[0].get("raw_text")


def _under_local(path):
    if ".local" not in Path(path).resolve().parts:
        raise ValueError("Trace and archive files must live under an ignored .local directory")


def verify_provenance(row, reg):
    """Per-class origin check. Returns the hashes that bind the approval to the origin."""
    provenance = row.get("provenance")
    capture = row.get("capture") or {}
    review = row.get("review") or {}
    if provenance not in PROVENANCE:
        raise ValueError("Unknown provenance")
    if not isinstance(row.get("text_origin"), str) or not row["text_origin"].strip():
        raise ValueError("text_origin must describe where the completions come from")
    if provenance == "captured_verbatim":
        return {"trace_sha256": verify_capture(row)}
    if provenance in CAPTURED:
        path = capture.get("trace_path")
        if not path or not Path(path).is_file():
            raise ValueError("Captured rows need an existing raw trace file")
        _under_local(path)
        digest = file_hash(path)
        if digest != capture.get("trace_sha256"):
            raise ValueError("Original trace hash changed")
        messages, raw = _load_trace(path, row)
        captured_version = prompt_version(messages[0]["content"], reg)
        if (
            captured_version is None
            or fingerprint(messages[0]["content"]) != capture.get("captured_prompt_fingerprint")
            or capture.get("captured_prompt_version") != captured_version
        ):
            raise ValueError("Captured prompt is not a registered version")
        if messages[1] != row["prompt"][1]:
            raise ValueError("User message differs from the captured model input")
        if provenance == "captured_old_prompt_reused":
            if capture.get("captured_rejected") is not True or raw != row["rejected"][0]["content"]:
                raise ValueError("Reused capture must keep the observed rejected output")
        else:
            if capture.get("captured_rejected_text") != raw:
                raise ValueError("Edited rows must preserve the observed output for audit")
            if not capture.get("edit_basis") or not review:
                raise ValueError("Edited rows need edit_basis and a review reference")
        return {"trace_sha256": digest, "captured_prompt_version": captured_version}
    if provenance == "archive_derived":
        archive = row.get("archive") or {}
        if capture.get("trace_path") or capture.get("trace_sha256"):
            raise ValueError("Archive rows must not claim a raw trace")
        path = archive.get("path")
        if not path or not Path(path).is_file() or not archive.get("record"):
            raise ValueError("Archive rows need an existing archive file and record")
        _under_local(path)
        digest = file_hash(path)
        if digest != archive.get("sha256") or not review:
            raise ValueError("Archive hash changed or review reference missing")
        return {"archive_sha256": digest}
    if any(capture.get(k) for k in ("trace_path", "trace_sha256", "captured_prompt_fingerprint")):
        raise ValueError("Synthetic rows must not claim a capture")
    if not isinstance(row.get("synthetic_additions"), list) or not review:
        raise ValueError("Synthetic rows need synthetic_additions and a review reference")
    return {}


# ----------------------------------------------------------------------------- rows
def pair_fingerprint(row):
    """Any change to content, origin or prompt version changes the hash and voids approvals."""
    capture = row.get("capture") or {}
    return fingerprint(
        {
            "prompt": row["prompt"],
            "chosen": row["chosen"][0]["content"],
            "rejected": row["rejected"][0]["content"],
            "evidence_packet": row["evidence_packet"],
            "provenance": row["provenance"],
            "prompt_version": row["prompt_version"],
            "synthetic_additions": row.get("synthetic_additions") or [],
            "origin": {
                "trace_sha256": capture.get("trace_sha256"),
                "archive_sha256": (row.get("archive") or {}).get("sha256"),
            },
        }
    )


def validate_row(row, split, reg, isolation):
    if row.get("schema_version") != CANDIDATE_SCHEMA:
        raise ValueError("Candidate schema is not v2")
    if row.get("proposed_split") != split or split not in {"train", "validation"}:
        raise ValueError("Only assigned development train/validation candidates are accepted")
    if "holdout" in row["id"].lower() or "test" in str(row.get("split", "")).lower():
        raise ValueError("Held-out tests must never become preferences")
    if row.get("contract_blocked") is not False or row.get("basic_validator_error"):
        raise ValueError("Unresolved output-contract failure")
    if not row.get("split_group"):
        raise ValueError("Missing leakage group")
    result = pair(row)
    prompt = result["prompt"]
    current = reg["entries"][reg["current"]]
    version = row.get("prompt_version") or {}
    if (
        len(prompt) != 2
        or prompt[0].get("role") != "system"
        or prompt[1].get("role") != "user"
        or fingerprint(prompt[0]["content"]) != current["fingerprint"]
        or version.get("name") != reg["current"]
        or version.get("fingerprint") != current["fingerprint"]
    ):
        raise ValueError("Writer prompt is not the registered current version")
    user = json.loads(prompt[1]["content"])
    if set(user) != {"language", "user_request", "evidence"}:
        raise ValueError("User message shape changed")
    if user["evidence"] != row["evidence_packet"]:
        raise ValueError("Evidence differs from the exact model input")
    if reg["current"] == "grounded-answer-v5-precomputed-presentation" and (
        presentation_packet(raw_packet(user["evidence"]), user["language"]) != user["evidence"]
    ):
        raise ValueError("The v5 input must include complete deterministic presentation fields")
    check_packet(row["evidence_packet"], user["language"])
    chosen, rejected = json.loads(result["chosen"]), json.loads(result["rejected"])
    if chosen == rejected:
        raise ValueError("Identical completions do not define a preference")
    sources = {s["evidence_id"] for s in row["evidence_packet"]["sources"]}
    for completion in (chosen, rejected):
        if (
            set(completion) != {"answer", "evidence_ids"}
            or not isinstance(completion["answer"], str)
            or not 1 <= len(completion["answer"]) <= 2200
            or set(completion["evidence_ids"]) != sources
        ):
            raise ValueError("Completion violates the writer output contract")
    if user["language"] != ("zh" if re.search(r"[\u4e00-\u9fff]", chosen["answer"]) else "en"):
        raise ValueError("Language field does not match the chosen answer")
    check_isolation(row, isolation)
    return result, answer_warnings(chosen["answer"], row["evidence_packet"])


def inspect(train, validation, reg, isolation, provenance_check=verify_provenance):
    report, seen_ids, seen = [], set(), {}
    chosen_by_split = {"train": [], "validation": []}
    keys = (
        "split_group",
        "evidence_fingerprint",
        "prompt_fingerprint",
        "user_fingerprint",
        "signature",
    )
    for split, rows in (("train", train), ("validation", validation)):
        if not rows:
            raise ValueError(f"Empty {split} split")
        for row in rows:
            if row["id"] in seen_ids:
                raise ValueError("Duplicate case id")
            seen_ids.add(row["id"])
            result, warnings = validate_row(row, split, reg, isolation)
            values = {
                "split_group": row["split_group"],
                "evidence_fingerprint": fingerprint(raw_packet(row["evidence_packet"])),
                "prompt_fingerprint": fingerprint(row["prompt"]),
                "user_fingerprint": fingerprint(row["prompt"][1]),
                "signature": evidence_signature(row["evidence_packet"]),
            }
            notes = []
            for key in keys:
                owner = seen.get((key, values[key]))
                if owner and owner[0] != split:
                    raise ValueError(f"Train/validation leakage ({key} shared with {owner[1]})")
                if owner and key == "evidence_fingerprint":
                    notes.append(f"same evidence packet as {owner[1]}")
                seen.setdefault((key, values[key]), (split, row["id"]))
            answer = json.loads(result["chosen"])["answer"]
            for other_split, items in chosen_by_split.items():
                for other_id, other in items:
                    if (
                        other_split != split
                        and difflib.SequenceMatcher(None, answer, other, autojunk=False).ratio()
                        >= 0.9
                    ):
                        raise ValueError(
                            f"Train/validation leakage (near-duplicate chosen of {other_id})"
                        )
            chosen_by_split[split].append((row["id"], answer))
            origin = provenance_check(row, reg)
            report.append(
                {
                    "id": row["id"],
                    "split": split,
                    "group": row["split_group"],
                    "provenance": row["provenance"],
                    "pair_sha256": pair_fingerprint(row),
                    "export_sha256": fingerprint(result),
                    "trace_sha256": origin.get("trace_sha256"),
                    "archive_sha256": origin.get("archive_sha256"),
                    "warnings": warnings,
                    "notes": notes,
                    "diff_spans": diff_spans(answer, json.loads(result["rejected"])["answer"]),
                    "synthetic_additions_sha256": fingerprint(row.get("synthetic_additions") or []),
                    "review_status": row.get("review_status"),
                }
            )
    return report


def export_bundle(
    train, validation, report, approvals, output, reg, isolation_path, derived_from=None
):
    """An approval binds the v2 pair hash, split, warnings and synthetic additions."""
    by_id = {r["id"]: r for r in approvals}
    if len(by_id) != len(approvals) or set(by_id) != {r["id"] for r in report}:
        raise ValueError("One exact approval per candidate is required")
    for row in report:
        approval = by_id[row["id"]]
        if (
            approval.get("decision") != "approve"
            or not approval.get("reviewer")
            or approval.get("pair_sha256") != row["pair_sha256"]
            or approval.get("split") != row["split"]
            or approval.get("acknowledged_warnings") != row["warnings"]
            or approval.get("synthetic_additions_sha256") != row["synthetic_additions_sha256"]
        ):
            raise ValueError("Pending, stale or mismatched pair approval")
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for split, rows in (("train", train), ("validation", validation)):
        path = output / f"{split}.jsonl"
        path.write_text("".join(json.dumps(pair(r), ensure_ascii=True) + "\n" for r in rows))
        path.chmod(0o600)
        hashes[path.name] = file_hash(path)
    counts = {}
    for row in report:
        counts[row["provenance"]] = counts.get(row["provenance"], 0) + 1
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "approved": True,
        "data_kind": "synthetic_development",
        "files": hashes,
        "counts": {"train": len(train), "validation": len(validation)},
        "system_prompt_sha256": reg["entries"][reg["current"]]["fingerprint"],
        "prompt_version": reg["current"],
        "provenance_counts": counts,
        "isolation_sha256": file_hash(isolation_path),
        "derived_from_bundle": derived_from,
        "pairs": report,
        "approvals_sha256": fingerprint(approvals),
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--system-prompt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--approvals", type=Path)
    parser.add_argument("--isolation", type=Path, default=ISOLATION)
    parser.add_argument("--derived-from", help="SHA-256 of the archived validation file")
    args = parser.parse_args()
    if ".local" not in args.output.resolve().parts:
        raise ValueError("Write review artifacts only under an ignored .local directory")
    reg = registry(json.loads(CONFIG.read_text()))
    text = args.system_prompt.read_text()
    if fingerprint(text) != reg["entries"][reg["current"]]["fingerprint"]:
        raise ValueError("System prompt file is not the registered current writer prompt")
    train = read_rows(args.candidates / "train.candidates.jsonl")
    validation = read_rows(args.candidates / "validation.candidates.jsonl")
    isolation = load_isolation(args.isolation)
    report = inspect(train, validation, reg, isolation)
    if args.approvals:
        export_bundle(
            train,
            validation,
            report,
            json.loads(args.approvals.read_text()),
            args.output,
            reg,
            args.isolation,
            args.derived_from,
        )
        print("Approved development export written; no model training was started")
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        write_json(args.output / "audit.json", report)
        write_json(
            args.output / "approvals.draft.json",
            [
                {
                    **{
                        k: r[k]
                        for k in ("id", "split", "pair_sha256", "synthetic_additions_sha256")
                    },
                    "acknowledged_warnings": r["warnings"],
                    "decision": "pending",
                    "reviewer": "",
                }
                for r in report
            ],
        )
        print(f"Audited {len(report)} v2 candidates; draft approvals are NOT a training export")


if __name__ == "__main__":
    main()
