"""Versioned in-process and WSGI API adapters for C3 application services."""
from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any, Mapping

from .contracts import (
    API_VERSION,
    ApiRequest,
    ApiResponse,
    ConsentPurpose,
    ErrorCode,
    PlatformError,
    SubmitCommand,
    error_response,
)
from .identity import IdentityService
from .privacy import PrivacyService
from .service import ProductPlatformService
from .uploads import UploadStorage


API_SCHEMA_VERSION = "c3-api-schema-v1"
MAX_JSON_BODY_BYTES = 15 * 1024 * 1024
_SUBMISSION_PATH = re.compile(r"^/v1/submissions/([^/]+)$")
_SUBMISSION_ACTION = re.compile(r"^/v1/submissions/([^/]+)/(report|revisions|hints|cancel)$")


def api_schema_contract() -> Mapping[str, Any]:
    return {
        "schemaVersion": API_SCHEMA_VERSION,
        "apiVersion": API_VERSION,
        "routes": {
            "POST /v1/sessions": "create an expiring session",
            "DELETE /v1/session": "revoke the active session",
            "POST /v1/uploads": "create an owner-scoped validated image upload",
            "POST /v1/submissions": "idempotently create one submission intent and durable job",
            "GET /v1/submissions/{id}": "read owner-scoped submission and distinct job state",
            "GET /v1/submissions/{id}/report": "read a complete report only",
            "POST /v1/submissions/{id}/revisions": "append an immutable revision artifact",
            "POST /v1/submissions/{id}/hints": "append an immutable hint event",
            "POST /v1/submissions/{id}/cancel": "request durable cancellation",
            "GET /v1/history": "read owner-scoped submission history",
            "GET /v1/runtime/rag": "read authenticated, redacted RAG runtime capability state",
            "GET|POST /v1/memory": "read or append owner-scoped memory artifacts",
            "POST /v1/support/cases": "create structured privacy-safe support/bad-case intake",
            "POST /v1/privacy/consent": "grant or revoke versioned purpose consent",
            "GET /v1/privacy/export": "export the complete authenticated subject bundle",
            "POST /v1/privacy/delete": "request asynchronous idempotent subject deletion",
        },
        "errorEnvelope": {
            "apiVersion": API_VERSION,
            "error": {"code": "TYPED_CODE", "message": "public-safe message"},
        },
    }


