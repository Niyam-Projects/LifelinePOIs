"""
Confidence Score computation for LifelinePOI conflation.

Formula: CS = (distance_score * 0.4) + (attribute_score * 0.4) + (source_score * 0.2)

- distance_score: 1.0 if within 50m, linear decay to 0 at 500m
- attribute_score: fraction of non-null key attributes present in both sources
- source_score: 1.0 if EIA/EPA match, 0.5 if OSM-only, 0.25 if authoritative-only

NAICS boost (additive, applied after composite is computed):
- naics_confidence_boost() returns 0.0–0.25 based on the highest-tier NAICS/SIC
  code match from the FEMA lifeline map. The boost is added after the base CS
  and capped at 1.0.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class ConfidenceTier(str, Enum):
    HIGH = "high"        # CS >= 0.75: in both OSM + EIA/EPA within 50m
    MEDIUM = "medium"    # CS >= 0.40: in one source only or distance mismatch
    LOW = "low"          # CS < 0.40: unverified / sparse attributes


@dataclass
class ConfidenceScore:
    distance_score: float
    attribute_score: float
    source_score: float
    weights: dict[str, float]

    @property
    def composite(self) -> float:
        w = self.weights
        return (
            self.distance_score * w.get("distance", 0.40)
            + self.attribute_score * w.get("attributes", 0.40)
            + self.source_score * w.get("source", 0.20)
        )

    @property
    def tier(self) -> ConfidenceTier:
        c = self.composite
        if c >= 0.75:
            return ConfidenceTier.HIGH
        elif c >= 0.40:
            return ConfidenceTier.MEDIUM
        return ConfidenceTier.LOW


def distance_score(distance_m: float, max_distance_m: float = 500.0) -> float:
    """Linear decay from 1.0 at 0m to 0.0 at max_distance_m."""
    if distance_m <= 0:
        return 1.0
    if distance_m >= max_distance_m:
        return 0.0
    return 1.0 - (distance_m / max_distance_m)


def attribute_score(osm_attrs: dict, auth_attrs: dict, key_fields: list[str]) -> float:
    """
    Fraction of key_fields that are non-null in both sources.
    Returns 1.0 if all key fields agree, 0.0 if none match.
    """
    if not key_fields:
        return 0.5
    matches = sum(
        1 for k in key_fields
        if osm_attrs.get(k) is not None and auth_attrs.get(k) is not None
    )
    return matches / len(key_fields)


def source_score(has_osm: bool, has_authoritative: bool) -> float:
    """Score based on source coverage."""
    if has_osm and has_authoritative:
        return 1.0
    if has_authoritative:
        return 0.5   # authoritative-only: reliable location, may lack OSM detail
    if has_osm:
        return 0.25  # OSM-only: unverified against authoritative source
    return 0.0


def naics_confidence_boost(
    naics_codes: str | None,
    sic_codes: str | None,
    cfg_overrides: dict | None = None,
) -> float:
    """
    Return an additive confidence boost (0.0–0.25) based on the highest-tier
    NAICS or SIC code match in the FEMA lifeline map.

    Args:
        naics_codes: raw NAICS string from FRS (may be pipe/comma-delimited)
        sic_codes:   raw SIC string from FRS (may be pipe/comma-delimited)
        cfg_overrides: optional dict with boost_tier1/2/3 overrides from config

    Returns:
        float in [0.0, 0.25]; 0.0 if no lifeline code matched
    """
    from lib.naics_lifeline_map import lookup_code, boost_for_tier
    entry = lookup_code(naics_codes, sic_codes)
    if entry is None:
        return 0.0
    return boost_for_tier(entry["tier"], cfg_overrides)


def compute_confidence(
    distance_m: float,
    osm_attrs: dict,
    auth_attrs: dict,
    key_fields: list[str],
    has_osm: bool,
    has_authoritative: bool,
    weights: dict[str, float] | None = None,
) -> ConfidenceScore:
    w = weights or {"distance": 0.40, "attributes": 0.40, "source": 0.20}
    return ConfidenceScore(
        distance_score=distance_score(distance_m),
        attribute_score=attribute_score(osm_attrs, auth_attrs, key_fields),
        source_score=source_score(has_osm, has_authoritative),
        weights=w,
    )
