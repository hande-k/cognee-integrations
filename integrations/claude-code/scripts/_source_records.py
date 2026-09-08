"""Source-independent record identifiers, timestamps and provenance."""

from _dataset_access import dataset_id


class MemoryError(ValueError):
    """A safe, actionable command error."""


def uuid(value):
    ident = dataset_id(value)
    if not ident:
        raise MemoryError("Use a dataset/document UUID returned by the plugin.")
    return ident


def metadata(row):
    return row.get("externalMetadata") or row.get("external_metadata") or {}


def document_ref(row):
    return {
        "dataset_id": row["dataset_id"],
        "document_id": row["id"],
        "label": row.get("label") or row.get("name"),
        "url": metadata(row).get("source_uri") or metadata(row).get("source_url"),
        "record_updated_at": row.get("updatedAt") or row.get("updated_at"),
    }
