"""HTTP routes for the server-side scan archive."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from starlette.concurrency import run_in_threadpool

from app.features.scans.errors import ScanError
from app.features.scans.schemas import (
    ScanClassificationFilter,
    ScanClearResponse,
    ScanCreateRequest,
    ScanDetail,
    ScanListResponse,
    ScanSort,
    SortOrder,
)
from app.features.scans.service import ScanService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scans", tags=["scans"])
SQLITE_MAX_INTEGER = 9_223_372_036_854_775_807


def get_scans_service(request: Request) -> ScanService:
    """Return the service created by the application lifespan."""
    service = getattr(request.app.state, "scans_service", None)
    if service is None:
        raise RuntimeError("Application lifespan has not initialized scan storage.")
    return service


async def _run_scan_operation(operation, *args, **kwargs):
    try:
        return await run_in_threadpool(operation, *args, **kwargs)
    except ScanError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except Exception as error:
        logger.error(
            "Unexpected scan archive request failure (%s)",
            type(error).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail=ScanError.default_message,
        ) from error


@router.post(
    "",
    response_model=ScanDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_scan(
    request: Request,
    response: Response,
    payload: ScanCreateRequest,
    service: Annotated[ScanService, Depends(get_scans_service)],
) -> ScanDetail:
    """Save one immutable scan result without image data."""
    record = await _run_scan_operation(service.create, payload)
    response.headers["Location"] = request.url_for("get_scan", scan_id=str(record.id)).path
    return record


@router.get("", response_model=ScanListResponse)
async def list_scans(
    service: Annotated[ScanService, Depends(get_scans_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=SQLITE_MAX_INTEGER)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
    classification: Annotated[
        ScanClassificationFilter | None,
        Query(),
    ] = None,
    sort: ScanSort = ScanSort.SCANNED_AT,
    order: SortOrder = SortOrder.DESCENDING,
) -> ScanListResponse:
    """Search, filter, sort, and paginate saved scans."""
    return await _run_scan_operation(
        service.list,
        limit=limit,
        offset=offset,
        query=q,
        classification=classification,
        sort=sort,
        order=order,
    )


@router.delete("", response_model=ScanClearResponse)
async def clear_scans(
    service: Annotated[ScanService, Depends(get_scans_service)],
) -> ScanClearResponse:
    """Clear the complete unowned archive before authentication exists."""
    return await _run_scan_operation(service.clear)


@router.get("/{scan_id}", response_model=ScanDetail, name="get_scan")
async def get_scan(
    scan_id: UUID,
    service: Annotated[ScanService, Depends(get_scans_service)],
) -> ScanDetail:
    """Return one complete scan including text and structured fields."""
    return await _run_scan_operation(service.get, scan_id)


@router.delete("/{scan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scan(
    scan_id: UUID,
    service: Annotated[ScanService, Depends(get_scans_service)],
) -> Response:
    """Delete one scan or return 404 when it does not exist."""
    await _run_scan_operation(service.delete, scan_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
