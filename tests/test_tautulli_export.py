# SPDX-License-Identifier: GPL-3.0-only

import json
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))
from histarr import tautulli as module

ROWS = [
    {
        "row_id": index,
        "user_id": 42,
        "full_title": f"Item {index}",
        "view_offset": index * 1000,
        "duration": 3600000,
        "percent_complete": index % 101,
        "play_duration": 600,
        "extra_new_field": {"sample": True},
    }
    for index in range(1001)
]


def fake_tautulli_call(api_url, api_key, command, **params):
    assert api_url == "http://tautulli.test/root/api/v2"
    assert api_key == "test-secret-key"
    assert command == "get_history"
    assert params["grouping"] == 0
    assert params["user_id"] == 42
    start = int(params["start"])
    length = int(params["length"])
    return {
        "recordsTotal": len(ROWS),
        "recordsFiltered": len(ROWS),
        "data": ROWS[start : start + length],
    }


module.tautulli_call = fake_tautulli_call
api_url = "http://tautulli.test/root/api/v2"
history = module.fetch_tautulli_history(
    api_url,
    "test-secret-key",
    user_id=42,
    username="test-user",
    all_users=False,
)
assert len(history) == 1001
output_dir = pathlib.Path(tempfile.mkdtemp())
paths = module.write_tautulli_exports(
    output_dir,
    {"id": 42, "username": "test-user"},
    history,
    api_url=api_url,
    all_users=False,
)
assert paths[3] == 1001
combined = "\n".join(path.read_text() for path in paths[:3])
assert "test-secret-key" not in combined
assert "extra_new_field" in combined
assert "percent_complete" in combined

print("Tautulli pagination, filtering, full-field export, and key-redaction tests passed")
