# -*- coding: utf-8 -*-
"""
Test cases for advanced address normalization in geocoder.py
"""
import pytest
from lib import geocoder

def test_parse_address_components_variants():
    # Handles 'PONCE DE LEON AVENUE, STOP 37 1/2' vs. '735 Avenida Ponce de León'
    cases = [
        # Spanish/US-mixed, with stop
        ("PONCE DE LEON AVENUE, STOP 37 1/2", {
            "house_number": "",
            "street_name": "PONCE DE LEON AVENUE, STOP 37 1/2",
        }),
        # Standard PR address
        ("735 Avenida Ponce de León", {
            "house_number": "735",
            "street_name": "Avenida Ponce de León",
        }),
        # US style
        ("123 Main St", {
            "house_number": "123",
            "street_name": "Main St",
        }),
        # Complex
        ("100A Commerce Blvd Apt 5B", {
            "house_number": "100A",
            "street_name": "Commerce Blvd Apt 5B",
        }),
        # Hyphenated
        ("12-14 Harbor Drive", {
            "house_number": "12-14",
            "street_name": "Harbor Drive",
        }),
        # No number
        ("PIER 1, BERTH 57", {
            "house_number": "",
            "street_name": "PIER 1, BERTH 57",
        }),
    ]
    for addr, expected in cases:
        c = geocoder.parse_address_components(addr)
        assert c["house_number"] == expected["house_number"], f"{addr}: {c}"
        assert c["street_name"] == expected["street_name"], f"{addr}: {c}"

def test_parse_street_address_variants():
    cases = [
        ("PONCE DE LEON AVENUE, STOP 37 1/2", ("", "PONCE DE LEON AVENUE, STOP 37 1/2")),
        ("735 Avenida Ponce de León", ("735", "Avenida Ponce de León")),
        ("123 Main St", ("123", "Main St")),
        ("100A Commerce Blvd Apt 5B", ("100A", "Commerce Blvd Apt 5B")),
        ("12-14 Harbor Drive", ("12-14", "Harbor Drive")),
        ("PIER 1, BERTH 57", ("", "PIER 1, BERTH 57")),
    ]
    for addr, expected in cases:
        result = geocoder.parse_street_address(addr)
        assert result == expected, f"{addr}: {result} != {expected}"
