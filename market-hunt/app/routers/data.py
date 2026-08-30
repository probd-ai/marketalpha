"""Data overview / universes / index browse endpoints (moved verbatim from api_server)."""
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.db import mongo_conn

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/data")
async def get_data_overview():
    """Get data overview and statistics"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        collection = mongo_conn.db.index_meta

        # Get total unique companies (by Symbol)
        unique_companies_pipeline = [
            {
                "$group": {
                    "_id": "$Symbol"
                }
            },
            {
                "$count": "unique_companies"
            }
        ]
        unique_companies_result = list(collection.aggregate(unique_companies_pipeline))
        unique_companies_count = unique_companies_result[0]["unique_companies"] if unique_companies_result else 0

        # Get index-wise statistics
        pipeline = [
            {
                "$group": {
                    "_id": "$index_name",
                    "count": {"$sum": 1},
                    "last_update": {"$max": "$download_timestamp"}
                }
            }
        ]

        index_stats = list(collection.aggregate(pipeline))

        # Serialize timestamps
        for stat in index_stats:
            if stat.get('last_update'):
                stat['last_update'] = stat['last_update'].isoformat() if hasattr(stat['last_update'], 'isoformat') else str(stat['last_update'])

        return JSONResponse(content={
            "total_documents": unique_companies_count,
            "index_stats": index_stats
        })

    except Exception as e:
        logger.error(f"Error fetching data overview: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch data overview: {str(e)}")


@router.get("/api/data/universes")
async def get_available_universes():
    """Get all available universes (indexes) from the database"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        collection = mongo_conn.db.index_meta

        # Get all unique index names with their stock counts
        pipeline = [
            {
                "$group": {
                    "_id": "$index_name",
                    "stock_count": {"$sum": 1}
                }
            },
            {
                "$sort": {"_id": 1}
            }
        ]

        universes_data = list(collection.aggregate(pipeline))

        # Point-in-time eligibility: only universes with loaded reconstitution
        # events can run PIT (rebalance-aware) simulations. Derive it from the
        # data so the flag stays truthful as more indices are backfilled — never
        # hardcode the list.
        pit_eligible_names = set(
            mongo_conn.db.index_reconstitution.distinct("index_name")
        )

        # Format the response
        universes = []
        for universe in universes_data:
            index_name = universe["_id"]
            stock_count = universe["stock_count"]

            # Create display name with stock count
            display_name = f"{index_name} ({stock_count} stocks)"

            universes.append({
                "value": index_name,
                "label": display_name,
                "stock_count": stock_count,
                "pit_eligible": index_name in pit_eligible_names
            })

        logger.info(f"📊 Found {len(universes)} available universes")

        return JSONResponse(content={
            "universes": universes,
            "total_universes": len(universes)
        })

    except Exception as e:
        logger.error(f"Error fetching universes: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch universes: {str(e)}")


@router.get("/api/data/index/{index_name}")
async def get_index_companies(index_name: str):
    """Get all companies for a specific index"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        # Fetch all companies for the specified index
        collection = mongo_conn.db.index_meta
        companies = list(collection.find(
            {"index_name": index_name},
            {
                "_id": 0,
                "Company Name": 1,
                "Industry": 1,
                "Symbol": 1,
                "Series": 1,
                "ISIN Code": 1,
                "download_timestamp": 1
            }
        ).sort("Company Name", 1))  # Sort alphabetically by company name

        # Convert datetime objects to strings
        for company in companies:
            if 'download_timestamp' in company and company['download_timestamp']:
                company['download_timestamp'] = company['download_timestamp'].isoformat() if hasattr(company['download_timestamp'], 'isoformat') else str(company['download_timestamp'])

        return JSONResponse(content={
            "index_name": index_name,
            "total_companies": len(companies),
            "companies": companies
        })
    except Exception as e:
        logger.error(f"Error fetching companies for index {index_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch companies for index: {str(e)}")


@router.get("/api/data/index/{index_name}/industries")
async def get_index_industries(index_name: str):
    """Get all industries for a specific index with company counts"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        collection = mongo_conn.db.index_meta

        # Aggregate to get unique industries and their company counts for this index
        pipeline = [
            {"$match": {"index_name": index_name}},
            {
                "$group": {
                    "_id": "$Industry",
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"count": -1}}
        ]

        industries = list(collection.aggregate(pipeline))

        return JSONResponse(content={
            "index_name": index_name,
            "total_industries": len(industries),
            "industries": industries
        })

    except Exception as e:
        logger.error(f"Error fetching industries for index {index_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch industries for index: {str(e)}")


@router.get("/api/data/index/{index_name}/industries/{industry_name}")
async def get_index_industry_companies(index_name: str, industry_name: str):
    """Get all companies for a specific industry within a specific index"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        collection = mongo_conn.db.index_meta

        # Fetch companies that belong to both the index and the industry
        companies = list(collection.find(
            {
                "index_name": index_name,
                "Industry": industry_name
            },
            {
                "_id": 0,
                "Company Name": 1,
                "Industry": 1,
                "Symbol": 1,
                "Series": 1,
                "ISIN Code": 1,
                "download_timestamp": 1
            }
        ).sort("Company Name", 1))

        # Convert datetime objects to strings
        for company in companies:
            if 'download_timestamp' in company and company['download_timestamp']:
                company['download_timestamp'] = company['download_timestamp'].isoformat() if hasattr(company['download_timestamp'], 'isoformat') else str(company['download_timestamp'])

        return JSONResponse(content={
            "index_name": index_name,
            "industry_name": industry_name,
            "total_companies": len(companies),
            "companies": companies
        })

    except Exception as e:
        logger.error(f"Error fetching companies for industry {industry_name} in index {index_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch companies for industry in index: {str(e)}")
