'use client';

import { MessageSquare, Plus, Trash2 } from 'lucide-react';
import type { ShotBeat } from '@/lib/workflowTypes';
import { nextBeat, type StoryboardSegment, type StoryboardIssue } from '@/lib/storyboard';

export default function DialogueTimeline({ segment, editing, disabled, issues, onChange }: {
  segment: StoryboardSegment; editing: boolean; disabled: boolean; issues: StoryboardIssue[];
  onChange: (segment: StoryboardSegment) => void;
}) {
  const beats = segment.shot_control?.beats || [];
  const change = (next: ShotBeat[]) => onChange({...segment, shot_control: {version: 1, beats: next}});
  const update = (index: number, patch: Partial<ShotBeat>) => change(beats.map((beat, i) => i === index ? {...beat, ...patch} : beat));
  const nextStart = beats.length ? beats[beats.length - 1].end + 0.5 : 0.5;
  const canAdd = segment.total_duration - nextStart >= 2;
  return <section aria-label={`片段 ${segment.segment_number} 对白时间表`} className="sb-timeline">
    <div className="sb-section-heading"><h4><MessageSquare size={16} />对白与动作</h4><span>片段内时间 · 秒</span></div>
    {beats.length > 0 && <div className="sb-time-track" aria-hidden="true">
      {beats.map((beat, index) => <span key={index} className={beat.kind === 'dialogue' ? 'sb-time-dialogue' : 'sb-time-action'}
        style={{left: `${Math.max(0, Math.min(100, beat.start / segment.total_duration * 100))}%`, width: `${Math.max(0, Math.min(100, (beat.end - beat.start) / segment.total_duration * 100))}%`}} />)}
    </div>}
    {!beats.length && <p className="sb-empty-inline">尚未设置对白时间表。原分镜仍然保留，可以手动添加对白或动作；不会自动生成台词。</p>}
    <div className="sb-beats">{beats.map((beat, index) => {
      const beatErrors = issues.filter(issue => issue.beatId === beat.unit_id);
      const prefix = `${segment.segment_id}-beat-${index}`;
      return <div key={index} className={`sb-beat ${beatErrors.length ? 'sb-beat-invalid' : ''}`}>
        <div className="sb-beat-heading"><span className="sb-beat-number">{String(index + 1).padStart(2, '0')}</span>
          <strong>{beat.kind === 'dialogue' ? '对白' : '动作'}</strong>
          {!editing && <span className="sb-beat-time">{beat.start}–{beat.end} 秒</span>}
          {editing && <button type="button" disabled={disabled} className="sb-icon-button" aria-label={`删除第 ${index + 1} 条${beat.kind === 'dialogue' ? '对白' : '动作'}`}
            onClick={() => {if (window.confirm('删除这一条对白或动作？保存后才会生效。')) change(beats.filter((_, i) => i !== index));}}><Trash2 size={16} /></button>}
        </div>
        {editing ? <fieldset disabled={disabled} className="sb-beat-form">
          <label htmlFor={`${prefix}-start`}>开始（秒）<input id={`${prefix}-start`} type="number" min="0" step="0.5" value={Number.isFinite(beat.start) ? beat.start : ''} onChange={e => update(index, {start: e.target.valueAsNumber})} /></label>
          <label htmlFor={`${prefix}-end`}>结束（秒）<input id={`${prefix}-end`} type="number" min="0" step="0.5" value={Number.isFinite(beat.end) ? beat.end : ''} onChange={e => update(index, {end: e.target.valueAsNumber})} /></label>
          {beat.kind === 'dialogue' && <label htmlFor={`${prefix}-speaker`}>说话人<select id={`${prefix}-speaker`} value={beat.speaker} onChange={e => update(index, {speaker: e.target.value, listeners: beat.listeners.filter(name => name !== e.target.value)})}>
            <option value="">选择人物</option>{(segment.characters || []).map(name => <option key={name} value={name}>{name}</option>)}
          </select></label>}
          {beat.kind === 'dialogue' && <fieldset className="sb-listeners"><legend>倾听者（不说话）</legend>
            {(segment.characters || []).filter(name => name !== beat.speaker).map(name => <label key={name}><input type="checkbox" checked={beat.listeners.includes(name)} onChange={e => update(index, {listeners: e.target.checked ? [...beat.listeners, name] : beat.listeners.filter(item => item !== name)})} />{name}</label>)}
          </fieldset>}
          <label className="sb-full" htmlFor={`${prefix}-text`}>{beat.kind === 'dialogue' ? '台词' : '动作描述'}<textarea id={`${prefix}-text`} rows={2} value={beat.text} onChange={e => update(index, {text: e.target.value})} aria-invalid={beatErrors.length > 0} aria-describedby={beatErrors.length ? `${prefix}-errors` : undefined} /></label>
        </fieldset> : <div className="sb-beat-copy">
          <p>{beat.kind === 'dialogue' && <strong>{beat.speaker} · </strong>}{beat.text}</p>
          {beat.kind === 'dialogue' && <span>{beat.listeners.length ? `倾听：${beat.listeners.join('、')}` : '其他人物保持安静'}</span>}
        </div>}
        {beatErrors.length > 0 && <ul id={`${prefix}-errors`} className="sb-field-errors">{beatErrors.map((issue, i) => <li key={i}>{issue.message}</li>)}</ul>}
      </div>;
    })}</div>
    {editing && <div className="sb-add-beat">
      <button type="button" className="sb-button sb-button-secondary" disabled={disabled || !canAdd || !(segment.characters || []).length} onClick={() => change([...beats, nextBeat(segment, 'dialogue')])}><Plus size={16} />添加对白</button>
      <button type="button" className="sb-button sb-button-secondary" disabled={disabled || !canAdd} onClick={() => change([...beats, nextBeat(segment, 'action')])}><Plus size={16} />添加动作</button>
      {!canAdd && <span>剩余时长不足 2 秒，请先调整镜头时长或时间表。</span>}
      {!(segment.characters || []).length && <span>先填写出场人物，再添加对白。</span>}
    </div>}
  </section>;
}
