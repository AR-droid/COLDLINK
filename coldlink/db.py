"""FalkorDB connection. One graph per lab (tenant) inside one FalkorDB instance."""
from __future__ import annotations

import os

from falkordb import FalkorDB

HOST = os.environ.get("FALKORDB_HOST", "localhost")
PORT = int(os.environ.get("FALKORDB_PORT", "6379"))
DEFAULT_LAB = os.environ.get("COLDLINK_LAB", "srfl")

_db: FalkorDB | None = None


def graph_name(lab: str) -> str:
    return f"coldlink_{lab}"


def get_graph(lab: str = DEFAULT_LAB):
    global _db
    if _db is None:
        _db = FalkorDB(host=HOST, port=PORT)
    return _db.select_graph(graph_name(lab))


def rows(g, query: str, params: dict | None = None, read_only: bool = False) -> list[dict]:
    """Run a query and return rows as dicts keyed by column name."""
    res = g.ro_query(query, params) if read_only else g.query(query, params)
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in res.header]
    cols = [c.decode() if isinstance(c, bytes) else c for c in cols]
    return [dict(zip(cols, r)) for r in res.result_set]
