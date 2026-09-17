import json
import unittest
from unittest.mock import Mock
import requests
from app.core.providers import OpenAICompatibleTransport, ProviderCallRequest, ProviderFailureCode, resolve_route, route_for, endpoint_for

class Response:
    status_code=200
    headers={}
    def __init__(self,lines): self.lines=lines;self.closed=False
    def iter_lines(self,**kwargs):
        for line in self.lines:
            if isinstance(line,Exception): raise line
            yield line
    def close(self): self.closed=True

def event(content='',finish=None):
    return 'data: '+json.dumps({'choices':[{'delta':{'content':content},'finish_reason':finish}]})

class ProviderRecoveryTests(unittest.TestCase):
    def call(self,response,**kw):
        self.post=Mock(side_effect=response) if isinstance(response,list) else Mock(return_value=response)
        contract=resolve_route(route_for('task2','criterion_scoring'),provider_id='test',model_id='test',api_key='hidden',base_url='https://example.test/v1').contract
        return OpenAICompatibleTransport(post=self.post,sleep=lambda _:None).call(contract,ProviderCallRequest(messages=[],display_name='test',require_json_object=True,**kw))
    def test_done_stops_before_reset_and_keeps_unicode(self):
        r=Response([event('{"text":"中文"}').encode(), 'data: [DONE]', requests.ConnectionError('private')])
        result=self.call(r)
        self.assertTrue(result.ok);self.assertEqual(json.loads(result.content),{'text':'中文'})
        self.assertEqual(self.post.call_count,1);self.assertTrue(r.closed)
    def test_chunked_disconnect_retries_once(self):
        with self.assertLogs('app.core.providers',level='WARNING') as captured:
            result=self.call(Response([event('{'),requests.exceptions.ChunkedEncodingError('private')]))
        self.assertEqual(result.failure.code,ProviderFailureCode.NETWORK_ERROR)
        self.assertEqual(result.attempts,2);self.assertNotIn('private',result.failure.message)
        self.assertIn('phase=response',captured.output[0]);self.assertNotIn('private',' '.join(captured.output))
    def test_terminal_choice_stops_before_gateway_disconnect(self):
        result=self.call(Response([event('{"ok":true}', 'stop'), requests.exceptions.ChunkedEncodingError('private')]))
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts,1)
    def test_insufficient_balance_is_not_retried_or_mislabelled_network(self):
        response=Response([])
        response.status_code=429
        response.json=lambda: {'error': {'code': '1113', 'message': 'private information'}}
        result=self.call(response)
        self.assertEqual(result.failure.code,ProviderFailureCode.QUOTA_EXCEEDED)
        self.assertEqual(result.attempts,1)
        self.assertNotIn('private',result.failure.message)
    def test_disconnect_before_response_retries_and_recovers(self):
        with self.assertLogs('app.core.providers',level='WARNING') as captured:
            result=self.call([requests.exceptions.ChunkedEncodingError('private'),Response([event('{"ok":true}'),'data: [DONE]'])])
        self.assertTrue(result.ok);self.assertEqual(result.attempts,2)
        self.assertIn('phase=connect',captured.output[0]);self.assertNotIn('private',' '.join(captured.output))
    def test_output_limit_is_not_mislabelled_json_error(self):
        result=self.call(Response([event('{"partial":','length'),'data: [DONE]']))
        self.assertEqual(result.failure.code,ProviderFailureCode.TRUNCATED_RESPONSE)
    def test_complete_fence_only_is_unwrapped(self):
        self.assertTrue(self.call(Response([event('```json\n{"ok":true}\n```'),'data: [DONE]'])).ok)
        self.assertFalse(self.call(Response([event('Here is {"ok":true}'),'data: [DONE]'])).ok)
    def test_json_repair_owner_receives_invalid_content(self):
        result=self.call(Response([event('{unfinished'),'data: [DONE]']),validate_json_object=False)
        self.assertTrue(result.ok);self.assertEqual(result.content,'{unfinished')
        self.assertEqual(self.post.call_args.kwargs['json']['response_format'],{'type':'json_object'})
    def test_malformed_stream_is_not_silently_dropped(self):
        result=self.call(Response(['data: nonsense','',event('{"ok":true}'),'data: [DONE]']))
        self.assertEqual(result.failure.code,ProviderFailureCode.SCHEMA_ERROR)
    def test_error_event_has_no_raw_leak(self):
        result=self.call(Response(['data: {"error":{"code":"1302","message":"private"}}']))
        self.assertEqual(result.failure.code,ProviderFailureCode.HTTP_ERROR)
        self.assertEqual(result.attempts,2);self.assertNotIn('private',result.failure.message)
    def test_non_stream_json_envelope_from_stream_request(self):
        r=Response([]);r.headers={'Content-Type':'application/json'}
        r.json=lambda:{'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]}
        self.assertTrue(self.call(r).ok)
    def test_versioned_compatible_base_url(self):
        self.assertEqual(endpoint_for('https://open.bigmodel.cn/api/paas/v4/'),'https://open.bigmodel.cn/api/paas/v4/chat/completions')
