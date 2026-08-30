"""Industry browse endpoints (moved verbatim from api_server)."""
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.db import mongo_conn

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/industries")
async def get_industries_overview():
    """Get overview of all industries with company counts"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        collection = mongo_conn.db.index_meta

        # Get total unique companies count (by Symbol)
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

        # Aggregate industry statistics with unique company counts
        pipeline = [
            {
                "$group": {
                    "_id": {
                        "industry": "$Industry",
                        "symbol": "$Symbol"
                    },
                    "index_name": {"$first": "$index_name"}
                }
            },
            {
                "$group": {
                    "_id": "$_id.industry",
                    "count": {"$sum": 1},
                    "indices": {"$addToSet": "$index_name"}
                }
            },
            {
                "$sort": {"count": -1}
            }
        ]

        industry_stats = list(collection.aggregate(pipeline))

        # Calculate additional metrics
        total_industries = len(industry_stats)

        return JSONResponse(content={
            "total_companies": unique_companies_count,
            "total_industries": total_industries,
            "industry_stats": industry_stats
        })

    except Exception as e:
        logger.error(f"Error fetching industries overview: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch industries overview: {str(e)}")


@router.get("/api/industries/{industry_name}")
async def get_industry_companies(industry_name: str):
    """Get all unique companies for a specific industry"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        collection = mongo_conn.db.index_meta

        # Aggregate to get unique companies (by Symbol) for the specified industry
        pipeline = [
            {"$match": {"Industry": industry_name}},
            {
                "$group": {
                    "_id": "$Symbol",
                    "Company Name": {"$first": "$Company Name"},
                    "Industry": {"$first": "$Industry"},
                    "Symbol": {"$first": "$Symbol"},
                    "Series": {"$first": "$Series"},
                    "ISIN Code": {"$first": "$ISIN Code"},
                    "indices": {"$addToSet": "$index_name"},
                    "download_timestamp": {"$first": "$download_timestamp"}
                }
            },
            {"$sort": {"Company Name": 1}}
        ]

        companies = list(collection.aggregate(pipeline))

        # Convert datetime objects to strings and flatten the structure
        unique_companies = []
        for company in companies:
            company_data = {
                "Company Name": company["Company Name"],
                "Industry": company["Industry"],
                "Symbol": company["Symbol"],
                "Series": company["Series"],
                "ISIN Code": company["ISIN Code"],
                "indices": company["indices"],
                "download_timestamp": company["download_timestamp"].isoformat() if hasattr(company["download_timestamp"], 'isoformat') else str(company["download_timestamp"]) if company["download_timestamp"] else None
            }
            unique_companies.append(company_data)

        return JSONResponse(content={
            "industry_name": industry_name,
            "total_companies": len(unique_companies),
            "companies": unique_companies
        })

    except Exception as e:
        logger.error(f"Error fetching companies for industry {industry_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch companies for industry: {str(e)}")


@router.get("/api/industries/{industry_name}/indices")
async def get_industry_indices(industry_name: str):
    """Get all indices that contain companies from a specific industry"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        collection = mongo_conn.db.index_meta

        # Get unique indices for this industry
        indices = collection.distinct("index_name", {"Industry": industry_name})

        # Get company count per index for this industry
        pipeline = [
            {"$match": {"Industry": industry_name}},
            {
                "$group": {
                    "_id": "$index_name",
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"count": -1}}
        ]

        index_stats = list(collection.aggregate(pipeline))

        return JSONResponse(content={
            "industry_name": industry_name,
            "total_indices": len(indices),
            "indices": index_stats
        })

    except Exception as e:
        logger.error(f"Error fetching indices for industry {industry_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch indices for industry: {str(e)}")


@router.get("/api/industries/{industry_name}/indices/{index_name}")
async def get_industry_index_companies(industry_name: str, index_name: str):
    """Get all companies for a specific industry within a specific index"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        collection = mongo_conn.db.index_meta

        # Fetch companies that belong to both the industry and the index
        companies = list(collection.find(
            {
                "Industry": industry_name,
                "index_name": index_name
            },
            {
                "_id": 0,
                "Company Name": 1,
                "Industry": 1,
                "Symbol": 1,
                "Series": 1,
                "ISIN Code": 1,
                "index_name": 1,
                "download_timestamp": 1
            }
        ).sort("Company Name", 1))

        # Convert datetime objects to strings
        for company in companies:
            if 'download_timestamp' in company and company['download_timestamp']:
                company['download_timestamp'] = company['download_timestamp'].isoformat() if hasattr(company['download_timestamp'], 'isoformat') else str(company['download_timestamp'])

        return JSONResponse(content={
            "industry_name": industry_name,
            "index_name": index_name,
            "total_companies": len(companies),
            "companies": companies
        })

    except Exception as e:
        logger.error(f"Error fetching companies for industry {industry_name} in index {index_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch companies for industry in index: {str(e)}")
