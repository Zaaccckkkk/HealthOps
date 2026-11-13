"""Deterministic presentation fields; canonical measurements and identifiers stay intact."""

from copy import deepcopy
from decimal import ROUND_HALF_UP, Decimal

PROVIDER_NAMES = {"healthkit": "Apple Health", "google_health": "Google Health"}


def provider_name(provider: str | None) -> str:
    """Display a source without changing its storage key or evidence identity."""
    return PROVIDER_NAMES.get(provider, provider or "")


DURATION_SUMMARIES = (
    "minimum",
    "maximum",
    "mean_of_available_daily_values",
    "first_to_last_change",
    "absolute_change",
)


def duration_text(value, language: str = "en", *, compact: bool = False) -> str | None:
    """Round a seconds quantity once to minutes, half up, without float arithmetic."""
    if value is None:
        return None
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Duration must be finite")
    minutes = int((abs(number) / 60).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    hours, rest = divmod(minutes, 60)
    rounded = Decimal(minutes * 60) != abs(number)
    sign = "-" if number < 0 and minutes else ""
    if compact:
        result = f"{hours} h {rest} min"
    elif language == "zh":
        result = (f"{hours}\u5c0f\u65f6" if hours else "") + (
            f"{rest}\u5206\u949f" if rest or not hours else ""
        )
    else:
        parts = []
        if hours:
            parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
        if rest or not hours:
            parts.append(f"{rest} minute" + ("s" if rest != 1 else ""))
        result = " ".join(parts)
    qualifier = ("\u7ea6" if language == "zh" else "about ") if rounded else ""
    return qualifier + sign + result


def presentation_packet(facts: dict, language: str) -> dict:
    """Add display fields to a copy immediately before the writer sees evidence.

    Raw seconds remain available for explicit seconds requests and evidence checking.
    Format only typed duration measurements, never dates, counts or arbitrary strings.
    This also handles frozen replay packets, using the same code as live messages.
    """
    packet = deepcopy(facts)

    def display_fields(container, names):
        for name in names:
            if name in container:
                container[name + "_display"] = duration_text(container[name], language)

    for source in packet.get("sources", []):
        provider = source.get("provider")
        if provider in PROVIDER_NAMES:
            source["provider_display"] = PROVIDER_NAMES[provider]
        for reading in source.get("readings", []):
            if reading.get("provider") in PROVIDER_NAMES:
                reading["provider_display"] = PROVIDER_NAMES[reading["provider"]]
        comparison = source.get("comparison_to_estimate", {})
        if "closest_channels" in comparison:
            comparison["closest_sources_display"] = [
                PROVIDER_NAMES.get(p, p) for p in comparison["closest_channels"]
            ]
        if source.get("unit") != "s":
            continue
        for day in source.get("days", []):
            display_fields(day, ("value",))
        display_fields(source.get("recorded_values_summary", {}), DURATION_SUMMARIES)
        for key in ("baseline", "current"):
            if isinstance(source.get(key), dict):
                display_fields(source[key], ("value",))
        display_fields(source, ("difference",))
        for reading in source.get("readings", []):
            display_fields(reading, ("value",))
        display_fields(source.get("model_estimate", {}), ("q05", "q50", "q95"))
        if "absolute_distances" in comparison:
            comparison["absolute_distances_display"] = {
                provider: duration_text(value, language)
                for provider, value in comparison["absolute_distances"].items()
            }
    return packet
