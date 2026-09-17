"""② Translate: what a value MEANS.

    indicators_info.py       the indicator catalogue
    canonical_units.py       a reading in the unit the catalogue declares
                             for its indicator (NOT `mirobody.units`, the
                             UCUM engine it borrows its arithmetic from)
    value_range_validator.py what counts as a plausible value
    fhir_mapping.py          indicator to fhir_id
    aggregate/               a day of points to one number, and which source
                             publishes it
    derive/                  quantities nothing measured: sleep efficiency,
                             heart-rate range
    std_indicator_registry/  publishes the catalogue to the database

① Collect stores what a device or a document said, verbatim and traceable.
This stage decides what it means. The two were one package until 1.4.4, with
the catalogue sitting inside `collect/` as `standardize/`, so "collect only
collects" was a sentence in a document rather than something the tree showed.

The frame is up; the contents are 1.5.0's. That version brings LOINC coding,
the comparability key and one standardized table, and these five modules are
what it rewrites against.

`aggregate/` is here because a daily total is the SAME quantity on a different
time axis, which is a LOINC axis change, and because the rules were already
here: `IndicatorInfo.aggregation_methods` declares them and `aggregate/` only
executes them. `derive/` sits beside it: sleep efficiency is total sleep over time in bed,
heart-rate range is max minus min. Nothing wore a sensor for either. They are
not collection, which is the point: ① Collect guarantees that what a source
said is stored cleanly and can be traced back, and computes nothing on top.

Two couplings, named rather than hidden. `collect/` imports this package in 11
files, because a provider declares its metrics with `StandardIndicator` and
`ingest` converts units before writing; `mirobody.collect` therefore still
re-exports `StandardIndicator` and `UNIT_CONVERSIONS`, so a provider plugin
keeps one import path. Back the other way, `aggregate/` writes through
`collect.readings.upsert_readings`, because `th_series_data` has one writer and
both stages write to it. 1.5.0 redesigns that table, which is where the second
one gets settled.

Nothing here is third-party: stdlib plus `mirobody` only. Keep it that way,
the same rule the library layer lives by.

Lazy (PEP 562): importing this package must not pull the server stack.
"""

from typing import TYPE_CHECKING

# name -> submodule that defines it. Every symbol another package needs is
# here: 14 of them, measured, not guessed. A caller outside this package
# imports `mirobody.translate`, so 1.5.0 can rewrite the modules behind these
# names without a call-site edit anywhere else.
_EXPORTS = {
    # the catalogue
    "StandardIndicator": "indicators_info",
    "HealthDataType": "indicators_info",
    "get_all_indicators_info": "indicators_info",
    "get_indicator_by_str": "indicators_info",
    "get_indicators_in_same_categories": "indicators_info",
    "get_standard_unit": "indicators_info",
    "is_valid_indicator": "indicators_info",
    "is_series_indicator": "indicators_info",
    "is_summary_indicator": "indicators_info",
    "normalize_indicator_name": "indicators_info",
    # canonical units
    "UNIT_CONVERSIONS": "canonical_units",
    "convert_to_standard": "canonical_units",
    "get_all_units_info": "canonical_units",
    # ranges, fhir ids, and the registry task
    "ValueRangeValidator": "value_range_validator",
    "FhirMapping": "fhir_mapping",
    "get_fhir_id": "fhir_mapping",
    "start_std_indicator_registry": "std_indicator_registry.startup",
    "start_aggregate_indicator_scheduler": "aggregate.startup",
    "start_derived_scheduler": "derive.task",
    "AggregateIndicatorService": "aggregate.service",
    "AggregateDatabaseService": "aggregate.database_service",
    "build_indicator_name": "aggregate.naming",
    "get_all_aggregation_rules": "aggregate.rule_generator",
}
__all__ = [*_EXPORTS]

if TYPE_CHECKING:  # static analyzers resolve the real symbols
    from .fhir_mapping import FhirMapping, get_fhir_id
    from .indicators_info import (
        HealthDataType,
        StandardIndicator,
        get_all_indicators_info,
        get_indicator_by_str,
        get_indicators_in_same_categories,
        get_standard_unit,
        is_series_indicator,
        is_summary_indicator,
        is_valid_indicator,
        normalize_indicator_name,
    )
    from .aggregate.database_service import AggregateDatabaseService
    from .aggregate.naming import build_indicator_name
    from .aggregate.rule_generator import get_all_aggregation_rules
    from .aggregate.service import AggregateIndicatorService
    from .aggregate.startup import start_aggregate_indicator_scheduler
    from .derive.task import start_derived_scheduler
    from .std_indicator_registry.startup import start_std_indicator_registry
    from .canonical_units import UNIT_CONVERSIONS, convert_to_standard, get_all_units_info
    from .value_range_validator import ValueRangeValidator


def __getattr__(name: str):
    import importlib

    where = _EXPORTS.get(name)
    if where is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = importlib.import_module(f".{where}", __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
