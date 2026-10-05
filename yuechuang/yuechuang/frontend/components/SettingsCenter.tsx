'use client';

import {useCallback, useEffect, useRef, useState} from 'react';
import {Bot, Check, Database, Loader2, Pencil, Plus, RefreshCw, Settings2, ShieldCheck, SlidersHorizontal, Star, X} from 'lucide-react';
import {useStudioConfirm} from './StudioConfirm';
import ModelSelect from '@/components/ui/ModelSelect';
import {modelDisplayName} from '@/lib/modelLabel';
import {compatibleVideoFormat} from '@/lib/modelSelection';
import {toast} from '@/lib/uiFeedback';
import {DefaultsPanel, StoragePanel, AdvancedPanel} from './SettingsPanels';
import {canLeaveEditor, useUnsavedChanges} from '@/lib/unsavedChanges';
import {errorMessage} from '@/lib/workflowTypes';
import {settingsEqual, settingsRequest, connectionDraft, serviceModelId, defaultOptions, KIND_LABELS, SERVICE_STATUS, MODEL_LABELS, type ModelField, type SettingsDocument, type ServiceKind, type ServiceConnection, type ConnectionDraft} from '@/lib/settingsApi';

type Section = 'services' | 'general' | 'storage' | 'advanced';
const NAV = [
  {id: 'services', label: 'AI 服务', icon: Bot},
  {id: 'general', label: '通用', icon: SlidersHorizontal},
  {id: 'storage', label: '存储与任务', icon: Database},
  {id: 'advanced', label: '高级设置', icon: Settings2},
] as const;
const DESCRIPTIONS = {text: '剧本改写、角色场景提取与分镜拆解', image: '角色图、场景图与镜头图等静态图像生成', video: '按已确认的分镜生成视频片段'};
const KINDS: ServiceKind[] = ['text', 'image', 'video'];
const modelName = modelDisplayName;

export function SettingsErrorNotice({message}: {message: string}) {
  return message ? <p role="alert" className="yc-settings-error">{message}</p> : null;
}

