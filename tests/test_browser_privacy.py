import tempfile
import unittest

from app.core.rag_local_runtime import LocalRagRuntime
from app.product_composition.local_browser import BrowserRuntime
from app.product_platform.contracts import PlatformError


class BrowserPrivacyTests(unittest.TestCase):
    def test_export_consent_and_explicit_deletion_reset_only_owned_data(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = BrowserRuntime(root, foundation=lambda request: None, rag_runtime=LocalRagRuntime({}))
            try:
                token = runtime.login()
                _, settings = runtime.dispatch('GET', '/privacy', token, {}, '')
                self.assertTrue(all(not item['granted'] for item in settings['consents']))
                runtime.dispatch('POST', '/privacy/consent', token, {'purpose': 'TRAINING', 'granted': True, 'policyVersion': settings['policyVersion']}, '')
                runtime.dispatch('POST', '/submissions', token, {'taskType': 'task2', 'question': 'Discuss transport.', 'candidateScript': 'My private draft.'}, 'privacy-fixture')
                _, exported = runtime.dispatch('GET', '/privacy/export', token, {}, '')
                self.assertIn('My private draft.', str(exported))
                other = runtime.platform.identity.create_tenant_admin('Other', 'other@example.invalid', 'test-password-long')
                other_token = runtime.platform.identity.login(other.tenant_id, 'other@example.invalid', 'test-password-long')['token']
                runtime.dispatch('POST', '/submissions', other_token, {'taskType': 'task2', 'question': 'Another question.', 'candidateScript': 'Other private draft.'}, 'other-fixture')
                with self.assertRaises(PlatformError): runtime.dispatch('POST', '/privacy/delete', token, {}, '')
                _, receipt = runtime.dispatch('POST', '/privacy/delete', token, {'confirmation': 'DELETE_MY_DATA'}, '')
                self.assertEqual(receipt['deletion']['state'], 'COMPLETE')
                with self.assertRaises(PlatformError): runtime.dispatch('GET', '/history', token, {}, '')
                self.assertEqual(len(runtime.dispatch('GET', '/history', other_token, {}, '')[1]['history']), 1)
                fresh_token = runtime.login()
                self.assertEqual(runtime.dispatch('GET', '/history', fresh_token, {}, '')[1]['history'], [])
                self.assertTrue(all(not x['granted'] for x in runtime.dispatch('GET', '/privacy', fresh_token, {}, '')[1]['consents']))
            finally:
                runtime.rag.close(); runtime.platform.close()
