import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from app.application.model_settings import ModelSettingsStore, normalized_base
from app.core.providers import Capability, ProviderCallResult
from app.product_platform.contracts import PlatformError


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.TemporaryDirectory()
        self.settings=SimpleNamespace(config_for_route=lambda *args: SimpleNamespace(
            model='configured-model',api_key='secret-test-key',base_url='https://fixture.invalid/v1',
            declared_capabilities=frozenset(Capability)))
        self.store=ModelSettingsStore(Path(self.root.name)/'models.json',self.settings)
    def tearDown(self): self.root.cleanup()
    def body(self):
        value=self.store.projection()
        return {k:value[k] for k in ('revision','profiles','assignments')}

    def test_credentials_stay_private_persist_and_snapshots_do_not_change(self):
        before=self.store.snapshot();body=self.body();body['profiles'][0]['model']='new-model'
        saved=self.store.save(body)
        self.assertNotIn('secret-test-key',json.dumps(saved))
        self.assertNotIn('apiKey',json.dumps(saved))
        self.assertEqual(before.config_for_stage('task1','task1').model,'configured-model')
        self.assertEqual(self.store.snapshot().config_for_stage('task1','task1').model,'new-model')
        self.assertEqual(self.store.path.stat().st_mode & 0o777,0o600)
        reopened=ModelSettingsStore(self.store.path,self.settings)
        self.assertEqual(reopened.snapshot().config_for_stage('task1','task1').api_key,'secret-test-key')
        with self.assertRaises(PlatformError): self.store.save(body)

    def test_changed_destination_does_not_receive_saved_secret(self):
        calls=[]
        self.store.get=lambda url,**kwargs: calls.append((url,kwargs)) or SimpleNamespace(status_code=200,
            json=lambda:{'data':[{'id':'account-model'}]})
        profile=self.body()['profiles'][0];profile['baseUrl']='https://another.invalid/compatible'
        self.assertEqual(self.store.discover(profile)['models'],['account-model'])
        self.assertNotIn('secret-test-key',json.dumps(calls))
        body=self.body();body['profiles'][0]=profile;self.store.save(body)
        self.assertEqual(self.store.snapshot().config_for_stage('task1','task1').api_key,'')

    def test_dynamic_model_id_and_arbitrary_compatible_path_keep_exact_endpoint(self):
        body=self.body();body['profiles'][1].update(model='new-provider/released-today',
            baseUrl='https://generativelanguage.googleapis.com/v1beta/openai')
        body['assignments']['audit']=body['profiles'][1]['id'];self.store.save(body)
        config=self.store.snapshot().config_for_stage('task1','audit')
        self.assertEqual(config.model,'new-provider/released-today')
        self.assertEqual(config.base_url,'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions')

    def test_invalid_routes_and_missing_task1_vision_fail_before_save(self):
        for url in ['http://cloud.invalid/v1','https://user:password@cloud.invalid/v1','https://cloud.invalid/v1?key=secret']:
            with self.subTest(url=url),self.assertRaises(ValueError): normalized_base(url)
        self.assertEqual(normalized_base('http://127.0.0.1:11434/v1/'),'http://127.0.0.1:11434/v1')
        body=self.body();body['profiles'][0]['vision']=False
        with self.assertRaises(ValueError): self.store.save(body)

    def test_connection_test_requires_valid_json_response(self):
        self.store.transport=SimpleNamespace(call=lambda *args: ProviderCallResult(content='{"connected":true}'))
        self.assertTrue(self.store.test(self.body()['profiles'][0])['ok'])
        self.store.transport=SimpleNamespace(call=lambda *args: ProviderCallResult(content='connected'))
        self.assertFalse(self.store.test(self.body()['profiles'][0])['ok'])

    def test_browser_model_routes_are_local_owner_only(self):
        from app.product_composition.local_browser import BrowserRuntime
        from app.core.rag_local_runtime import LocalRagRuntime
        runtime=BrowserRuntime(self.root.name,settings=self.settings,rag_runtime=LocalRagRuntime({}),
            foundation=lambda request:None)
        try:
            token=runtime.login()
            status,value=runtime.dispatch('GET','/settings/models',token,{},'')
            self.assertEqual(status,200);self.assertNotIn('secret-test-key',json.dumps(value))
            other=runtime.platform.identity.create_tenant_admin('Other','other-model@example.invalid','a-safe-test-password')
            token=runtime.platform.identity.login(other.tenant_id,'other-model@example.invalid','a-safe-test-password')['token']
            for method,path in [('GET','/settings/models'),('POST','/settings/models/test'),('POST','/settings/models')]:
                with self.subTest(path=path),self.assertRaises(PlatformError):
                    runtime.dispatch(method,path,token,{},'')
        finally: runtime.rag.close();runtime.platform.close()
