"""Fixture loading for the NCBI server tests.

Not a conftest: the NCBI fixtures are captured NCBI responses and belong to the
test_ncbi_* files alone, while tests/conftest.py is shared with the mygene
suite. Importing by name keeps the two from leaking into each other.
"""

import json
import pathlib

FIXTURES = pathlib.Path(__file__).parent / "ncbi_fixtures"


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text())


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text()
