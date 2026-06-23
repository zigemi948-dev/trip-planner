from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import ErrorResponse, TripPlannerError


def register_error_handlers(app: FastAPI) -> None:
    """Register stable JSON error responses for API consumers."""

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": ErrorResponse.VALIDATION_ERROR,
                "message": "Request validation failed.",
                "details": exc.errors(),
                "path": str(request.url.path),
            },
        )

    @app.exception_handler(TripPlannerError)
    async def domain_exception_handler(request: Request, exc: TripPlannerError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": ErrorResponse.DOMAIN_ERROR,
                "message": str(exc),
                "path": str(request.url.path),
            },
        )

    @app.exception_handler(Exception)
    async def internal_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": ErrorResponse.INTERNAL_ERROR,
                "message": "Unexpected server error.",
                "path": str(request.url.path),
            },
        )
