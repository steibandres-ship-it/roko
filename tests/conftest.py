from pathlib import Path

import pytest


@pytest.fixture
def temp_root(tmp_path: Path) -> Path:
    return tmp_path