export default function SettingsCenter() {
  const [doc, setDoc] = useState<SettingsDocument | null>(null);
  const [section, setSection] = useState<Section>('services');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState('');
  const [editing, setEditing] = useState<{kind: ServiceKind; connection?: ServiceConnection} | null>(null);
  const lock = useRef(false);
  const {confirm, confirmation} = useStudioConfirm();
  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {setDoc(await settingsRequest<SettingsDocument>(''));}
    catch (e) {setError(errorMessage(e, '设置读取失败'));}
    finally {setLoading(false);}
  }, []);
  useEffect(() => {
    let active = true;
    settingsRequest<SettingsDocument>('').then(value => {if (active) setDoc(value);})
      .catch(e => {if (active) setError(errorMessage(e, '设置读取失败'));})
      .finally(() => {if (active) setLoading(false);});
    return () => {active = false;};
  }, []);

  async function mutate(key: string, path: string, body: unknown, method = 'POST', success = '设置已保存') {
    if (lock.current) return;
    lock.current = true; setBusy(key); setError(''); setNotice('');
    try {
      const result = await settingsRequest<SettingsDocument>(path, method, body);
      setDoc(current => current ? {...current, ...result} : result);
      if (key.endsWith(':test')) {
        const tested=result.connections?.find(connection=>`${connection.id}:test`===key);
        const passed=tested?.test.status==='available';
        const testMessage=tested?.test.message||success;
        setNotice(testMessage);
        if(passed)toast.success(testMessage);else if(tested?.test.status==='error'||tested?.test.status==='missing')toast.error(testMessage);else toast.info(testMessage);
      } else {setNotice(success);toast.success(success);}
    } catch (e) {
      const message=errorMessage(e, '保存失败，请重试');
      if (key !== 'connection') setError(message);
      toast.error(message);
      throw e;
    } finally {lock.current = false; setBusy('');}
  }
  async function toggle(conn: ServiceConnection) {
    if (!doc) return;
    if (conn.enabled && !await confirm('停用后，新任务不能使用这条连接；已提交的任务继续使用提交时的配置。是否停用？')) return;
    void mutate(`${conn.id}:toggle`, `/connections/${conn.id}`, {revision: doc.revision, ...connectionDraft(conn), enabled: !conn.enabled}, 'PUT', conn.enabled ? '服务已停用' : '服务已启用').catch(() => {});
  }
  async function updateDefaultModel(field: ModelField, value: string) {
    if (!doc || value === doc.defaults.models[field]) return;
    const connection = doc.connections.find(c => c.models.some(model => serviceModelId(c.id, model) === value));
    const model = connection?.models.find(model => serviceModelId(connection.id, model) === value);
    const success = '新项目默认模型已更新；已有项目保持原配置';
    if (connection && model) {
      await mutate(`${connection.id}:default`, `/connections/${connection.id}/default`, {revision: doc.revision, model}, 'POST', success);
      return;
    }
    const capabilities = doc.catalog.find(model => model.id === value)?.capabilities;
    const defaults = {...doc.defaults,
      ...(field.startsWith('video_') ? compatibleVideoFormat(doc.defaults, capabilities) : {}),
      models: {...doc.defaults.models, [field]: value}};
    await mutate('defaults', '/defaults', {revision: doc.revision, defaults}, 'PUT', success);
  }
  const title = {services: 'AI 服务配置', general: '通用设置', storage: '存储与任务', advanced: '高级设置'}[section];
  const description = {services: '管理文本、图片、视频模型服务与默认配置。', general: '设置新项目的创作偏好和默认模型。', storage: '查看本机文件位置、磁盘空间与最近任务。', advanced: '管理本机服务、日志与代理。'}[section];

  return <div className="yc-settings-page hb-settings">
    <div className="yc-settings-layout">
      <aside className="yc-settings-nav" aria-label="设置分类">
        <div className="hb-settings-nav-group"><p>基础</p>{NAV.map(item => {
          const Icon = item.icon;
          return <button key={item.id} aria-current={section === item.id ? 'page' : undefined} onClick={() => {
            if (item.id === section || !canLeaveEditor()) return;
            setSection(item.id); setNotice('');
          }}><Icon size={14}/>{item.label}</button>;
        })}</div>
      </aside>
      <main className="yc-settings-content"><div className="yc-settings-scroll">
        <header className="yc-settings-heading"><div><h1>{title}</h1><p>{description}</p></div><button className="yc-settings-icon hb-settings-refresh" aria-label="重新加载设置" disabled={loading || !!busy} onClick={() => {if (canLeaveEditor()) void load();}}><RefreshCw size={14} className={loading ? 'animate-spin' : ''}/></button></header>
        <SettingsErrorNotice message={error}/>
        {notice && <div className="yc-settings-notice" role="status"><Check size={14}/>{notice}</div>}
        {loading ? <div className="yc-settings-loading" role="status"><Loader2 size={18} className="animate-spin"/>正在读取设置…</div> : !doc ? <div className="yc-settings-card"><p>暂时无法读取设置。</p><button className="yc-primary-button" onClick={() => void load()}>重新加载</button></div> : <>
          {section === 'services' && <>
            <section className="yc-settings-card hb-settings-summary">
              <header><h2>当前默认模型</h2><span className="yc-settings-badge">新项目使用</span></header>
              <p>各用途使用独立的服务连接，已有项目保留自己的模型配置。</p>
              <div className="experience-settings-defaults">{(['llm', 'image_t2i', ({text: 'video_text', first_frame: 'video_first_frame', start_end_frame: 'video_start_end', reference: 'video_reference'} as const)[doc.defaults.video_generation_mode]] as ModelField[]).map(field => {
                const id = doc.defaults.models[field];
                const conn = doc.connections.find(c => c.models.some(m => serviceModelId(c.id, m) === id));
                const kind = field === 'llm' ? 'text' : field === 'image_t2i' ? 'image' : 'video';
                const configured=!!conn&&Object.values(conn.credentials).length>0&&Object.values(conn.credentials).every(Boolean);
                const options=defaultOptions(doc,field);
                return <article key={field}>
                  <header><span>{KIND_LABELS[kind]}</span><span className="yc-settings-badge">新项目默认</span></header>
                  <label className="experience-default-model"><span>默认模型</span><ModelSelect label={`${KIND_LABELS[kind]}默认模型`} group={KIND_LABELS[kind]} value={id} options={options} disabled={!!busy} onChange={value=>void updateDefaultModel(field,value).catch(()=>{})}/></label>
                  <p>{field==='llm'?'编写与分析剧本':field==='image_t2i'?'生成角色、场景和镜头图':MODEL_LABELS[field]}</p>
                  <small>{conn?doc.providers.find(p=>p.id===conn.provider)?.label||conn.provider:id?'原供应商配置':'尚未配置默认模型'}</small>
                  <footer>{conn?<><button type="button" disabled={!!busy||!configured||!conn.enabled} title={!configured?'请先配置凭据':!conn.enabled?'请先启用连接':'只读取模型清单，不产生生成费用'} onClick={()=>void mutate(`${conn.id}:test`, `/connections/${conn.id}/test`, {revision:doc.revision}, 'POST', '只读连接测试已结束').catch(()=>{})}>{busy===`${conn.id}:test`?'测试中…':'测试连接'}</button><button type="button" disabled={!!busy} onClick={()=>setEditing({kind,connection:conn})}><Pencil size={12}/>编辑服务</button></>:<button type="button" disabled={!!busy} onClick={()=>setEditing({kind})}><Plus size={12}/>添加服务</button>}</footer>
                </article>;
              })}</div>
            </section>
            <section className="yc-settings-card yc-settings-templates"><header><div><h2>手动模板</h2><p>选择服务类型后，填写供应商、Base URL 和已接入的模型。</p></div></header><div className="yc-settings-template-actions">{KINDS.map(kind => <button key={kind} disabled={!!busy} onClick={() => setEditing({kind})}>{KIND_LABELS[kind]}</button>)}</div></section>
            <div className="hb-settings-sections">{KINDS.map(kind => <section className="yc-settings-service-group" key={kind}>
              <header><div><h2>{KIND_LABELS[kind]}</h2><p>{DESCRIPTIONS[kind]}</p></div>{doc.connections.some(c => c.kind === kind && c.enabled) && <span className="yc-settings-badge">{doc.connections.filter(c => c.kind === kind && c.enabled).length} 已启用</span>}<button aria-label={`新增${KIND_LABELS[kind]}服务`} disabled={!!busy} onClick={() => setEditing({kind})}><Plus size={13}/>新增</button></header>
              {!doc.connections.some(c => c.kind === kind) ? <div className="yc-settings-empty">暂无配置</div> : doc.connections.filter(c => c.kind === kind).map(conn => {
                const configured = Object.values(conn.credentials).length > 0 && Object.values(conn.credentials).every(Boolean);
                const status = configured ? conn.test.status : 'missing';
                return <article className={`yc-settings-connection ${!conn.enabled ? 'is-disabled' : ''}`} data-test-status={status} key={conn.id}>
                  <span className="hb-settings-provider-icon" data-provider={conn.provider} aria-hidden="true">{conn.provider.slice(0, 1).toUpperCase()}</span>
                  <div className="yc-settings-connection-main">
                    <div className="yc-settings-service-title"><h3>{conn.name}</h3><span className={`yc-settings-badge state-${configured ? 'available' : 'missing'}`}>{configured ? '已配置凭据' : '未配置凭据'}</span>{!conn.enabled && <span className="yc-settings-badge">已停用</span>}</div>
                    <div className="yc-settings-model-tags">{conn.models.map(model => {
                      const defaultFields = Object.entries(doc.defaults.models).filter(([, value]) => value === serviceModelId(conn.id, model)).map(([field]) => MODEL_LABELS[field as ModelField]);
                      const isDefault = defaultFields.length > 0;
                      return <button key={model} aria-label={`设为默认 ${conn.name} ${modelName(model)}`} aria-pressed={isDefault} title={isDefault ? `已用于新项目：${defaultFields.join('、')}` : '设为对应能力的默认模型；已有项目保持原配置'} disabled={!!busy || !conn.enabled} className={isDefault ? 'is-default' : ''} onClick={() => void mutate(`${conn.id}:default`, `/connections/${conn.id}/default`, {revision: doc.revision, model}, 'POST', '新项目默认模型已更新；已有项目保持原配置').catch(() => {})}>{isDefault && <Star size={9}/>}<span>{modelName(model)}</span></button>;
                    })}</div>
                    <p className="yc-settings-endpoint" title={conn.base_url}>{conn.base_url}</p>
                    {conn.test.status !== 'untested' && <small className={`yc-settings-test-message state-${status}`}>{SERVICE_STATUS[status] || '能力待验'} · {conn.test.message}{conn.test.tested_at ? ` · ${new Date(conn.test.tested_at * 1000).toLocaleString('zh-CN')}` : ''}</small>}
                  </div>
                  <div className="yc-settings-service-actions"><button className="hb-settings-test" aria-label={`测试连接 ${conn.name}`} disabled={!!busy || !configured || !conn.enabled} onClick={() => void mutate(`${conn.id}:test`, `/connections/${conn.id}/test`, {revision: doc.revision}, 'POST', '只读连接测试已结束；生成效果仍需单独验证').catch(() => {})}>{busy === `${conn.id}:test` && <Loader2 size={12} className="animate-spin"/>}{busy === `${conn.id}:test` ? '测试中' : '测试连接'}</button><label className="yc-settings-toggle"><input aria-label={`启用 ${conn.name}`} type="checkbox" checked={conn.enabled} disabled={!!busy} onChange={() => void toggle(conn)}/><span/></label><button className="yc-settings-icon" aria-label={`编辑 ${conn.name}`} disabled={!!busy} onClick={() => setEditing({kind, connection: conn})}><Pencil size={13}/></button></div>
                </article>;
              })}
            </section>)}</div>
            <div className="yc-settings-footnote"><ShieldCheck size={14}/><p>凭据仅保存在后端。测试连接只读取模型清单，不生成内容；连接通过不代表生成效果通过。</p></div>
          </>}
          {section === 'general' && <DefaultsPanel doc={doc} save={values => mutate('defaults', '/defaults', {revision: doc.revision, defaults: values}, 'PUT', '创作默认值已保存，新建项目将继承这些设置')} busy={!!busy}/>}
          {section === 'storage' && <StoragePanel/>}
          {section === 'advanced' && <AdvancedPanel doc={doc} busy={!!busy} save={values => mutate('advanced', '/advanced', {revision: doc.revision, ...values}, 'PUT', '高级设置已保存；监听地址与端口需重启后端生效')}/>}
        </>}
      </div></main>
    </div>
    {editing && doc && <ConnectionEditor doc={doc} kind={editing.kind} connection={editing.connection} close={() => setEditing(null)} save={async values => {
      await mutate('connection', editing.connection ? `/connections/${editing.connection.id}` : '/connections', {revision: doc.revision, ...values}, editing.connection ? 'PUT' : 'POST', '服务配置已保存');
      setEditing(null);
    }}/>}
    {confirmation}
  </div>;
}

