"""Explicitly local/private, same-origin HTTP adapter over accepted C3 services.

Not a public authentication or hosting service. The opt-in local session is
restricted to loopback + exact Host/Origin + a non-simple request header; the C3
session itself is HttpOnly and is never returned to JavaScript.
"""
import argparse
import base64
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import unquote, urlsplit

from app.application.local_task2 import LocalTask2Foundation
from app.application.local_task1 import LocalTask1Foundation
from app.application.browser_learning import BrowserLearningService
from app.core.image_input import validate_image_bytes, _detect_media_type
from app.core.settings import AppSettings
from app.core.rag_local_runtime import LocalRagRuntime
from app.product_platform.bootstrap import create_runtime
from app.product_platform.contracts import ApiRequest, ConsentPurpose, ErrorCode, PlatformError, digest
from app.product_platform.privacy import PRIVACY_POLICY_VERSION
from .service import DraftLoopApplicationService
from .browser_projection import PersistBrowserProjection, read_workspace

PREFIX = "/api/draftloop/v1"
COOKIE = "draftloop_local_session"


class BrowserRuntime:
    def __init__(self, data_root, *, settings=None, rag_runtime=None, foundation=None):
        self.platform = create_runtime(data_root)
        self.rag = rag_runtime or LocalRagRuntime()
        self.platform.service.rag_runtime = self.rag
        self.facade = DraftLoopApplicationService(self.platform.service)
        self.progress = {}
        self.progress_lock = threading.Lock()
        self.active = threading.local()
        from app.application.model_settings import ModelSettingsStore
        configured = settings or AppSettings.load()
        self.models = ModelSettingsStore(self.platform.data_root / 'model-profiles.json', configured)
        from app.application.example_memory import ExampleMemory
        from app.product_platform.contracts import Principal
        self.examples=ExampleMemory(self.platform.service,encoder=getattr(self.rag.provider,'encoder',None))
        if foundation is None:
            def foundation(request):
                # One configuration snapshot per submitted job, never mutable midway through scoring.
                adapter = LocalTask1Foundation if request.task_type == 'task1' else LocalTask2Foundation
                examples=self.examples.retrieve(Principal(request.tenant_id,request.owner_id),request.question) if request.task_type=='task2' else ()
                return adapter(self.models.snapshot(), on_checkpoint=self.checkpoint, rag_runtime=self.rag,
                    reusable_examples=examples)(request)
        self.foundation = foundation
        self.learning = BrowserLearningService(self.platform.service, self.models)
        processor = PersistBrowserProjection(self.platform.service.local_coaching_processor(self.foundation))
        def process(request):
            self.active.submission_id = request.submission_id
            with self.progress_lock:
                self.progress[request.submission_id] = {"stage": "CHART_FACTS" if request.task_type == "task1" else "TASK_UNDERSTANDING", "startedAt": time.time(), "criteria": {}}
            try:
                return processor(request)
            finally:
                self.active.submission_id = None
        self.processor = process
        self.stop = threading.Event()
        self.worker = None
        self.session_lock = threading.Lock()
        self._load_local_account()

    def _load_local_account(self):
        credentials = self.platform.data_root / "local-browser-account.json"
        if credentials.exists():
            self.credentials = json.loads(credentials.read_text())
            email_hash = digest({"kind": "normalized-email", "value": self.credentials["email"].strip().casefold()})
            if self.platform.store.user_by_email_hash(self.credentials["tenantId"], email_hash) is not None:
                return
            # A completed owner deletion also invalidates its server-side login.
            credentials.unlink()
        if not credentials.exists():
            admin = self.platform.identity.create_tenant_admin("Local private workspace",
                "local-admin@draftloop.invalid", secrets.token_urlsafe(32))
            password = secrets.token_urlsafe(32)
            learner = self.platform.identity.create_learner(admin, "local-owner@draftloop.invalid", password)
            self.credentials = {"tenantId": learner.tenant_id, "email": "local-owner@draftloop.invalid", "password": password}
            import os
            fd = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as file:
                json.dump(self.credentials, file)

    def checkpoint(self, event):
        print(json.dumps(event), flush=True)
        sid = getattr(self.active, "submission_id", None)
        if sid:
            with self.progress_lock:
                progress = self.progress.get(sid)
                if progress:
                    progress["stage"] = event["stage"]
                    if "criterion" in event and "status" in event:
                        progress["criteria"][event["criterion"]] = event["status"]
                    if "rescoreTriggered" in event:
                        progress["rescoreTriggered"] = bool(event["rescoreTriggered"])

    def login(self):
        with self.session_lock:
            self._load_local_account()
            return self.platform.identity.login(self.credentials["tenantId"],
                self.credentials["email"], self.credentials["password"])["token"]

    def start_worker(self):
        def run():
            while not self.stop.is_set():
                try:
                    result = self.platform.service.run_one_job("draftloop-local-worker", self.processor, lease_seconds=900)
                    if result:
                        print(json.dumps({"worker": "draftloop-local-worker", "result": result}), flush=True)
                    else:
                        learning = self.learning.run_one()
                        if learning:
                            print(json.dumps({"worker": "draftloop-revision-worker", "result": learning}), flush=True)
                        else:
                            self.stop.wait(0.25)
                except Exception:
                    print('{"worker":"draftloop-local-worker","state":"INTERNAL_FAILURE"}', flush=True)
                    self.stop.wait(1)
        self.worker = threading.Thread(target=run, daemon=True, name="draftloop-c3-worker")
        self.worker.start()

    def dispatch(self, method, path, token, body, idempotency_key):
        principal = self.platform.identity.authenticate(token)
        if path.startswith('/settings/models'):
            email_hash = digest({'kind':'normalized-email','value':self.credentials['email'].strip().casefold()})
            owner = self.platform.store.user_by_email_hash(self.credentials['tenantId'], email_hash)
            if owner is None or (principal.tenant_id,principal.user_id) != (self.credentials['tenantId'],str(owner['user_id'])):
                raise PlatformError(ErrorCode.FORBIDDEN,'仅本地工作区所有者可以修改模型设置。')
            try:
                if method == 'GET' and path == '/settings/models': return 200,self.models.projection()
                if method == 'POST' and path == '/settings/models': return 200,self.models.save(body)
                if method == 'POST' and path == '/settings/models/discover': return 200,self.models.discover(body)
                if method == 'POST' and path == '/settings/models/test': return 200,self.models.test(body)
            except ValueError as exc:
                raise PlatformError(ErrorCode.INVALID_REQUEST,str(exc)) from None
        if method == 'GET' and path == '/learning/memory':
            return 200, self.learning.memory_projection(principal)
        if method == 'GET' and path == '/learning/practice':
            for item in self.platform.service.history(principal):
                if item['state'] != 'COMPLETE': continue
                workspace = read_workspace(self.facade, principal, item['submission_id'])
                report = workspace['presentation']['report']
                if report is None: continue
                source = self.platform.service.writing_source(principal, item['submission_id'])
                return 200, {'submissionId': item['submission_id'], 'question': source['question'],
                    'sections': [s for s in report['sections'] if s['key'] in {'next_actions', 'topic_learning'}]}
            return 200, {'submissionId': None, 'question': None, 'sections': []}
        if method == 'POST' and path == '/learning/memory/correct':
            return 200, self.learning.correct_memory(principal, body)
        match = re.fullmatch(r"/workspaces/(submission_[a-zA-Z0-9]+)/learning(?:/(hints|revisions|source))?", path)
        if match:
            if method == 'GET' and match[2] == 'source':
                source, _ = self.learning.context(principal, match[1])
                return 200, {'source': source}
            if method == 'GET' and match[2] is None:
                return 200, self.learning.read(principal, match[1])
            if method == 'POST' and match[2] == 'hints':
                return 200, self.learning.hint(principal, match[1], body, idempotency_key)
            if method == 'POST' and match[2] == 'revisions':
                return 202, self.learning.submit_revision(principal, match[1], body, idempotency_key)
        if method == "GET" and path == "/privacy":
            return 200, {"policyVersion": PRIVACY_POLICY_VERSION,
                "consents": [self.platform.store.consent(principal, purpose) for purpose in ConsentPurpose]}
        if method == "POST" and path == "/privacy/delete":
            if body.get("confirmation") != "DELETE_MY_DATA":
                raise PlatformError(ErrorCode.INVALID_REQUEST, "Explicit deletion confirmation required.")
            with self.session_lock:
                principal = self.platform.identity.authenticate(token)
                request = self.platform.privacy.request_deletion(principal)
                receipt = self.platform.privacy.execute_deletion(request["deletionId"])
            return 200, {"deletion": receipt}
        match = re.fullmatch(r"/uploads/(upload_[a-zA-Z0-9]+)", path)
        if method == "GET" and match:
            return 200, self.platform.uploads.read(principal, match[1])
        if method == "POST" and path == "/uploads":
            try:
                raw = body.get("dataBase64")
                if not isinstance(raw, str):
                    raise ValueError("Missing upload bytes.")
                data = base64.b64decode(raw, validate=True)
                result = validate_image_bytes(data, expected_media_type=body.get("mediaType"))
                if not result.ok:
                    raise ValueError("Image validation failed.")
            except (ValueError, TypeError):
                raise PlatformError(ErrorCode.UNSAFE_UPLOAD, "Upload must be a valid PNG, JPEG or WebP image within 10 MiB.") from None
            response = self.platform.router.handle(ApiRequest("POST", "/v1/uploads",
                {"Authorization": "Bearer " + token}, {"mediaType": result.image.metadata.media_type, "data": data}))
            return response.status, response.body
        match = re.fullmatch(r"/workspaces/(submission_[a-zA-Z0-9]+)", path)
        if method == "GET" and match:
            sid = match[1]
            workspace = read_workspace(self.facade, principal, sid)
            workspace["source"] = self.platform.service.writing_source(principal, sid)
            workspace["assessmentIssues"] = self.platform.service.assessment_issues(principal, sid)
            with self.progress_lock:
                workspace["progress"] = json.loads(json.dumps(self.progress.get(sid)))
            workspace["capabilities"] = {"downloadPdf": workspace["presentation"]["state"] == "NORMAL"}
            if workspace['presentation']['state'] == 'NORMAL':
                self.learning.record_report_access(principal, sid)
            workspace["workspaceSha256"] = digest({k: v for k, v in workspace.items() if k != "workspaceSha256"})
            return 200, workspace
        match = re.fullmatch(r"/workspaces/(submission_[a-zA-Z0-9]+)/pdf", path)
        if method == "GET" and match:
            from .browser_pdf import render_report_pdf
            workspace = read_workspace(self.facade, principal, match[1])
            report = workspace["presentation"]["report"]
            if report is None:
                raise PlatformError(ErrorCode.CONFLICT, "Normal report required for PDF export.")
            source = self.platform.service.writing_source(principal, match[1])
            self.learning.record_report_access(principal, match[1])
            image_bytes = self.platform.uploads.read(principal, source['uploadId']) if source.get('uploadId') else None
            return 200, render_report_pdf(report, source, image_bytes=image_bytes)
        allowed = ((method, path) in {("GET", "/history"), ("POST", "/submissions"),
                   ("GET", "/privacy/export"), ("POST", "/privacy/consent")}
                   or (method == "GET" and re.fullmatch(r"/submissions/submission_[a-zA-Z0-9]+", path))
                   or (method == "POST" and re.fullmatch(r"/submissions/submission_[a-zA-Z0-9]+/cancel", path)))
        if not allowed:
            raise PlatformError(ErrorCode.NOT_FOUND, "Browser capability not available.")
        response = self.platform.router.handle(ApiRequest(method, "/v1" + path,
            {"Authorization": "Bearer " + token, "Idempotency-Key": idempotency_key}, body))
        if method == "GET" and path == "/history" and response.status == 200:
            return 200, {"history": [{"submissionId": item["submission_id"], "state": item["state"]}
                                     | {"createdAt": item["created_at"],
                                        "taskType": self.platform.service.writing_source(principal, item["submission_id"])["taskType"],
                                        "title": self.platform.service.writing_source(principal, item["submission_id"])["question"][:160]}
                                     for item in response.body["history"]]}
        return response.status, response.body


