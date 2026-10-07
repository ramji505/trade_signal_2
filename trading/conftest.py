import sys
import os
import tempfile
import pytest
from pathlib import Path

# Add root directory to sys.path so tests can import project modules
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

@pytest.fixture(autouse=True, scope="session")
def isolated_test_database(tmp_path_factory):
    """
    CRITICAL: Creates a fresh isolated SQLite database for the entire test session.
    Prevents test results from being polluted by production trades.db state.
    The production trades.db is NEVER touched during tests.
    """
    from config import settings
    from database import init_db
    
    # Create a temp directory for this test session
    test_db_dir = tmp_path_factory.mktemp("test_db")
    test_db_path = str(test_db_dir / "test_trades.db")
    
    # Point settings at the isolated test DB
    original_db_path = settings.DATABASE_PATH
    settings.DATABASE_PATH = test_db_path
    
    # Initialize a clean schema
    init_db()
    
    yield test_db_path
    
    # Restore original path after all tests complete
    settings.DATABASE_PATH = original_db_path
