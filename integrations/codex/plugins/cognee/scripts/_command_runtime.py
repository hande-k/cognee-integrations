"""Use the hooks' identity and launch resolution for every explicit command."""

import json
import os

import _plugin_common as pc


def resolve():
    host, _ = pc.resolve_host_key_outside_hook()
    if host:
        pc.set_session_key(host)
    record = pc._read_map_record(host) if host else {}
    url = pc._local_api_url()
    key, source = pc._api_key_with_source(url)
    return {
        "service_url": url,
        "api_key": key,
        "credential_source": source,
        "session_id": record.get("session_id") or os.environ.get("COGNEE_SESSION_ID", ""),
        "dataset": record.get("dataset")
        or os.environ.get("COGNEE_PLUGIN_DATASET", "agent_sessions"),
    }


if __name__ == "__main__":
    # Consumed internally by the shell wrappers, never log this credential envelope.
    print(json.dumps(resolve()))