def handler_for(runtime, *, origin, client_root, allow_local_session=False):
    client_root = Path(client_root).resolve()
    host = urlsplit(origin).netloc

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # No student text, cookies, request bodies or local paths in access logs.

        def respond(self, status, body, *, cookie=None, content_type="application/json; charset=utf-8"):
            data = json.dumps(body, ensure_ascii=False).encode() if not isinstance(body, bytes) else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if cookie:
                self.send_header("Set-Cookie", f"{COOKIE}={cookie}; HttpOnly; SameSite=Strict; Path={PREFIX}; Max-Age=43200")
            if content_type == "application/pdf":
                self.send_header("Content-Disposition", 'attachment; filename="DraftLoop-report.pdf"')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.handle_request("GET")

        def do_POST(self):
            self.handle_request("POST")

        def handle_request(self, method):
            try:
                if self.headers.get("Host") != host or self.client_address[0] != "127.0.0.1":
                    raise PlatformError(ErrorCode.FORBIDDEN, "Loopback origin required.")
                path = urlsplit(self.path).path
                if not path.startswith(PREFIX + "/"):
                    if method != "GET":
                        raise PlatformError(ErrorCode.NOT_FOUND, "Not found.")
                    relative = "index.html" if path == "/" else unquote(path).lstrip("/")
                    candidate = (client_root / relative).resolve()
                    if not candidate.is_relative_to(client_root) or not candidate.is_file():
                        raise PlatformError(ErrorCode.NOT_FOUND, "Not found.")
                    return self.respond(200, candidate.read_bytes(),
                        content_type=mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
                if (self.headers.get("X-DraftLoop-Client") != "browser-v1"
                    or self.headers.get("Origin") not in (None, origin)
                    or self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none")):
                    raise PlatformError(ErrorCode.FORBIDDEN, "Same-origin browser request required.")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= (14 * 1024 * 1024 if path == PREFIX + "/uploads" else 100000):
                    raise PlatformError(ErrorCode.INVALID_REQUEST, "Request too large.")
                if method == "POST" and self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise PlatformError(ErrorCode.INVALID_REQUEST, "JSON required.")
                body = json.loads(self.rfile.read(size)) if size else {}
                if not isinstance(body, dict):
                    raise PlatformError(ErrorCode.INVALID_REQUEST, "JSON object required.")
                cookies = SimpleCookie(self.headers.get("Cookie", ""))
                token = cookies[COOKIE].value if COOKIE in cookies else ""
                route = path[len(PREFIX):]
                if method == "POST" and route == "/session" and allow_local_session:
                    try:
                        runtime.platform.identity.authenticate(token)
                    except PlatformError:
                        token = runtime.login()
                    return self.respond(200, {"mode": "LOCAL_PRIVATE_RESEARCH", "apiVersion": "v1"}, cookie=token)
                status, result = runtime.dispatch(method, route, token, body, self.headers.get("Idempotency-Key", ""))
                media_type = "application/json; charset=utf-8"
                if isinstance(result, bytes):
                    media_type = (_detect_media_type(result) or "application/octet-stream") if route.startswith("/uploads/") else "application/pdf"
                self.respond(status, result, content_type=media_type)
            except PlatformError as exc:
                self.respond(exc.http_status, {"error": {"code": exc.code.value, "message": exc.public_message}})
            except (ValueError, TypeError):
                self.respond(400, {"error": {"code": "INVALID_REQUEST", "message": "Request rejected."}})
            except Exception:
                self.respond(500, {"error": {"code": "INTERNAL_FAILURE", "message": "Request could not be completed."}})

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--client-root", default="frontend/dist/client")
    parser.add_argument("--port", type=int, default=4174)
    parser.add_argument("--allow-local-session", action="store_true", required=True)
    args = parser.parse_args()
    runtime = BrowserRuntime(args.data_root)
    origin = f"http://127.0.0.1:{args.port}"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(runtime, origin=origin,
        client_root=args.client_root, allow_local_session=args.allow_local_session))
    runtime.start_worker()
    print(json.dumps({"origin": origin, "scope": "LOCAL_PRIVATE_ONLY", "state": "READY"}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop.set()
        server.server_close()
        if runtime.worker:
            runtime.worker.join(timeout=2)
        if not runtime.worker or not runtime.worker.is_alive():
            runtime.rag.close()
            runtime.platform.close()


if __name__ == "__main__":
    main()
