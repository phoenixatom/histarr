# SPDX-License-Identifier: GPL-3.0-only

import json
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))
from histarr import app as module

user = {"id": 42, "username": "test-user"}
cache_path = pathlib.Path(tempfile.mkdtemp()) / module.CACHE_FILE_NAME
cache = module.empty_incremental_cache(user, False)
cache["plex_servers"] = {
    "server-1": {
        "records": [
            {"historyKey": "h1", "ratingKey": "1", "viewedAt": 100},
            {"historyKey": "h2", "ratingKey": "2", "viewedAt": 200},
        ]
    }
}
cache["tautulli"] = {
    "history": [
        {"row_id": 1, "rating_key": "1", "started": 90},
        {"row_id": 2, "rating_key": "2", "started": 190},
    ]
}
module.save_incremental_cache(cache_path, cache)

loaded, used = module.load_incremental_cache(cache_path, user, False)
assert used
assert loaded["plex_servers"]["server-1"]["records"][1]["historyKey"] == "h2"
assert json.loads(cache_path.read_text())["account_id"] == "42"

legacy = json.loads(cache_path.read_text())
legacy["schema_version"] = "1.0"
legacy["metadata"] = {
    "server-1": {
        "1": {"ratingKey": "1", "_exporter_source": "plex"},
        "missing": {"_exporter_source": "not_found", "_exporter_checked_at": "2026-01-01T00:00:00Z"},
    }
}
cache_path.write_text(json.dumps(legacy))
migrated, used = module.load_incremental_cache(cache_path, user, False)
assert used
assert migrated["schema_version"] == module.CACHE_SCHEMA_VERSION
assert "1" in migrated["metadata"]["server-1"]
assert "missing" not in migrated["metadata"]["server-1"]

plex = module.merge_plex_history(
    loaded["plex_servers"]["server-1"]["records"],
    [
        {"historyKey": "h2", "ratingKey": "2", "viewedAt": 200, "updated": True},
        {"historyKey": "h3", "ratingKey": "3", "viewedAt": 300},
    ],
)
assert [row["historyKey"] for row in plex] == ["h1", "h2", "h3"]
assert plex[1]["updated"] is True

tautulli = module.merge_tautulli_history(
    loaded["tautulli"]["history"],
    [
        {"row_id": 2, "rating_key": "2", "started": 190, "updated": True},
        {"row_id": 3, "rating_key": "3", "started": 290},
    ],
)
assert [row["row_id"] for row in tautulli] == [1, 2, 3]
assert tautulli[1]["updated"] is True

other_scope, used = module.load_incremental_cache(cache_path, user, True)
assert not used
assert other_scope["all_users"] is True

cache_text = cache_path.read_text()
assert "plex-secret-token" not in cache_text
assert "tautulli-secret-token" not in cache_text

print("Incremental cache, overlap merge, scope isolation, and credential-redaction tests passed")
