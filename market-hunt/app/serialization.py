"""JSON serialization helpers shared across routers (moved verbatim from api_server)."""
from datetime import datetime


def sanitize_for_json(obj):
    """Recursively sanitize objects for JSON serialization"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {key: sanitize_for_json(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif hasattr(obj, '__dict__'):
        return sanitize_for_json(obj.__dict__)
    else:
        return obj


def serialize_doc(doc):
    if doc:
        doc['_id'] = str(doc['_id'])
        if 'created_at' in doc and doc['created_at']:
            doc['created_at'] = doc['created_at'].isoformat() if hasattr(doc['created_at'], 'isoformat') else str(doc['created_at'])
        if 'updated_at' in doc and doc['updated_at']:
            doc['updated_at'] = doc['updated_at'].isoformat() if hasattr(doc['updated_at'], 'isoformat') else str(doc['updated_at'])
        if 'last_downloaded' in doc and doc['last_downloaded']:
            doc['last_downloaded'] = doc['last_downloaded'].isoformat() if hasattr(doc['last_downloaded'], 'isoformat') else str(doc['last_downloaded'])
    return doc
