'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Check, Clapperboard, Clock, Code2, Edit3, Film, Loader2, Plus, Save, Trash2, X } from 'lucide-react';
import Image from 'next/image';
import type { StageViewProps } from './types';
import { continuationLabel, episodeTiming, isMinimaxStartEnd, planNotes, readStoryboard, shotRanges, storyboardIssues, type StoryboardEpisode, type StoryboardSegment } from '@/lib/storyboard';
import { errorMessage } from '@/lib/workflowTypes';
import DialogueTimeline from './DialogueTimeline';
import StageProgress from './StageProgress';
import { assetUrl } from './utils';
import VideoPreview from './VideoPreview';

const SHOT_TYPES = ['远景', '中景', '近景', '过肩近景', '特写'];

export default function StoryboardStage({state, onConfirm, onRegenerate, onSaveSelections, showConfirm = true, isRunning, artifacts, projectMeta}: StageViewProps) {
  const loaded = useMemo(() => {
    try {return {episodes: readStoryboard(state.artifact?.episodes || state.artifact?.payload?.episodes || []), error: ''};}
    catch (error) {return {episodes: [] as StoryboardEpisode[], error: errorMessage(error, '分镜数据无法读取，请重新打开项目。')};}
  }, [state.artifact]);
  const [selectedId, setSelectedId] = useState('');
  const [previewTab, setPreviewTab] = useState<'image' | 'video'>('video');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<StoryboardEpisode[]>([]);
  const [rawMode, setRawMode] = useState(false);
  const [raw, setRaw] = useState('');
  const [characterText, setCharacterText] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const savingRef = useRef(false);
  const episodes = editing ? draft : loaded.episodes;
  const issues = useMemo(() => storyboardIssues(episodes), [episodes]);
  const segmentCount = episodes.reduce((n, ep) => n + ep.segments.length, 0);
  const duration = episodes.flatMap(ep => ep.segments).reduce((n, seg) => n + seg.total_duration, 0);
  const locked = saving || isRunning;
  const segments = episodes.flatMap(ep => ep.segments.map(segment => ({episode: ep.episode_number, segment})));
  const active = segments.find(item => item.segment.segment_id === selectedId) || segments[0];
  const activeId = active?.segment.segment_id;
  const selectedImage = artifacts?.reference_generation?.scenes?.find(scene => scene.id === activeId)?.selected;
  const selectedVideo = artifacts?.video_generation?.clips?.find(clip => clip.id === activeId)?.selected;
  const showVideo = !!selectedVideo && (previewTab === 'video' || !selectedImage);
  const videoMode = projectMeta?.video_generation_mode;
  const videoModel = videoMode === 'start_end_frame' ? projectMeta?.video_start_end_model || projectMeta?.video_model : projectMeta?.video_model;
  const minimax = isMinimaxStartEnd(videoMode, videoModel);
  const navGroups = segments.reduce<{key: string; items: typeof segments}[]>((groups, item) => {
    const key = item.segment.scene_key || '';
    const last = groups[groups.length - 1];
    if (key && last?.key === key) last.items.push(item);
    else groups.push({key, items: [item]});
    return groups;
  }, []);

  useEffect(() => {
    if (!editing) return;
    const warn = (event: BeforeUnloadEvent) => {event.preventDefault(); event.returnValue = '';};
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [editing]);

  const startEdit = () => {setDraft(structuredClone(loaded.episodes)); setCharacterText({}); setEditing(true); setRawMode(false); setError(''); setNotice('');};
  const cancel = () => {if (!saving && window.confirm('放弃本次未保存的修改？')) {setEditing(false); setError('');}};
  const switchMode = () => {
    if (rawMode) {
      try {setDraft(readStoryboard(JSON.parse(raw))); setCharacterText({}); setRawMode(false); setError('');}
      catch {setError('JSON 格式或分镜结构无效，请修正后再切换；你的输入已保留。');}
    } else {setRaw(JSON.stringify(draft, null, 2)); setRawMode(true); setError('');}
  };
  const save = useCallback(async () => {
    if (savingRef.current || isRunning) return;
    let value: StoryboardEpisode[];
    try {value = rawMode ? readStoryboard(JSON.parse(raw)) : draft;}
    catch {setError('JSON 格式或分镜结构无效，未保存。请检查括号、逗号及字段。'); return;}
    const invalid = storyboardIssues(value);
    if (invalid.length) {setError(invalid[0].message); return;}
    if (!onSaveSelections) {setError('项目尚未就绪，请重新打开项目；草稿仍然保留。'); return;}
    savingRef.current = true; setSaving(true); setError('');
    try {
      await onSaveSelections({episodes: value});
      setEditing(false); setNotice('已保存到项目。后续素材仍保留，请按修改后的分镜重新检查并确认。');
    } catch (err) {setError(errorMessage(err, '保存失败，草稿已保留，请重试。'));}
    finally {savingRef.current = false; setSaving(false);}
  }, [draft, raw, rawMode, isRunning, onSaveSelections]);
  const updateSegment = (epIndex: number, segIndex: number, value: StoryboardSegment) => setDraft(prev => prev.map((ep, i) => i === epIndex ? {...ep, segments: ep.segments.map((seg, j) => j === segIndex ? value : seg)} : ep));
  const confirm = () => {
    if (editing || locked || issues.length || state.error) return;
    if (window.confirm('确认采用这版分镜并进入下一阶段？下一步可能调用模型生成参考图并产生费用，请先核对对白与画面。')) onConfirm();
  };
  const regenerate = () => {
    if (window.confirm('继续生成分镜可能调用模型并产生费用，确定继续？已保存的内容会按原流程处理。')) onRegenerate();
  };

  return <div className="storyboard-workbench">
    <div className="sb-content">
      <header className="sb-header">
        <div><div className="sb-eyebrow">03 / STORYBOARD</div><h2>分镜工作室</h2><p>先安排画面，再明确每个人何时说话。保存编辑不会调用模型。</p></div>
        <span className="sb-status"><span className="sb-status-dot" />{editing ? '编辑中 · 未保存' : state.status === 'running' ? '正在生成' : showConfirm ? '等待审核' : '已确认分镜'}</span>
      </header>
      <div className="sb-stats"><span><Film size={16} />{episodes.length} 集</span><span><Clapperboard size={16} />{segmentCount} 个片段</span><span><Clock size={16} />{Number.isFinite(duration) ? duration : '—'} 秒</span></div>
      {(error || loaded.error || state.error) && <div className="sb-alert sb-alert-error" role="alert"><AlertCircle size={18} /><span>{error || loaded.error || state.error}{editing && ' 修改内容仍保留在编辑区。'}</span></div>}
      {notice && !editing && <div className="sb-alert" role="status"><Check size={18} /><span>{notice}</span></div>}
      {(state.artifact?.validation_warnings || []).map((warning, index) => <div className="sb-alert" key={index}><AlertCircle size={18} />{warning.message}</div>)}
      {editing && <div className="sb-edit-toolbar"><p>修改保存后，下游阶段需要重新确认。切换阶段或离开页面前请先保存草稿。</p><button type="button" className="sb-button sb-button-secondary" disabled={locked} onClick={switchMode}><Code2 size={16} />{rawMode ? '返回表单' : '高级 JSON'}</button></div>}
      {state.status === 'running' && <StageProgress message={state.progressMessage} progress={state.progress} color="blue" />}
      {!loaded.episodes.length && !editing && <div className="sb-empty"><Clapperboard size={36} /><h3>{loaded.error ? '分镜暂时无法显示' : state.status === 'running' ? '正在准备分镜' : '还没有分镜'}</h3><p>分镜生成后，可以在这里审核镜头、安排对白和调整时长。</p></div>}
      <div className="sb-workspace">
        {segments.length > 0 && <nav className="sb-navigator" aria-label="片段列表"><div className="sb-panel-label">故事片段 <span>{segmentCount}</span></div><div className="sb-nav-list">{navGroups.map(group => {
          const buttons = group.items.map(({episode, segment}) => {
            const index = segments.findIndex(item => item.segment.segment_id === segment.segment_id);
            const thumbnail = artifacts?.reference_generation?.scenes?.find(scene => scene.id === segment.segment_id)?.selected;
            const link = continuationLabel(segment, videoMode, videoModel);
            return <button type="button" key={segment.segment_id} aria-pressed={activeId === segment.segment_id} onClick={() => setSelectedId(segment.segment_id)} className="sb-nav-item">
              <span className="sb-nav-thumb">{thumbnail ? <Image unoptimized src={assetUrl(thumbnail)} width={160} height={90} alt="" /> : <Clapperboard size={22} />}<span>{String(index + 1).padStart(2, '0')}</span></span>
              <strong>{segment.location || '待填写场景'}</strong><small>第 {episode} 集 · {segment.total_duration} 秒{link ? ` · ${link}` : ''}{issues.some(issue => issue.segmentId === segment.segment_id) ? ' · 待调整' : ''}</small>
            </button>;
          });
          return group.key && group.items.length > 1 ? <div className="sb-scene-group" key={group.key + group.items[0].segment.segment_id}>{buttons}</div> : buttons;
        })}</div></nav>}
        <div className="sb-editor">
      {editing && rawMode ? <label className="sb-raw">分镜 JSON<textarea disabled={locked} value={raw} onChange={e => setRaw(e.target.value)} spellCheck={false} /></label> :
        episodes.map((episode, epIndex) => !episode.segments.some(segment => segment.segment_id === activeId) ? null : <section key={epIndex} className="sb-episode" aria-label={`第 ${episode.episode_number} 集分镜`}>
          {(() => { const timing = episodeTiming(episode, projectMeta?.target_duration); const notes = planNotes(episode); return <>
          <div className="sb-episode-heading"><h3>第 {episode.episode_number} 集 <span>{episode.episode_title || '未命名剧集'}</span></h3><span className={timing.over ? 'sb-duration-over' : ''} data-over={timing.over || undefined}>{timing.text}</span><span>{episode.segments.length} 个片段</span></div>
          {notes.length > 0 && <ul className="sb-plan-notes">{notes.map(note => <li key={note}>{note}</li>)}</ul>}
          </>})()}
          {episode.segments.map((segment, segIndex) => {
            if (segment.segment_id !== activeId) return null;
            const segIssues = issues.filter(issue => issue.segmentId === segment.segment_id);
            const setSegment = (value: StoryboardSegment) => updateSegment(epIndex, segIndex, value);
            const setShots = (shots: StoryboardSegment['shots']) => setSegment({...segment, shots, total_duration: shots.reduce((sum, shot) => sum + shot.duration, 0)});
            const ranges = shotRanges(segment);
            const link = continuationLabel(segment, videoMode, videoModel);
            const segmentSeconds = Number.isFinite(segment.total_duration) ? segment.total_duration : 0;
            return <article className="sb-segment" key={segment.segment_id}>
              <div className="sb-segment-heading"><span className="sb-segment-index">{String(segment.segment_number).padStart(2, '0')}</span><div><h4>{segment.location || '待填写场景'}</h4><p>{(segment.characters || []).join(' / ') || '尚未指定人物'}</p></div>{link && <span className="sb-continuation">{link}</span>}<span className="sb-duration">{Number.isFinite(segment.total_duration) ? segment.total_duration : '—'} 秒</span></div>
              {minimax && (segmentSeconds < 5 || segmentSeconds > 15) && <p className="sb-plan-notes">MiniMax 首尾帧每段只支持 5–15 秒，当前这段是 {segmentSeconds} 秒。</p>}
              {editing && <fieldset className="sb-segment-fields" disabled={locked}>
                <label>场景<input value={segment.location || ''} onChange={e => setSegment({...segment, location: e.target.value})} /></label>
                <label>出场人物（用顿号分隔）<input value={characterText[segment.segment_id] ?? (segment.characters || []).join('、')} onChange={e => {setCharacterText(prev => ({...prev, [segment.segment_id]: e.target.value})); setSegment({...segment, characters: e.target.value.split(/[、,，]/).map(name => name.trim()).filter(Boolean)});}} /></label>
              </fieldset>}
              <div className="sb-section-heading"><h4>画面安排</h4><span>{segment.shots.length} 个镜头</span></div>
              <div className="sb-shots">{segment.shots.map((shot, shotIndex) => <div className="sb-shot" key={shotIndex}>
                {editing ? <fieldset disabled={locked} className="sb-shot-form">
                  <label>景别<select value={shot.shot_type} onChange={e => setShots(segment.shots.map((item, i) => i === shotIndex ? {...item, shot_type: e.target.value} : item))}>
                    {!SHOT_TYPES.includes(shot.shot_type) && <option value={shot.shot_type}>{shot.shot_type}</option>}{SHOT_TYPES.map(type => <option key={type}>{type}</option>)}</select></label>
                  <label>时长（秒）<input type="number" min="1" step="1" value={Number.isFinite(shot.duration) ? shot.duration : ''} onChange={e => setShots(segment.shots.map((item, i) => i === shotIndex ? {...item, duration: e.target.valueAsNumber} : item))} /></label>
                  <label className="sb-shot-description">画面描述<textarea rows={2} value={shot.content} onChange={e => setShots(segment.shots.map((item, i) => i === shotIndex ? {...item, content: e.target.value} : item))} /></label>
                  <button type="button" className="sb-icon-button" aria-label={`删除镜头 ${shot.shot_number}`} disabled={segment.shots.length === 1} onClick={() => {if (window.confirm('删除这个镜头？片段总时长会相应减少，请同时检查对白时间。')) setShots(segment.shots.filter((_, i) => i !== shotIndex).map((item, i) => ({...item, shot_number: i + 1})));}}><Trash2 size={16} /></button>
                </fieldset> : <><div className="sb-shot-meta"><span>镜头 {shot.shot_number}</span><strong>{shot.shot_type}</strong><span>{ranges ? `${ranges[shotIndex].start}–${ranges[shotIndex].end}s` : `${shot.duration} 秒`}</span></div><p>{shot.content}</p></>}
              </div>)}</div>
              {editing && <button type="button" disabled={locked} className="sb-button sb-button-secondary sb-add-shot" onClick={() => setShots([...segment.shots, {shot_number: segment.shots.length + 1, shot_type: '中景', duration: 3, content: ''}])}><Plus size={16} />添加镜头</button>}
              <DialogueTimeline segment={segment} editing={editing} disabled={locked} issues={segIssues} onChange={setSegment} />
              {segIssues.some(issue => !issue.beatId) && <ul className="sb-field-errors">{segIssues.filter(issue => !issue.beatId).map((issue, i) => <li key={i}>{issue.message}</li>)}</ul>}

            </article>;
          })}
        </section>)}
        </div>
        {active && <aside className="sb-preview" aria-label="当前片段素材预览">
          <div className="sb-panel-label">素材预览 <span>片段 {active.segment.segment_number}</span></div>
          <div className="sb-preview-tabs" role="group" aria-label="预览类型">
            <button type="button" disabled={!selectedVideo} aria-pressed={showVideo} onClick={() => setPreviewTab('video')}>视频</button>
            <button type="button" disabled={!selectedImage} aria-pressed={!showVideo && !!selectedImage} onClick={() => setPreviewTab('image')}>参考图</button>
          </div>
          <div className="sb-preview-media">
            {showVideo ? <VideoPreview key={selectedVideo} label={`片段 ${active.segment.segment_number} 已有视频`} src={assetUrl(selectedVideo!)} className="w-full rounded-lg bg-black" /> : selectedImage ? <Image unoptimized src={assetUrl(selectedImage)} width={720} height={1280} alt={`片段 ${active.segment.segment_number} 参考图`} /> : <div className="sb-preview-empty"><Film size={32} /><p>本段还没有素材</p><small>确认分镜后，继续生成参考图与视频。</small></div>}
          </div>
          {selectedImage && <a href={assetUrl(selectedImage)} target="_blank" rel="noreferrer" className="sb-preview-link">打开原始参考图 ↗</a>}
          <p className="sb-preview-note">{selectedImage || selectedVideo ? '这里展示已生成素材。修改分镜后，素材不会自动更新，请重新核对画面、对白与衔接。' : '先安排画面和对白，再逐段检查生成结果。'}</p>
        </aside>}
      </div>
    </div>
    <footer className="sb-footer">
      <div className="sb-footer-note">{editing ? '草稿未保存' : '生成完成后仍需人工审核'}{editing && issues.length > 0 && !rawMode && <span> · {issues.length} 项待调整</span>}</div>
      <div className="sb-footer-actions">{editing ? <>
        <button type="button" className="sb-button sb-button-secondary" disabled={locked} onClick={cancel}><X size={16} />取消编辑</button>
        <button type="button" className="sb-button sb-button-primary" disabled={locked} onClick={save}>{saving ? <Loader2 className="animate-spin" size={16} /> : <Save size={16} />}{saving ? '保存中…' : '保存分镜'}</button>
      </> : <>
        {state.status !== 'completed' && <button type="button" className="sb-button sb-button-secondary" disabled={isRunning} onClick={regenerate}>继续生成</button>}
        {loaded.episodes.length > 0 && <button type="button" className="sb-button sb-button-secondary" disabled={isRunning} onClick={startEdit}><Edit3 size={16} />编辑分镜与对白</button>}
        {showConfirm && loaded.episodes.length > 0 && <button type="button" className="sb-button sb-button-primary" disabled={isRunning || !!state.error || issues.length > 0} onClick={confirm}><Check size={16} />确认分镜并继续</button>}
      </>}</div>
    </footer>
  </div>;
}