class C3ApiRouter:
    def __init__(
        self,
        service: ProductPlatformService,
        identity: IdentityService,
        privacy: PrivacyService,
        *,
        uploads: UploadStorage | None = None,
    ) -> None:
        self.service = service
        self.identity = identity
        self.privacy = privacy
        self.uploads = uploads

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str | None:
        wanted = name.casefold()
        for key, value in headers.items():
            if key.casefold() == wanted:
                return str(value)
        return None

    def _principal(self, request: ApiRequest):
        authorization = self._header(request.headers, "Authorization") or ""
        if not authorization.startswith("Bearer "):
            raise PlatformError(ErrorCode.UNAUTHENTICATED, "Bearer authentication is required.")
        return self.identity.authenticate(authorization[7:].strip())

    def handle(self, request: ApiRequest) -> ApiResponse:
        try:
            if not request.path.startswith("/v1/") and request.path != "/v1":
                raise PlatformError(ErrorCode.VERSION_UNSUPPORTED, "API version is unsupported.")
            return self._dispatch(request)
        except PlatformError as exc:
            return error_response(exc)
        except Exception:
            return error_response(PlatformError(
                ErrorCode.INTERNAL_FAILURE,
                "The request could not be completed.",
            ))

    def _dispatch(self, request: ApiRequest) -> ApiResponse:
        method = request.method.upper()
        body = dict(request.body)

        if method == "GET" and request.path == "/v1/health":
            return ApiResponse(200, {"status": "ok", "schemaVersion": API_SCHEMA_VERSION})
        if method == "POST" and request.path == "/v1/sessions":
            session = self.identity.login(
                str(body.get("tenantId", "")),
                str(body.get("email", "")),
                str(body.get("password", "")),
            )
            return ApiResponse(201, {"session": session})

        principal = self._principal(request)
        if method == "GET" and request.path == "/v1/runtime/rag":
            return ApiResponse(200, {"rag": self.service.rag_status(principal)})
        if method == "DELETE" and request.path == "/v1/session":
            self.identity.logout(principal)
            return ApiResponse(204, {})
        if method == "POST" and request.path == "/v1/uploads":
            if self.uploads is None:
                raise PlatformError(ErrorCode.CONFLICT, "Upload storage is unavailable.")
            data = body.get("data")
            if not isinstance(data, bytes):
                raise PlatformError(ErrorCode.INVALID_REQUEST, "Upload bytes are required.")
            created = self.uploads.save(
                principal,
                media_type=str(body.get("mediaType", "")),
                data=data,
            )
            return ApiResponse(201, {"upload": created})
        if method == "POST" and request.path == "/v1/submissions":
            idempotency_key = self._header(request.headers, "Idempotency-Key") or ""
            command = SubmitCommand(
                str(body.get("taskType", "")),
                str(body.get("question", "")),
                str(body.get("candidateScript", "")),
                idempotency_key,
                float(body["targetBand"]) if body.get("targetBand") is not None else None,
                str(body["uploadId"]) if body.get("uploadId") is not None else None,
            )
            created = self.service.submit(principal, command)
            return ApiResponse(202 if created["created"] else 200, {"submission": created})
        if method == "GET" and request.path == "/v1/history":
            return ApiResponse(200, {"history": list(self.service.history(principal))})
        if request.path == "/v1/memory":
            if method == "GET":
                return ApiResponse(200, {"memory": list(self.service.memory(principal))})
            if method == "POST":
                return ApiResponse(201, {"memory": self.service.append_memory(principal, body)})
        if method == "POST" and request.path == "/v1/support/cases":
            result = self.service.create_support_case(
                principal,
                category=str(body.get("category", "")),
                failure_code=str(body["failureCode"]) if body.get("failureCode") is not None else None,
                submission_id=str(body["submissionId"]) if body.get("submissionId") is not None else None,
            )
            return ApiResponse(201, {"case": result})
        if method == "POST" and request.path == "/v1/privacy/consent":
            try:
                purpose = ConsentPurpose(str(body.get("purpose", "")))
            except ValueError as exc:
                raise PlatformError(ErrorCode.INVALID_REQUEST, "Consent purpose is invalid.") from exc
            if not isinstance(body.get("granted"), bool):
                raise PlatformError(ErrorCode.INVALID_REQUEST, "Consent granted must be boolean.")
            result = self.service.set_consent(
                principal,
                purpose,
                granted=bool(body["granted"]),
                policy_version=str(body.get("policyVersion", "")),
            )
            return ApiResponse(200, {"consent": result})
        if method == "GET" and request.path == "/v1/privacy/export":
            return ApiResponse(200, {"export": self.privacy.export_subject(principal)})
        if method == "POST" and request.path == "/v1/privacy/delete":
            return ApiResponse(202, {"deletion": self.privacy.request_deletion(principal)})

        match = _SUBMISSION_PATH.match(request.path)
        if match and method == "GET":
            return ApiResponse(200, {"submission": self.service.status(principal, match.group(1))})
        action = _SUBMISSION_ACTION.match(request.path)
        if action:
            submission_id, name = action.groups()
            if name == "report" and method == "GET":
                return ApiResponse(200, {"report": self.service.report(principal, submission_id)})
            if name == "revisions" and method == "POST":
                return ApiResponse(201, {"revision": self.service.add_revision(principal, submission_id, body)})
            if name == "hints" and method == "POST":
                return ApiResponse(201, {"hintEvent": self.service.add_hint(principal, submission_id, body)})
            if name == "cancel" and method == "POST":
                return ApiResponse(202, {"cancellation": self.service.cancel(principal, submission_id)})
        raise PlatformError(ErrorCode.NOT_FOUND, "Route not found.")


class C3WSGIApplication:
    """Minimal standard-library server adapter; application semantics stay in the router."""

    def __init__(self, router: C3ApiRouter) -> None:
        self.router = router

    def __call__(self, environ, start_response):
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
            if length < 0 or length > MAX_JSON_BODY_BYTES:
                raise PlatformError(ErrorCode.INVALID_REQUEST, "Request body is too large.")
            raw = environ["wsgi.input"].read(length) if length else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(body, dict):
                raise PlatformError(ErrorCode.INVALID_REQUEST, "JSON object body is required.")
            if environ.get("PATH_INFO") == "/v1/uploads" and "dataBase64" in body:
                try:
                    body["data"] = base64.b64decode(str(body.pop("dataBase64")), validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise PlatformError(ErrorCode.INVALID_REQUEST, "Upload encoding is invalid.") from exc
            headers = {
                key[5:].replace("_", "-"): value
                for key, value in environ.items()
                if key.startswith("HTTP_")
            }
            response = self.router.handle(ApiRequest(
                environ.get("REQUEST_METHOD", "GET"),
                environ.get("PATH_INFO", "/"),
                headers,
                body,
            ))
        except PlatformError as exc:
            response = error_response(exc)
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = error_response(PlatformError(ErrorCode.INVALID_REQUEST, "Request JSON is invalid."))
        payload = json.dumps(response.public_body(), ensure_ascii=False, sort_keys=True).encode("utf-8")
        start_response(
            f"{response.status} {_reason(response.status)}",
            [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(payload)))],
        )
        return [payload]


def _reason(status: int) -> str:
    return {
        200: "OK",
        201: "Created",
        202: "Accepted",
        204: "No Content",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        422: "Unprocessable Entity",
        429: "Too Many Requests",
        500: "Internal Server Error",
    }.get(status, "Response")
