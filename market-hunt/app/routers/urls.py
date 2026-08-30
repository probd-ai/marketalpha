"""URL config CRUD + CSV processing endpoints (moved verbatim from api_server)."""
import logging
from datetime import datetime
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import mongo_conn
from app.serialization import serialize_doc

logger = logging.getLogger(__name__)
router = APIRouter()


class URLConfig(BaseModel):
    url: str
    index_name: str
    description: str
    tags: List[str]
    is_active: bool = True


class ProcessURLRequest(BaseModel):
    url_ids: List[str]


class URLUpdateConfig(BaseModel):
    url: Optional[str] = None
    index_name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    is_active: Optional[bool] = None


@router.get("/api/urls")
async def get_urls(active_only: bool = False):
    """Get all URL configurations"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        collection = mongo_conn.db.index_meta_csv_urls

        # Build query
        query = {}
        if active_only:
            query["is_active"] = True

        # Get URLs
        urls = list(collection.find(query))

        # Serialize documents
        serialized_urls = [serialize_doc(url) for url in urls]

        return JSONResponse(content=serialized_urls)

    except Exception as e:
        logger.error(f"Error fetching URLs: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch URLs: {str(e)}")


@router.post("/api/urls")
async def add_url(url_config: URLConfig):
    """Add a new URL configuration"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        from url_manager import URLManager

        manager = URLManager()
        manager.connect_to_mongodb()

        success, message = manager.add_url(
            url=url_config.url,
            index_name=url_config.index_name,
            description=url_config.description,
            tags=url_config.tags
        )

        manager.close_connection()

        if success:
            return JSONResponse(content={"success": True, "message": message})
        else:
            raise HTTPException(status_code=400, detail=message)

    except Exception as e:
        logger.error(f"Error adding URL: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to add URL: {str(e)}")


@router.put("/api/urls/{url_id}")
async def update_url(url_id: str, url_config: URLUpdateConfig):
    """Update an existing URL configuration"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        from url_manager import URLManager

        manager = URLManager()
        manager.connect_to_mongodb()

        # Get the existing URL first
        try:
            existing_url = mongo_conn.db.index_meta_csv_urls.find_one({"_id": ObjectId(url_id)})
            if not existing_url:
                raise HTTPException(status_code=404, detail="URL not found")
        except Exception as e:
            raise HTTPException(status_code=400, detail="Invalid URL ID format")

        # Prepare update data - only include fields that are provided
        update_data = {}
        if url_config.url is not None:
            update_data['url'] = url_config.url
        if url_config.index_name is not None:
            update_data['index_name'] = url_config.index_name
        if url_config.description is not None:
            update_data['description'] = url_config.description
        if url_config.tags is not None:
            update_data['tags'] = url_config.tags
        if url_config.is_active is not None:
            update_data['is_active'] = url_config.is_active

        # Always update the updated_at timestamp
        update_data['updated_at'] = datetime.now()

        # Update the document
        result = mongo_conn.db.index_meta_csv_urls.update_one(
            {"_id": ObjectId(url_id)},
            {"$set": update_data}
        )

        manager.close_connection()

        if result.modified_count > 0:
            return JSONResponse(content={"success": True, "message": "URL updated successfully"})
        else:
            return JSONResponse(content={"success": False, "message": "No changes made"})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating URL: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update URL: {str(e)}")


@router.post("/api/process")
async def process_urls(request: ProcessURLRequest):
    """Process data from specified URLs"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        from generic_data_loader import GenericIndexDataLoader

        loader = GenericIndexDataLoader()
        loader.connect_to_mongodb()

        # Process specified URLs
        results = loader.process_specific_urls(request.url_ids)

        loader.close_connection()

        if results.get("success", False):
            processed_count = results.get("processed_count", 0)
            total_count = results.get("total_count", 0)

            return JSONResponse(content={
                "success": True,
                "message": f"Successfully processed {processed_count}/{total_count} URLs",
                "processed_count": processed_count,
                "total_count": total_count,
                "results": results.get("results", [])
            })
        else:
            error_msg = results.get("error", "Unknown error occurred")
            processed_count = results.get("processed_count", 0)
            total_count = results.get("total_count", 0)

            return JSONResponse(content={
                "success": False,
                "message": f"Processed {processed_count}/{total_count} URLs with errors",
                "error": error_msg,
                "processed_count": processed_count,
                "total_count": total_count,
                "results": results.get("results", [])
            })

    except Exception as e:
        logger.error(f"Error processing URLs: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process URLs: {str(e)}")


@router.delete("/api/urls/{url_id}")
async def delete_url(url_id: str):
    """Delete a URL configuration"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        from url_manager import URLManager

        manager = URLManager()
        manager.connect_to_mongodb()

        success, message = manager.delete_url(url_id)

        manager.close_connection()

        if success:
            return JSONResponse(content={"success": True, "message": message})
        else:
            raise HTTPException(status_code=404, detail=message)

    except Exception as e:
        logger.error(f"Error deleting URL: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete URL: {str(e)}")
