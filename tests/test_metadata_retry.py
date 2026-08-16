# SPDX-License-Identifier: GPL-3.0-only

import pathlib
import sys
import urllib.parse


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))
from histarr import plex as module

calls = []


def fake_json_request(url, _headers, *, timeout=15, **_kwargs):
    path = urllib.parse.urlsplit(url).path
    keys = path.rsplit("/", 1)[-1].split(",")
    calls.append(keys)
    if len(keys) > 25:
        raise module.ExportError("simulated oversized/transient metadata batch")
    return {
        "MediaContainer": {
            "Metadata": [
                {"ratingKey": key, "type": "movie", "title": f"Item {key}", "duration": 60000}
                for key in keys
            ]
        }
    }


module.json_request = fake_json_request
module.time.sleep = lambda _seconds: None
result = {
    "_connection_urls": ["http://plex.test:32400"],
    "_token": "secret-not-for-output",
}
metadata, errors, unavailable = module.fetch_plex_metadata(
    result, "client-id", [str(value) for value in range(1, 51)]
)
assert len(metadata) == 50
assert not errors
assert not unavailable
assert [len(keys) for keys in calls] == [50, 50, 25, 25]
assert all(value["_exporter_source"] == "plex" for value in metadata.values())


def fake_not_found(url, _headers, *, timeout=15, **_kwargs):
    raise module.ExportError("HTTP 404 from plex.test: <html>Not Found</html>")


module.json_request = fake_not_found
metadata, errors, unavailable = module.fetch_plex_metadata(
    result, "client-id", ["deleted-1", "deleted-2"]
)
assert not metadata
assert not errors
assert unavailable == ["deleted-1", "deleted-2"]

print("Plex metadata retry and split-batch recovery tests passed")
