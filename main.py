import time
import structlog
from fastapi import FastAPI, Depends, Query, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Annotated
from starlette_prometheus import metrics, PrometheusMiddleware

from . import models, schemas, crud
from .database import engine, SessionLocal
from .logging_config import setup_logging

# Set up logging as soon as the application starts
setup_logging()

# This command tells SQLAlchemy to create all the tables based on the models
# but only if they don't exist already.
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="痛點雷達 API",
    description="為「痛點雷達」前端應用提供數據支持。",
    version="1.0.0"
)

# Add Prometheus middleware to automatically track request metrics
app.add_middleware(PrometheusMiddleware)

@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Middleware to log every incoming request."""
    structlog.contextvars.clear_contextvars()
    start_time = time.perf_counter()
    
    response = await call_next(request)
    
    end_time = time.perf_counter()
    process_time = (end_time - start_time) * 1000

    logger = structlog.get_logger("api.request")
    logger.info(
        "Request processed",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        client_host=request.client.host if request.client else "unknown",
        process_time_ms=f"{process_time:.2f}",
    )
    return response

# Dependency to get a DB session for each request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

DbDep = Annotated[Session, Depends(get_db)]

@app.get("/", tags=["Health Check"])
def read_root():
    return {"status": "ok", "message": "Welcome to TonDianLeiDar API!"}

@app.get("/health", tags=["Health Check"])
def health_check(db: DbDep):
    """
    Performs a health check on the API and its database connection.
    Returns a 200 OK if successful, or a 503 Service Unavailable if the
    database connection fails.
    """
    try:
        # Execute a simple query to check the database connection
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        logger = structlog.get_logger("health_check")
        logger.error("Database health check failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable.",
        )

# Add the /metrics endpoint for Prometheus
app.add_route("/metrics", metrics)

@app.get(
    "/api/opportunities",
    response_model=schemas.PaginatedOpportunityResponse,
    summary="Get a list of opportunity cards",
    tags=["Opportunities"],
)
def read_opportunities(
    db: DbDep,
    page: int = Query(1, ge=1, description="Page number, starting from 1"),
    page_size: int = Query(10, ge=1, le=100, description="Number of items per page"),
):
    """
    Retrieve a paginated list of opportunity reports for the main feed.
    """
    opportunities_data = crud.get_opportunities(db, page=page, page_size=page_size)
    return opportunities_data

@app.get(
    "/api/opportunities/{opportunity_id}",
    response_model=schemas.OpportunityReportDetail,
    summary="Get the detailed report for a single opportunity",
    tags=["Opportunities"],
    responses={404: {"description": "Opportunity not found"}},
)
def read_opportunity_detail(opportunity_id: int, db: DbDep):
    """
    Retrieve the full analysis report for a single opportunity by its ID.
    """
    logger = structlog.get_logger().bind(opportunity_id=opportunity_id)
    db_report = crud.get_opportunity_by_id(db, opportunity_id=opportunity_id)

    if db_report is None:
        logger.warning("Opportunity not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Opportunity not found",
        )

    logger.info("Opportunity detail retrieved successfully")
    # Combine the top-level fields from the ORM model with the
    # nested JSON data from the `report_data` field.
    # Pydantic will automatically parse this combined dictionary.
    if isinstance(db_report.report_data, dict):
        report_data_dict = db_report.report_data
    else:
        logger.warning(
            "report_data is not a dict; using empty dict as fallback",
            actual_type=str(type(db_report.report_data))
        )
        report_data_dict = {}

    response_data = {
        **report_data_dict,
        "id": db_report.id,
        "source_url": db_report.source_post.url if db_report.source_post else None,
        "created_at": db_report.created_at,
        "pain_point_summary": db_report.pain_point_summary,
    }
    return response_data
