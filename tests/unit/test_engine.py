"""Regression test: `make_engine()` must create a sqlite file URL's parent
directory. Found by actually booting the app under uvicorn with the
default `./data/...` URL against a fresh checkout (no test had exercised a
file-based sqlite URL before — every other test uses the bare
`sqlite+aiosqlite://` in-memory form) -- `init_models()` crashed with
`sqlite3.OperationalError: unable to open database file`.
"""

from __future__ import annotations

from sqlalchemy import text

from app.db.engine import make_engine


async def test_make_engine_creates_missing_parent_directory_for_sqlite_file(tmp_path):
    db_path = tmp_path / "nested" / "does" / "not" / "exist" / "test.db"
    assert not db_path.parent.exists()

    engine = make_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    await engine.dispose()

    assert db_path.parent.exists()
    assert db_path.exists()


async def test_make_engine_in_memory_sqlite_unaffected():
    engine = make_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    await engine.dispose()
