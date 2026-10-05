'use client';

import {useEffect, useRef, useState} from 'react';
import {useRouter} from 'next/navigation';
import {ArrowRight, BookOpen, Loader2, X} from 'lucide-react';
import {ipRequest, type IPDetailData, type IPVersion} from '@/lib/ipApi';
import {isValidChapterRange} from '@/lib/chapterSelection';
import {errorMessage} from '@/lib/workflowTypes';
import {settingsRequest} from '@/lib/settingsApi';
import {STYLES, VIDEO_RATIOS} from '@/config/models';
import {toast} from '@/lib/uiFeedback';
import IPChapterRange from './IPChapterRange';

const DIRECTIONS = [
  {label: '人物成长', text: '以主角的成长为主线，保留推动命运转折的关键事件，压缩次要支线。每集围绕一个困境展开，在结尾留下悬念。'},
  {label: '情绪爽点', text: '聚焦主角面对冲突、作出选择并逆转局面的过程，突出原著中已有的高光场景。精简铺垫，保留人物动机与因果。'},
  {label: '悬疑推进', text: '以核心悬念带动叙事，分集释放原著已有的线索与真相。保留关键人物关系，避免提前泄露后续转折。'},
];

export default function IPProjectDialog({data, version, onClose}: {data: IPDetailData; version: IPVersion; onClose: () => void}) {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const lock = useRef(false);
  const attempt = useRef<{signature: string; key: string} | null>(null);
  const [title, setTitle] = useState(`${data.item.title} · 改编`);
  const [first, setFirst] = useState(1);
  const [last, setLast] = useState(Math.min(10, version.chapters.length));
  const [rangePending, setRangePending] = useState(false);
  const [direction, setDirection] = useState('');
  const [style, setStyle] = useState('realistic');
  const [ratio, setRatio] = useState('16:9');
  const [defaultsLoading, setDefaultsLoading] = useState(true);
  const [defaultsNote, setDefaultsNote] = useState('');
  const [episodes, setEpisodes] = useState(1);
  const [duration, setDuration] = useState(60);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const validRange = isValidChapterRange(first, last, version.chapters.length);
  const validTargets = Number.isInteger(episodes) && episodes >= 1 && episodes <= 24 && Number.isInteger(duration) && duration >= 15 && duration <= 600;
  const disabledReason = defaultsLoading ? '正在读取创作默认设置' : rangePending ? '请再选择一个结束章节' : !validRange ? '请选择有效章节范围' : !title.trim() ? '请填写项目名称' : !direction.trim() ? '请填写改编方向，也可使用下方示例' : !validTargets ? '集数须为 1–24，单集时长须为 15–600 秒的整数' : '';
  useEffect(() => {const previous = document.activeElement; dialog.current?.showModal(); return () => {if (previous instanceof HTMLElement && previous.isConnected) previous.focus();};}, []);
  useEffect(() => {
    let active = true;
    settingsRequest<{style: string; video_ratio: string}>('/project-defaults').then(values => {
      if (!active) return;
      if (STYLES.some(option => option.id === values.style)) setStyle(values.style);
      if (VIDEO_RATIOS.some(option => option.id === values.video_ratio)) setRatio(values.video_ratio);
      setDefaultsNote('已沿用创作默认设置，可在此调整。');
    }).catch(() => {if (active) setDefaultsNote('默认设置暂不可用，先使用写实 · 16:9，可自行调整。');}).finally(() => {if (active) setDefaultsLoading(false);});
    return () => {active = false;};
  }, []);
  function close() {if (!busy && (!dirty || window.confirm('放弃尚未保存的改编设置？'))) onClose();}
  async function create() {
    if (lock.current || busy) return;
    if (disabledReason) {setError(disabledReason); return;}
    lock.current = true; setBusy(true); setError('');
    const body = {title: title.trim(), version_id: version.id, expected_revision: data.item.revision, first_chapter: first, last_chapter: last, direction: direction.trim(), episodes, target_duration: duration, style, video_ratio: ratio};
    const signature = JSON.stringify(body);
    if (attempt.current?.signature !== signature) attempt.current = {signature, key: crypto.randomUUID()};
    try {
      const result = await ipRequest<{session_id: string}>(`/${data.item.id}/projects`, {method: 'POST', body, key: attempt.current.key});
      toast.success('改编项目已创建，可进入原著分析。');
      router.push(`/projects/${result.session_id}`); onClose();
    } catch (e) {const message = errorMessage(e, '项目创建失败'); setError(message); toast.error(message);}
    finally {setBusy(false); lock.current = false;}
  }
  return <dialog ref={dialog} className="hb-project-dialog hb-ip-project-dialog ex-ip-project-dialog" aria-labelledby="ip-project-title" onCancel={event => {event.preventDefault(); close();}}>
    <form onSubmit={event => {event.preventDefault(); void create();}} onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault();}}>
      <header><div className="hb-dialog-icon"><BookOpen size={20}/></div><div><h2 id="ip-project-title">创建改编项目</h2><p>《{data.item.title}》· 正文版本 {version.number}</p></div><button className="hb-dialog-close" type="button" aria-label="关闭创建改编项目" onClick={close} disabled={busy}><X size={18}/></button></header>
      <div className="hb-ip-project-body"><fieldset className="hb-dialog-body" disabled={busy} onChange={event => {if (!(event.target instanceof HTMLInputElement && event.target.closest('.ex-ip-chapter-search'))) setDirty(true);}}>
        <label>项目名称 <span aria-hidden="true">*</span><input autoFocus required maxLength={120} value={title} onChange={event => setTitle(event.target.value)}/></label>
        <IPChapterRange chapters={version.chapters} first={first} last={last} disabled={busy} onPendingChange={setRangePending} onChange={(start, end) => {setFirst(start); setLast(end); setDirty(true); setError('');}}/>
        <div className="ex-ip-direction"><label htmlFor="ip-direction">改编方向 <span aria-hidden="true">*</span></label><textarea id="ip-direction" required rows={3} maxLength={4000} value={direction} onChange={event => setDirection(event.target.value)} placeholder="想突出谁的故事？保留哪些情节？"/><div className="ex-ip-examples" role="group" aria-label="改编方向示例">{DIRECTIONS.map(example => <button type="button" key={example.label} aria-pressed={direction === example.text} onClick={() => {setDirection(example.text); setDirty(true);}}>试试：{example.label}</button>)}</div><small>示例可继续编辑。已选范围将用于后续原著分析。</small></div>
        <div className="hb-ip-targets"><label>视觉风格<select aria-label="视觉风格" disabled={defaultsLoading} value={style} onChange={event => setStyle(event.target.value)}>{STYLES.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label><label>画面比例<select aria-label="画面比例" disabled={defaultsLoading} value={ratio} onChange={event => setRatio(event.target.value)}>{VIDEO_RATIOS.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label></div>
        <p className="hb-ip-project-note" role="status">{defaultsLoading ? '正在读取创作默认设置…' : defaultsNote}</p>
        <div className="hb-ip-targets"><label>目标集数<input type="number" min={1} max={24} step={1} required value={episodes || ''} onChange={event => setEpisodes(Number(event.target.value))}/></label><label>单集时长（秒）<input type="number" min={15} max={600} step={1} required value={duration || ''} onChange={event => setDuration(Number(event.target.value))}/></label></div>
        <p className="hb-ip-project-note">此处只保存项目，不调用模型。分析与生成在项目内确认后开始。</p>
      </fieldset>{error && <p role="alert" className="hb-project-error">{error}</p>}</div>
      <footer><small id="ip-create-help">{disabledReason || '准备就绪，可保存项目'}</small><button type="button" className="hb-project-button" onClick={close} disabled={busy}>取消</button><span title={disabledReason || undefined}><button className="hb-project-button is-primary" aria-describedby="ip-create-help" disabled={busy || !!disabledReason}>{busy ? <Loader2 size={14} className="animate-spin"/> : <ArrowRight size={14}/>} {busy ? '创建中…' : '保存并进入项目'}</button></span></footer>
    </form>
  </dialog>;
}
