from pathlib import Path

import pytest

from decision_forge.io import load_project
from decision_forge.models import Project

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/customer_support/project.yaml"


@pytest.fixture
def example_path() -> Path:
    return EXAMPLE


@pytest.fixture
def project() -> Project:
    return load_project(EXAMPLE)
