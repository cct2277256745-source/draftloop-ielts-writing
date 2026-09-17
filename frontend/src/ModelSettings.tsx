import { useEffect, useState } from 'react';
import { IconPlus, IconTrash, IconPlugConnected, IconSearch, IconCheck } from '@tabler/icons-react';
import { BrowserApi, type ModelSettings as Settings, type ModelProfile, type ModelRole } from './application/BrowserApi';
import { PrivacySettings } from './PrivacySettings';

const api = new BrowserApi();
const roles: Array<[ModelRole,string,string]> = [
  ['task2','大作文评分','Rubric 初评与作文证据提取'], ['task1','小作文评分','图表识别与四项评分，需要图像能力'],
  ['audit','RAG 独立审核','对照参考证据审核第一次评分'], ['rescore','重新评分','达到分差阈值后重新依据 Rubric 判断'],
  ['coaching','修改建议与报告','生成逐段分析、表达优化和训练建议'],
];

export function WorkspaceSettings({ onDeleted }: { onDeleted: () => void }) {
  const [tab,setTab]=useState<'models'|'data'>('models');
  return <section className="studio-settings"><header className="studio-page-heading"><div><h1>设置</h1><p>选择适合你的模型，管理自己的写作记录。</p></div></header>
    <div className="studio-tabs" role="tablist" aria-label="设置分类" onKeyDown={e=>{if(["ArrowLeft","ArrowRight","Home","End"].includes(e.key)){e.preventDefault();const next=e.key==="Home"?"models":e.key==="End"?"data":tab==="models"?"data":"models";setTab(next);document.getElementById(next+"-settings-tab")?.focus();}}}>
      <button id="models-settings-tab" role="tab" aria-controls="models-settings-panel" tabIndex={tab==='models'?0:-1} aria-selected={tab==='models'} onClick={()=>setTab('models')}>模型配置</button>
      <button id="data-settings-tab" role="tab" aria-controls="data-settings-panel" tabIndex={tab==='data'?0:-1} aria-selected={tab==='data'} onClick={()=>setTab('data')}>个人数据</button>
    </div>
    <div id="models-settings-panel" role="tabpanel" aria-labelledby="models-settings-tab" hidden={tab!=='models'}><ModelSettings/></div>
    <div id="data-settings-panel" role="tabpanel" aria-labelledby="data-settings-tab" hidden={tab!=='data'}>{tab==='data'?<PrivacySettings onDeleted={onDeleted} embedded/>:null}</div>
  </section>;
}