function ConnectionEditor({doc, kind, connection, close, save}: {doc: SettingsDocument; kind: ServiceKind; connection?: ServiceConnection; close: () => void; save: (values: ConnectionDraft) => Promise<void>}) {
  const kinds = {text: ['llm', 'vlm'], image: ['t2i', 'i2i'], video: ['video']};
  const providers = doc.providers.filter(p => doc.catalog.some(m => m.provider === p.id && m.type.some(t => kinds[kind].includes(t))));
  const initial: ConnectionDraft = connection ? connectionDraft(connection) : {name: '', kind, provider: providers[0]?.id || 'openai', base_url: providers[0]?.base_url || '', models: [], enabled: true, enable_proxy: false, credentials: {}, clear_credentials: []};
  const [values, setValues] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [modelQuery, setModelQuery] = useState('');
  const ref = useRef<HTMLDialogElement>(null);
  const lock = useRef(false);
  const {confirm, confirmation} = useStudioConfirm();
  const dirty = !settingsEqual(values, initial);
  useUnsavedChanges(dirty);
  useEffect(() => {ref.current?.showModal();}, []);
  const fields = values.provider === 'kling' ? ['access_key', 'secret_key'] : ['api_key'];
  const candidates = doc.catalog.filter(m => m.provider === values.provider && m.type.some(t => kinds[kind].includes(t)));
  const visible = candidates.filter(m => `${m.name} ${m.id}`.toLowerCase().includes(modelQuery.trim().toLowerCase()));
  function selectProvider(provider: string) {
    if (connection) return;
    setValues({...values, provider, base_url: providers.find(p => p.id === provider)?.base_url || '', models: [], credentials: {}, clear_credentials: []});
    setModelQuery('');
  }
  async function dismiss() {
    if (busy) return;
    if (dirty && !await confirm('当前服务修改尚未保存，是否放弃修改？')) return;
    close();
  }
  async function submit() {
    if (lock.current) return;
    if (values.clear_credentials.length && !await confirm('清空后，这条服务的新任务将无法使用所清空的凭据。是否继续保存？')) return;
    lock.current = true; setBusy(true); setError('');
    try {await save(values);}
    catch (e) {setError(errorMessage(e, '保存失败，请重试'));}
    finally {lock.current = false; setBusy(false);}
  }
  return <><dialog ref={ref} className="yc-project-dialog yc-settings-dialog hb-settings-dialog" aria-labelledby="connection-heading" onCancel={e => {e.preventDefault(); void dismiss();}} onClick={e => {if (e.target === e.currentTarget) {const box = e.currentTarget.getBoundingClientRect(); if (e.clientX < box.left || e.clientX > box.right || e.clientY < box.top || e.clientY > box.bottom) void dismiss();}}}>
    <form onSubmit={e => {e.preventDefault(); void submit();}}>
      <header><div><h2 id="connection-heading">{connection ? '编辑 AI 服务' : `添加${KIND_LABELS[kind]}服务`}</h2><p>填写服务连接，配置该用途的可用模型。</p></div><span className="yc-settings-badge">{KIND_LABELS[kind]}</span><button type="button" aria-label="关闭服务编辑" disabled={busy} onClick={() => void dismiss()}><X size={16}/></button></header>
      <div className="hb-settings-dialog-body"><fieldset disabled={busy}>
        {!connection && <div className="hb-settings-presets" aria-label="供应商模板">{providers.map(p => <button key={p.id} type="button" aria-pressed={values.provider === p.id} onClick={() => selectProvider(p.id)}>{p.label}</button>)}</div>}
        <label>服务名称<input autoFocus required maxLength={100} value={values.name} onChange={e => setValues({...values, name: e.target.value})} placeholder="例如：ToAPIs · 主账号"/></label>
        <label>供应商<select value={values.provider} disabled={!!connection} onChange={e => selectProvider(e.target.value)}>{!providers.some(p => p.id === values.provider) && <option value={values.provider}>{values.provider} · 当前配置</option>}{providers.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
        {fields.map(field => <div className="yc-settings-secret" key={field}><label>{field === 'api_key' ? 'API Key' : field === 'access_key' ? 'Access Key' : 'Secret Key'}<input type="password" autoComplete="new-password" value={values.credentials[field] || ''} placeholder={connection?.credentials[field] ? '已配置，留空保留；输入新凭据以替换' : '输入凭据，仅保存到后端'} disabled={values.clear_credentials.includes(field)} onChange={e => setValues({...values, credentials: {...values.credentials, [field]: e.target.value}})}/></label>{connection?.credentials[field] && <label className="yc-settings-check"><input type="checkbox" checked={values.clear_credentials.includes(field)} onChange={e => setValues({...values, credentials: {...values.credentials, [field]: ''}, clear_credentials: e.target.checked ? [...values.clear_credentials, field] : values.clear_credentials.filter(v => v !== field)})}/>清空已保存凭据</label>}</div>)}
        <label>Base URL<input required type="url" maxLength={500} value={values.base_url} onChange={e => setValues({...values, base_url: e.target.value})} placeholder="https://…"/><small>使用当前供应商适配协议，地址须与供应商一致。</small></label>
        <section className="hb-settings-models"><h3>模型</h3>{values.models.length > 0 && <div className="hb-settings-selected-models">{values.models.map(model => <span key={model}>{modelName(model)}<button type="button" aria-label={`移除模型 ${modelName(model)}`} onClick={() => setValues({...values, models: values.models.filter(m => m !== model)})}><X size={11}/></button></span>)}</div>}
          <input aria-label="搜索可用模型" value={modelQuery} onChange={e => setModelQuery(e.target.value)} placeholder="搜索已接入的模型"/>
          <div className="yc-settings-model-picker">{visible.map(model => <label key={model.id}><input type="checkbox" checked={values.models.includes(model.id)} onChange={e => setValues({...values, models: e.target.checked ? [...values.models, model.id] : values.models.filter(m => m !== model.id)})}/><span>{modelName(model.id,model.name)}<small>{model.type.map(type=>({llm:'文本',vlm:'图像理解',t2i:'文生图',i2i:'图生图',video:'视频'} as Record<string,string>)[type]||type).join(' · ')}</small></span><span className="yc-settings-badge">{model.capabilities?.api_contract_verified ? '协议已校验' : '能力待验'}</span></label>)}{!visible.length && <p>没有匹配的已接入模型。</p>}</div><small>可多选。仅显示已接入模型；保存后可在服务卡片中设为默认。</small>
        </section>
        <div className="hb-settings-checks"><label className="yc-settings-check"><input type="checkbox" checked={values.enabled} onChange={e => setValues({...values, enabled: e.target.checked})}/>启用此服务</label><label className="yc-settings-check"><input type="checkbox" checked={values.enable_proxy} onChange={e => setValues({...values, enable_proxy: e.target.checked})}/>使用高级设置中的代理</label></div>
      </fieldset><SettingsErrorNotice message={error}/></div>
      <footer><small>保存不调用模型，凭据留空保留原值。</small><button type="button" className="yc-secondary-button" disabled={busy} onClick={() => void dismiss()}>取消</button><button className="yc-primary-button" disabled={busy || !values.name.trim() || !values.models.length}>{busy && <Loader2 size={13} className="animate-spin"/>}{busy ? '保存中…' : '保存'}</button></footer>
    </form>
  </dialog>{confirmation}</>;
}
