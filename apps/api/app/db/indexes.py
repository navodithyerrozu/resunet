"""CLI entrypoint for index management: `python -m app.db.indexes`.

Kept runnable standalone so index creation is an explicit deploy step rather
than something that happens implicitly on every API boot. Building an index
on a large collection is a foreground operation that can stall writes; you
want to choose when that happens.
"""

from __future__ import annotations

import asyncio
import sys

from app.core.logging import configure_logging, get_logger
from app.db.mongo import connect, disconnect, ensure_indexes, get_database

logger = get_logger(__name__)


async def _run() -> int:
    configure_logging()
    await connect()
    try:
        created = await ensure_indexes()
        total = sum(len(v) for v in created.values())
        print(f"\nEnsured {total} indexes across {len(created)} collections:\n")
        for coll, names in sorted(created.items()):
            print(f"  {coll}")
            for name in names:
                print(f"    - {name}")

        # Report anything present in the database but absent from the plan.
        # Stray indexes cost write throughput and usually mean a rename left
        # an orphan behind.
        db = get_database()
        planned = {c: set(n) for c, n in created.items()}
        for coll in sorted(planned):
            existing = {
                idx["name"]
                async for idx in db[coll].list_indexes()
                if idx["name"] != "_id_"
            }
            if orphans := existing - planned[coll]:
                logger.warning("mongo.unplanned_indexes", collection=coll, names=sorted(orphans))
                print(f"\n  WARNING {coll}: unplanned indexes {sorted(orphans)}")
    finally:
        await disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