function ModelSettings() {
  const [value,setValue]=useState<Settings|null>(null),[selected,setSelected]=useState('');
  const [busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [dirty,setDirty]=useState(false),[models,setModels]=useState<string[]>([]);
  const load=async()=>{ setError('');try { const next=await api.models();setValue(next);setSelected(next.profiles[0]?.id??'');setDirty(false); } catch(e){setError((e as Error).message);} };
  useEffect(()=>{void load();},[]);
  const profile=value?.profiles.find(p=>p.id===selected);
  const change=(patch:Partial<ModelProfile>)=>{
    if(!value||!profile)return;
    setValue({...value,profiles:value.profiles.map(p=>p.id===selected?{...p,...patch}:p)});setDirty(true);setNotice('');
  };
  const run=async(name:string,fn:()=>Promise<void>)=>{setBusy(name);setError('');setNotice('');try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy('');}};
  if(!value)return <div className="settings-loading">{error?<><p role="alert">{error}</p><button className="secondary-button" onClick={()=>void load()}>重新读取</button></>:<p role="status">正在读取模型配置…</p>}</div>;
  return <div className="model-settings-body">
    <div className="settings-intro"><div><h2>你的模型服务</h2><p>连接云端或本地模型。选择已有配置，或添加新的服务。</p></div>
      <button className="secondary-button" disabled={!!busy} onClick={()=>{
        const id='model-'+crypto.randomUUID();setValue({...value,profiles:[...value.profiles,{id,name:'新模型配置',provider:'openai',model:'',baseUrl:value.providers.find(p=>p.id==='openai')?.baseUrl??'',vision:false}]});setSelected(id);setModels([]);setDirty(true);
      }}><IconPlus size={18} stroke={1.7}/>添加模型</button></div>
    {error?<p className="studio-notice error" role="alert">{error}</p>:null}
    {notice?<p className="studio-notice" role="status"><IconCheck size={18}/>{notice}</p>:null}
    <div className="model-config-grid">
      <div className="model-profile-list" aria-label="已配置模型">{value.profiles.map(p=><button key={p.id} disabled={!!busy} aria-pressed={p.id===selected} onClick={()=>{setSelected(p.id);setModels([]);setNotice('');}}>
        <strong>{p.name}</strong><span>{p.model||'尚未选择模型'}</span><small>{p.vision?'文本与图像':'文本'}</small>
      </button>)}</div>
      {profile?<div className="model-profile-editor">
        <div className="model-editor-heading"><h3>{profile.name}</h3><button className="icon-button" aria-label="移除此模型配置" title="移除此模型配置" disabled={!!busy||value.profiles.length===1} onClick={()=>{
          const profiles=value.profiles.filter(p=>p.id!==selected);setValue({...value,profiles,assignments:Object.fromEntries(Object.entries(value.assignments).map(([k,v])=>[k,v===selected?null:v])) as Settings['assignments']});setSelected(profiles[0].id);setDirty(true);
        }}><IconTrash size={20} stroke={1.6}/></button></div>
        <div className="model-fields"><label>配置名称<input value={profile.name} maxLength={100} disabled={!!busy} onChange={e=>change({name:e.target.value})}/></label>
          <label>服务商<select value={profile.provider} disabled={!!busy} onChange={e=>{const provider=value.providers.find(p=>p.id===e.target.value)!;change({provider:provider.id,baseUrl:provider.baseUrl,apiKey:'',hasApiKey:false});setModels([]);}}>{value.providers.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          <label className="model-field-wide">接口地址 <span>Base URL</span><input lang="en" value={profile.baseUrl} placeholder="https://…/v1" disabled={!!busy} onChange={e=>{change({baseUrl:e.target.value,apiKey:'',hasApiKey:false});setModels([]);}}/></label>
          <label className="model-field-wide">API Key <span>{profile.hasApiKey&&profile.apiKey===undefined?'已保存，留空保留现有密钥':'本地服务可留空'}</span><input type="password" autoComplete="new-password" spellCheck={false} value={profile.apiKey??''} placeholder={profile.hasApiKey?'已配置密钥':'输入服务商提供的密钥'} disabled={!!busy} onChange={e=>change({apiKey:e.target.value})}/></label>
          <label className="model-field-wide">模型 ID<div className="model-id-control"><input lang="en" list="available-model-ids" value={profile.model} placeholder="搜索模型列表，或直接输入模型 ID" disabled={!!busy} onChange={e=>change({model:e.target.value})}/><button className="secondary-button" disabled={!!busy||!profile.baseUrl} onClick={()=>void run('discover',async()=>{const found=await api.discoverModels(profile);setModels(found.models);setNotice(found.models.length?`已读取 ${found.models.length} 个模型，可在输入框中搜索。`:found.message||'列表为空，可手动输入模型 ID。');})}><IconSearch size={18}/>{busy==='discover'?'读取中…':'读取列表'}</button></div><datalist id="available-model-ids">{models.map(model=><option key={model} value={model}/>)}</datalist></label>
        </div>
        <label className="model-vision"><input type="checkbox" checked={profile.vision} disabled={!!busy} onChange={e=>change({vision:e.target.checked})}/><span>此模型支持图像输入<small>用于识别小作文图表，请按服务商能力选择。</small></span></label>
        <button className="secondary-button" disabled={!!busy||!profile.model||!profile.baseUrl} onClick={()=>void run('test',async()=>{const result=await api.testModel(profile);if(result.ok)setNotice(result.message);else setError(result.message);})}><IconPlugConnected size={18}/>{busy==='test'?'正在测试…':'测试连接'}</button>
      </div>:null}
    </div>
    <section className="model-roles"><h2>按用途选择模型</h2><p>评分、独立审核和教学报告可以使用不同模型。</p>
      {roles.map(([key,label,hint])=><label className="model-role-row" key={key}><span><strong>{label}</strong><small>{hint}</small></span><select disabled={!!busy} value={value.assignments[key]??''} onChange={e=>{setValue({...value,assignments:{...value.assignments,[key]:e.target.value||null}});setDirty(true);setNotice('');}}><option value="">{key==='task1'||key==='task2'?'请选择模型':'跟随当前写作任务'}</option>{value.profiles.filter(p=>key!=='task1'||p.vision).map(p=><option key={p.id} value={p.id}>{p.name} · {p.model||'未设置 ID'}</option>)}</select></label>)}
    </section>
    <div className="settings-save"><span>{dirty?'有尚未保存的修改':'配置已与本地工作区同步'}</span><button className="primary-button" disabled={!!busy||!dirty} onClick={()=>void run('save',async()=>{const saved=await api.saveModels(value);setValue(saved);setDirty(false);setNotice('模型配置已保存，下一次批改将使用新配置。');})}>{busy==='save'?'保存中…':'保存模型配置'}</button></div>
  </div>;
}
