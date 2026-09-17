"""Browser API acceptance seams. Synthetic assessment inputs are test-only."""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import requests

from app.core.rag_local_runtime import LocalRagRuntime
from app.core.rag_historical import package_sentinel
from app.product_composition.local_browser import BrowserRuntime, handler_for, PREFIX
from app.product_platform.contracts import PlatformError, ProcessingOutcome, SubmissionState
from scripts.validate_local_private_rag import smoke_inputs


class BrowserC3ProjectionTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("RUN_LOOPBACK_HTTP_TESTS") == "1", "Explicit local HTTP test opt-in required")
    def test_http_cookie_auth_origin_host_and_raw_report_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = BrowserRuntime(root, rag_runtime=LocalRagRuntime({}), foundation=lambda request: None)
            server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
            origin = "http://127.0.0.1:" + str(server.server_port)
            server.RequestHandlerClass = handler_for(runtime, origin=origin, client_root=root, allow_local_session=True)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            session = requests.Session()
            session.trust_env = False
            session.headers["X-DraftLoop-Client"] = "browser-v1"
            try:
                self.assertEqual(session.get(origin + PREFIX + "/history").status_code, 401)
                self.assertEqual(session.post(origin + PREFIX + "/session", json={}, headers={"Origin": "https://untrusted.invalid"}).status_code, 403)
                self.assertEqual(session.post(origin + PREFIX + "/session", json={}, headers={"Host": "untrusted.invalid"}).status_code, 403)
                response = session.post(origin + PREFIX + "/session", json={}, headers={"Origin": origin})
                self.assertEqual(response.status_code, 200)
                self.assertIn("HttpOnly", response.headers["Set-Cookie"])
                self.assertIn("SameSite=Strict", response.headers["Set-Cookie"])
                self.assertNotIn("token", response.text)
                self.assertEqual(session.get(origin + PREFIX + "/history").status_code, 200)
                self.assertEqual(session.get(origin + PREFIX + "/submissions/submission_test/report").status_code, 404)
                self.assertEqual(session.get(origin + "/%2e%2e/CONTEXT.md").status_code, 404)
                self.assertNotIn("Access-Control-Allow-Origin", response.headers)
            finally:
                session.close()
                server.shutdown()
                server.server_close()
                worker.join()
                runtime.rag.close()
                runtime.platform.close()

    @unittest.skipUnless(os.environ.get("RUN_REAL_PRIVATE_RAG_TESTS") == "1", "Explicit real-package opt-in required")
    def test_real_private_retrieval_reaches_persisted_browser_evidence_projection(self):
        with tempfile.TemporaryDirectory() as root:
            inputs = smoke_inputs()
            runtime = BrowserRuntime(root, foundation=lambda request: inputs)
            try:
                before = package_sentinel(runtime.rag.validation.runtime_handle.authorized.root)
                token = runtime.login()
                _, created = runtime.dispatch("POST", "/submissions", token, {
                    "taskType": "task2", "question": inputs.submission.question,
                    "candidateScript": inputs.submission.essay_version.original_text,
                }, "real-browser-rag-controlled-assessment")
                job = runtime.platform.service.run_one_job("real-browser-rag-worker", runtime.processor)
                self.assertEqual(job["state"], "SUCCEEDED")
                _, workspace = runtime.dispatch("GET", "/workspaces/" + created["submission"]["submissionId"], token, {}, "")
                report = workspace["presentation"]["report"]
                self.assertEqual(workspace["rag"]["status"], "ENABLED")
                self.assertEqual(report["rag"]["status"], "ENABLED")
                self.assertEqual(report["rag"]["mode"], "LOCAL_PRIVATE_RESEARCH")
                self.assertEqual(report["rag"]["packageIdentity"], "ielts-frozen-retrieval-v1")
                self.assertEqual(report["rag"]["evidenceState"], "SUFFICIENT")
                self.assertIs(report["rag"]["scoreAuthority"], False)
                references = [r for s in report["sections"] for r in s["records"] if r["authority"] == "RAG_EVIDENCE"]
                self.assertEqual(len(references), 2)
                self.assertEqual(report["lockedScoreSha256"], inputs.locked_score.snapshot_sha256)
                for forbidden in ("sourceIds", "object_id", "sourcePaths", "dense.faiss", "EMPIRICAL_RAG_PACKAGE_PATH", "/Users/"):
                    self.assertNotIn(forbidden, json.dumps(workspace))
                runtime.rag.validation.runtime_handle.assert_unchanged()
                self.assertEqual(package_sentinel(runtime.rag.validation.runtime_handle.authorized.root), before)
            finally:
                runtime.rag.close()
                runtime.platform.close()

    def test_non_complete_worker_results_never_render_or_export_normal_reports(self):
        for state in (SubmissionState.PARTIAL, SubmissionState.REVIEW_REQUIRED, SubmissionState.FAILED):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as root:
                runtime = BrowserRuntime(root, rag_runtime=LocalRagRuntime({}),
                    foundation=lambda request: ProcessingOutcome(state, None, "CONTROLLED_NON_COMPLETE"))
                try:
                    token = runtime.login()
                    _, created = runtime.dispatch("POST", "/submissions", token, {
                        "taskType": "task2", "question": "Discuss public transport.",
                        "candidateScript": "Public transport can improve access to work.",
                    }, "browser-state-" + state.value)
                    runtime.platform.service.run_one_job("state-worker", runtime.processor)
                    _, result = runtime.dispatch("GET", "/workspaces/" + created["submission"]["submissionId"], token, {}, "")
                    self.assertEqual(result["semanticResult"]["state"], state.value)
                    self.assertIsNone(result["presentation"]["report"])
                    self.assertEqual(result["exportArtifact"]["state"], "BLOCKED")
                finally:
                    runtime.rag.close()
                    runtime.platform.close()

    def test_other_principal_cannot_read_workspace_or_raw_report(self):
        with tempfile.TemporaryDirectory() as root:
            inputs = smoke_inputs()
            runtime = BrowserRuntime(root, rag_runtime=LocalRagRuntime({}), foundation=lambda request: inputs)
            try:
                token = runtime.login()
                _, created = runtime.dispatch("POST", "/submissions", token, {
                    "taskType": "task2", "question": inputs.submission.question,
                    "candidateScript": inputs.submission.essay_version.original_text,
                }, "browser-private-owner")
                sid = created["submission"]["submissionId"]
                other = runtime.platform.identity.create_tenant_admin("Other", "other@test.invalid", "other password long enough")
                other_token = runtime.platform.identity.login(other.tenant_id, "other@test.invalid", "other password long enough")["token"]
                with self.assertRaises(PlatformError):
                    runtime.dispatch("GET", "/workspaces/" + sid, other_token, {}, "")
                with self.assertRaises(PlatformError):
                    runtime.dispatch("GET", "/submissions/" + sid + "/report", token, {}, "")
            finally:
                runtime.rag.close()
                runtime.platform.close()

    def test_submission_worker_workspace_preserve_locked_identity(self):
        with tempfile.TemporaryDirectory() as root:
            inputs = smoke_inputs()
            runtime = BrowserRuntime(root, rag_runtime=LocalRagRuntime({}), foundation=lambda request: inputs)
            try:
                token = runtime.login()
                status, created = runtime.dispatch("POST", "/submissions", token, {
                    "taskType": "task2", "question": inputs.submission.question,
                    "candidateScript": inputs.submission.essay_version.original_text,
                }, "controlled-browser-test-001")
                self.assertEqual(status, 202)
                submission_id = created["submission"]["submissionId"]
                _, waiting = runtime.dispatch("GET", "/workspaces/" + submission_id, token, {}, "")
                self.assertIsNone(waiting["presentation"]["report"])
                job = runtime.platform.service.run_one_job("test-browser-worker", runtime.processor)
                self.assertEqual(job["state"], "SUCCEEDED")
                _, result = runtime.dispatch("GET", "/workspaces/" + submission_id, token, {}, "")
                report = result["presentation"]["report"]
                self.assertEqual(report["lockedScoreSha256"], inputs.locked_score.snapshot_sha256)
                self.assertEqual(report["overallBand"], inputs.locked_score.overall_band)
                self.assertEqual(report["rag"]["status"], "DISABLED")
                self.assertEqual(result["rag"]["status"], "DISABLED")
                self.assertEqual(result["exportArtifact"]["state"], "BLOCKED")
                serialized = json.dumps(result)
                for forbidden in ("sourceIds", "EMPIRICAL_RAG_PACKAGE_PATH", "/Users/", "embeddings", "executionIdentity"):
                    self.assertNotIn(forbidden, serialized)
            finally:
                runtime.rag.close()
                runtime.platform.close()
