"""Local model profiles, atomic secret storage, discovery and capability probes."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

import requests

from app.core.providers import Capability, OpenAICompatibleTransport, ProviderCallRequest, resolve_route, route_for
from app.core.settings import ModelConfig, TEXT_ROUTE_CAPABILITIES
from app.product_platform.contracts import ErrorCode, PlatformError

# Model IDs are discovered from each account at runtime; users may enter any ID.
# Sources and limitations are recorded in docs/runbooks/model-configuration.md.
PROVIDERS = [
    {'id': 'openai', 'name': 'OpenAI', 'baseUrl': 'https://api.openai.com/v1', 'kind': 'cloud'},
    {'id': 'anthropic', 'name': 'Anthropic · Claude', 'baseUrl': 'https://api.anthropic.com/v1', 'kind': 'cloud'},
    {'id': 'google', 'name': 'Google · Gemini', 'baseUrl': 'https://generativelanguage.googleapis.com/v1beta/openai', 'kind': 'cloud'},
    {'id': 'deepseek', 'name': 'DeepSeek', 'baseUrl': 'https://api.deepseek.com/v1', 'kind': 'cloud'},
    {'id': 'qwen-cn', 'name': '阿里云百炼 · 中国', 'baseUrl': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'kind': 'cloud'},
    {'id': 'qwen-intl', 'name': '阿里云百炼 · 国际', 'baseUrl': 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1', 'kind': 'cloud'},
    {'id': 'zhipu', 'name': '智谱 · GLM', 'baseUrl': 'https://open.bigmodel.cn/api/paas/v4', 'kind': 'cloud'},
    {'id': 'moonshot', 'name': 'Moonshot · Kimi', 'baseUrl': 'https://api.moonshot.ai/v1', 'kind': 'cloud'},
    {'id': 'xai', 'name': 'xAI · Grok', 'baseUrl': 'https://api.x.ai/v1', 'kind': 'cloud'},
    {'id': 'mistral', 'name': 'Mistral', 'baseUrl': 'https://api.mistral.ai/v1', 'kind': 'cloud'},
    {'id': 'groq', 'name': 'Groq · 开源模型', 'baseUrl': 'https://api.groq.com/openai/v1', 'kind': 'gateway'},
    {'id': 'siliconflow', 'name': '硅基流动 · 多模型平台', 'baseUrl': 'https://api.siliconflow.cn/v1', 'kind': 'gateway'},
    {'id': 'openrouter', 'name': 'OpenRouter · 多模型平台', 'baseUrl': 'https://openrouter.ai/api/v1', 'kind': 'gateway'},
    {'id': 'ollama', 'name': 'Ollama · 本地模型', 'baseUrl': 'http://127.0.0.1:11434/v1', 'kind': 'local'},
    {'id': 'lmstudio', 'name': 'LM Studio · 本地模型', 'baseUrl': 'http://127.0.0.1:1234/v1', 'kind': 'local'},
    {'id': 'custom', 'name': '自定义 · 任意兼容服务', 'baseUrl': '', 'kind': 'custom'},
]
ASSIGNMENTS = ('task1', 'task2', 'audit', 'rescore', 'coaching')


def normalized_base(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError('请填写模型服务的 Base URL。')
    value = value.strip().rstrip('/')
    if value.endswith('/chat/completions'): value = value[:-17]
    parsed = urlsplit(value)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme not in {'http', 'https'}
            or (parsed.scheme == 'http' and parsed.hostname not in {'127.0.0.1', 'localhost', '::1'})
            or any(ord(c) < 32 for c in value)):
        raise ValueError('请使用 HTTPS 地址；本地服务可使用 localhost 或 127.0.0.1。')
    return value


class ModelSettingsSnapshot:
    def __init__(self, data, fallback):
        self.data, self.fallback = deepcopy(data), fallback

    def config_for_stage(self, task, stage):
        selection = stage if stage in {'audit', 'rescore', 'coaching'} else task
        profile_id = self.data['assignments'].get(selection) or self.data['assignments'][task]
        profile = next((p for p in self.data['profiles'] if p['id'] == profile_id), None)
        if profile is None: return self.fallback.config_for_route(task, 'primary')
        capabilities = TEXT_ROUTE_CAPABILITIES
        if profile['vision']: capabilities = capabilities | frozenset({Capability.IMAGE_INPUT})
        base = profile['baseUrl']
        key = profile['apiKey'] or ('local' if urlsplit(base).hostname in {'127.0.0.1','localhost','::1'} else '')
        # Pass the exact compatible endpoint; arbitrary path prefixes must not gain an extra /v1.
        return ModelConfig(profile['model'], key, base + '/chat/completions', capabilities)

    def config_for_route(self, task, route):
        return self.config_for_stage(task, 'coaching' if route == 'syntax_enhancement' else task)


class ModelSettingsStore:
    def __init__(self, path, fallback, *, get=requests.get, transport=None):
        self.path, self.fallback = Path(path), fallback
        self.lock = threading.RLock()
        self.get = get
        self.transport = transport or OpenAICompatibleTransport()
        self.data = self._load()

    def _load(self):
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or data.get('version') != 1:
                raise ValueError('Model settings require review.')
            return data
        profiles=[]
        for task in ('task1','task2'):
            config=self.fallback.config_for_route(task,'primary')
            profiles.append({'id': task+'-existing', 'name': '当前小作文模型' if task=='task1' else '当前大作文模型',
                'provider': 'custom', 'model': config.model, 'baseUrl': normalized_base(config.base_url),
                'apiKey': config.api_key, 'vision': Capability.IMAGE_INPUT in config.declared_capabilities})
        return {'version':1,'revision':0,'profiles':profiles,
            'assignments':{'task1':'task1-existing','task2':'task2-existing','audit':None,'rescore':None,'coaching':None}}

    def snapshot(self):
        with self.lock: return ModelSettingsSnapshot(self.data, self.fallback)

    def config_for_stage(self, task, stage):
        return self.snapshot().config_for_stage(task, stage)

    def config_for_route(self, task, route):
        return self.snapshot().config_for_route(task, route)

    def projection(self):
        with self.lock:
            return {'revision':self.data['revision'], 'providers':deepcopy(PROVIDERS),
                'profiles':[{k:v for k,v in p.items() if k!='apiKey'} | {'hasApiKey':bool(p['apiKey'])}
                            for p in self.data['profiles']], 'assignments':dict(self.data['assignments'])}

    def _profile(self, value, *, allow_empty_model=False):
        if not isinstance(value,dict): raise ValueError('模型配置无效。')
        allowed={'id','name','provider','model','baseUrl','apiKey','hasApiKey','vision'}
        if set(value)-allowed: raise ValueError('模型配置包含未知字段。')
        identifier=value.get('id')
        if not isinstance(identifier,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',identifier):
            raise ValueError('模型配置标识无效。')
        old=next((p for p in self.data['profiles'] if p['id']==identifier),None)
        base=normalized_base(value.get('baseUrl'))
        key=value.get('apiKey')
        # A saved credential is never forwarded to a changed destination implicitly.
        if key is None: key=old['apiKey'] if old and old['baseUrl']==base else ''
        if not isinstance(key,str) or len(key)>4096 or any(ord(c)<32 for c in key):
            raise ValueError('API Key 格式无效。')
        model=value.get('model','').strip() if isinstance(value.get('model',''),str) else ''
        name=value.get('name','')
        provider=value.get('provider','custom')
        if (not isinstance(name,str) or not 1<=len(name.strip())<=100
                or provider not in {p['id'] for p in PROVIDERS} or not isinstance(value.get('vision'),bool)
                or (not model and not allow_empty_model) or len(model)>200):
            raise ValueError('请填写配置名称、模型 ID，并选择图像能力。')
        return {'id':identifier,'name':name.strip(),'provider':provider,'model':model,
            'baseUrl':base,'apiKey':key.strip(),'vision':value['vision']}

    def save(self, body):
        with self.lock:
            if body.get('revision')!=self.data['revision']:
                raise PlatformError(ErrorCode.CONFLICT,'模型设置已变化，请刷新后重试。')
            profiles=body.get('profiles'); assignments=body.get('assignments')
            if not isinstance(profiles,list) or not 1<=len(profiles)<=50 or not isinstance(assignments,dict):
                raise ValueError('请至少保留一个模型配置。')
            parsed=[self._profile(p) for p in profiles]
            ids={p['id'] for p in parsed}
            if len(ids)!=len(parsed) or set(assignments)!=set(ASSIGNMENTS): raise ValueError('模型用途配置无效。')
            for role,identifier in assignments.items():
                if (role in {'task1','task2'} and identifier not in ids) or (identifier is not None and identifier not in ids):
                    raise ValueError('请为写作任务选择已保存的模型。')
            selected=next(p for p in parsed if p['id']==assignments['task1'])
            if not selected['vision']: raise ValueError('小作文模型需要支持图像输入。')
            value={'version':1,'revision':self.data['revision']+1,'profiles':parsed,'assignments':assignments}
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.path.with_suffix('.new')
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            try:
                with os.fdopen(fd,'w') as file:
                    json.dump(value,file,ensure_ascii=False);file.flush();os.fsync(file.fileno())
                os.replace(temporary,self.path)
                os.chmod(self.path,0o600)
            finally:
                if temporary.exists(): temporary.unlink()
            self.data=value
            return self.projection()

    def discover(self, value):
        with self.lock: profile=self._profile(value,allow_empty_model=True)
        headers={'Authorization':'Bearer '+(profile['apiKey'] or 'local')}
        if urlsplit(profile['baseUrl']).hostname=='api.anthropic.com':
            headers={'x-api-key':profile['apiKey'],'anthropic-version':'2023-06-01'}
        try:
            response=self.get(profile['baseUrl']+'/models',headers=headers,timeout=(5,15),allow_redirects=False)
            if response.status_code!=200: raise ValueError()
            data=response.json(); items=data.get('data',[]) if isinstance(data,dict) else []
            models=sorted({item['id'] for item in items if isinstance(item,dict)
                and isinstance(item.get('id'),str) and len(item['id'])<=200})
            return {'models':models[:3000], 'status':'AVAILABLE' if models else 'EMPTY'}
        except (requests.RequestException,ValueError,TypeError):
            return {'models':[],'status':'UNAVAILABLE','message':'未能读取模型列表，可直接填写服务商提供的模型 ID。'}

    def test(self, value):
        with self.lock: profile=self._profile(value)
        snapshot=ModelSettingsSnapshot({'profiles':[profile], 'assignments':{'task1':profile['id'],'task2':profile['id']}},self.fallback)
        config=snapshot.config_for_stage('task2','task2')
        route=resolve_route(route_for('task2','criterion_scoring'),provider_id='openai-compatible',
            model_id=config.model,api_key=config.api_key,base_url=config.base_url,
            declared_capabilities=config.declared_capabilities)
        if route.contract is None: return {'ok':False,'message':'请检查模型 ID、API Key 和接口地址。'}
        result=self.transport.call(route.contract,ProviderCallRequest(
            [{'role':'user','content':'Return exactly this JSON object: {"connected":true}'}],
            '模型连接测试',require_json_object=True,stream=False,timeout=(5,15),max_tokens=128))
        success=False
        if result.ok:
            try: success=json.loads(result.content)=={'connected':True}
            except ValueError: pass
        return {'ok':success,'message':'连接成功，JSON 输出有效。' if success else
            '连接或 JSON 输出检查未通过，请核对模型名称、余额与接口兼容性。'}
