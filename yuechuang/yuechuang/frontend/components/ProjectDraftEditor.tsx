'use client';
import Link from 'next/link';
import {useEffect, useRef, useState} from 'react';
import {FileText, Loader2, Save, Sparkles} from 'lucide-react';
import {STYLES, VIDEO_RATIOS} from '@/config/models';
import {fetchApiModels, updateProjectDraft, type ApiModelOption} from '@/lib/workflowApi';
import {errorMessage, type ProjectMeta} from '@/lib/workflowTypes';
import {useUnsavedChanges} from '@/lib/unsavedChanges';
import {useEditorRecovery} from '@/lib/editorRecovery';
import {modelDisplayName} from '@/lib/modelLabel';
import {toast} from '@/lib/uiFeedback';

export default function ProjectDraftEditor({sessionId, meta, busy, onSaved, onGenerate}: {
  sessionId: string; meta: ProjectMeta; busy: boolean; onSaved: (meta: ProjectMeta) => void; onGenerate: () => Promise<void>;
}) {
  const initial = {title: meta.title || '未命名项目', idea: meta.user_textbox_input ?? meta.idea ?? '',
    style: meta.style || 'realistic', video_ratio: meta.video_ratio || '9:16',
    episodes: meta.episodes || 1, target_duration: meta.target_duration || 60, llm_model: meta.llm_model || ''};
  const [draft, setDraft] = useState(initial);
  const [saved, setSaved] = useState(JSON.stringify(initial));
  const [revision, setRevision] = useState(meta.project_revision || 0);
  const [models, setModels] = useState<ApiModelOption[]>([]);
  const [modelError, setModelError] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const lock = useRef(false);
  const dirty = JSON.stringify(draft) !== saved;
  useUnsavedChanges(dirty);
  const {recovery, clearRecovery} = useEditorRecovery(`yc-draft-${sessionId}`, JSON.stringify(draft), dirty);
  useEffect(() => {
    let active = true;
    fetchApiModels({modelType:'llm'}).then(items => {if (active) setModels(items);}).catch(() => {if (active) setModelError('文本模型列表加载失败，可在设置中检查后刷新。');});
    return () => {active = false;};
  }, []);
  const save = async () => {
    const result = await updateProjectDraft(sessionId, draft, revision);
    setRevision(result.meta.project_revision || 0); setSaved(JSON.stringify(draft)); onSaved(result.meta);
    clearRecovery();
    setMessage('草稿已保存');toast.success('草稿已保存');
  };
  const submit = async (generate: boolean) => {
    if (busy || lock.current) return;
    setError(''); setMessage('');
    if (!draft.title.trim()) {setError('请填写项目名称'); return;}
    if (generate && !draft.idea.trim() && !meta.idea?.trim()) {setError('请先写下一句话创意，或从首页导入文档'); return;}
    if (generate && !draft.llm_model) {setError('请先选择文本模型。草稿仍可保存。'); return;}
    if (generate && !window.confirm(`将使用 ${modelDisplayName(draft.llm_model)} 生成 ${draft.episodes} 集、每集目标 ${draft.target_duration} 秒的剧本。编写、评审和整理可能产生多次文本请求，按服务商实际用量计费。完成后等待你确认；本次不生成图片或视频。开始吗？`)) return;
    lock.current = true; setSaving(true);
    try {if (dirty) await save(); if (generate) await onGenerate();}
    catch (e) {const message=errorMessage(e, '操作失败，当前输入已保留');setError(message);toast.error(message);}
    finally {lock.current = false; setSaving(false);}
  };
  const field = <K extends keyof typeof draft>(key: K, value: typeof draft[K]) => {setDraft(prev => ({...prev, [key]: value})); setMessage('');};
  return <div className="yc-draft-scroll"><section className="yc-project-draft">
    <div className="yc-draft-intro"><span className="yc-draft-icon"><FileText size={24}/></span><div><p className="yc-eyebrow">第一步 · 剧本</p><h1>你的故事，从这里开始</h1><p>写下人物、处境或一个意想不到的转折。</p></div></div>
    <form onSubmit={e => {e.preventDefault(); void submit(false);}}>
      {recovery && !dirty && <div className="yc-recovery-notice">本标签页有未保存的输入。
        <button type="button" onClick={() => {try {setDraft(JSON.parse(recovery)); setMessage('已恢复到表单，请检查后保存');} catch {setError('本地草稿无法读取');}}}>恢复输入</button>
        <button type="button" onClick={clearRecovery}>放弃备份</button>
      </div>}
      <fieldset disabled={busy || saving}>
        <label>项目名称<input aria-label="项目名称" value={draft.title} maxLength={120} onChange={e => field('title', e.target.value)}/></label>
        <label>一句话创意<textarea aria-label="项目创作想法" value={draft.idea} maxLength={20000} onChange={e => field('idea', e.target.value)} rows={5} placeholder="例如：失业的修表匠收到一只会倒走的怀表，却只能挽回陌生人的一分钟。"/></label>
        {meta.idea !== meta.user_textbox_input && meta.user_textbox_input !== undefined && <p className="yc-draft-hint">已保留从首页导入的文档内容。</p>}
        <div className="yc-draft-grid">
          <label>画面风格<select aria-label="画面风格" value={draft.style} onChange={e => field('style', e.target.value)}>{STYLES.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</select></label>
          <label>画幅<select aria-label="画幅" value={draft.video_ratio} onChange={e => field('video_ratio', e.target.value)}>{VIDEO_RATIOS.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}</select></label>
          <label>集数<input aria-label="集数" type="number" min={1} max={24} value={draft.episodes} onChange={e => field('episodes', Number(e.target.value))}/></label>
          <label>每集目标时长（秒）<input aria-label="每集目标时长" type="number" min={15} max={600} value={draft.target_duration} onChange={e => field('target_duration', Number(e.target.value))}/></label>
        </div>
        <label>文本模型<select aria-label="文本模型" value={draft.llm_model} onChange={e => field('llm_model', e.target.value)}><option value="">生成前选择文本模型</option>{draft.llm_model && !models.some(m => m.id === draft.llm_model) && <option value={draft.llm_model}>{modelDisplayName(draft.llm_model)}</option>}{models.map(m => <option key={m.id} value={m.id}>{modelDisplayName(m.id,m.label)}</option>)}</select></label>
      </fieldset>
      <p className="yc-draft-hint">{modelError || '保存草稿免费。生成前请确认文本服务已配置，后续素材和视频逐步确认。'} <Link href="/settings">查看服务设置 →</Link></p>
      {error && <p role="alert" className="yc-project-error">{error}</p>}{message && <p role="status" className="yc-save-message">{message}</p>}
      <div className="yc-draft-actions"><span>{dirty ? '有未保存修改' : '草稿已保存'}</span><button type="submit" className="yc-secondary-button" disabled={busy || saving || !dirty} title={!dirty?'没有未保存的修改':busy||saving?'请等待当前操作完成':undefined}><Save size={16}/>保存草稿</button><button type="button" className="yc-primary-button" disabled={busy || saving} onClick={() => void submit(true)}>{busy || saving ? <Loader2 size={16} className="animate-spin"/> : <Sparkles size={16}/>}生成剧本</button></div>
    </form>
  </section></div>;
}
