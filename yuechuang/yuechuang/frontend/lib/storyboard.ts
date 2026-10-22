import type { ShotBeat, ShotControl } from './workflowTypes';

export interface StoryboardShot { shot_number: number; shot_type: string; duration: number; content: string; unit_ids?: string[]; dialogue?: unknown }
export interface StoryboardSegment {
  segment_id: string; segment_number: number; location: string; characters: string[];
  total_duration: number; shots: StoryboardShot[]; shot_control?: ShotControl | null;
  plan_rule?: string; scene_key?: string; continues_previous?: boolean; speech_seconds?: number;
}
export interface StoryboardEpisode {
  episode_number: number; episode_title?: string; segments: StoryboardSegment[];
  plan_rule?: string; estimated_duration?: number; target_duration?: number; plan_notes?: string[];
}
export interface StoryboardIssue { segmentId: string; beatId?: string; message: string }
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(x => typeof x === 'string');
const positiveInt = (value: number) => Number.isInteger(value) && value > 0;
export const speechSeconds = (text: string) => Math.max(2, Math.ceil(Array.from(text.replace(/\s/g, '')).length / 4));

export function episodeTiming(episode: StoryboardEpisode, fallbackTarget?: number) {
  const estimated = typeof episode.estimated_duration === 'number'
    ? episode.estimated_duration
    : episode.segments.reduce((sum, segment) => sum + (Number(segment.total_duration) || 0), 0);
  const target = typeof episode.target_duration === 'number' ? episode.target_duration : fallbackTarget;
  return {
    estimated, target,
    over: typeof target === 'number' && estimated > target,
    text: typeof target === 'number' ? `预计总时长 ${estimated} 秒 / 目标时长 ${target} 秒` : `预计总时长 ${estimated} 秒`,
  };
}

export function planNotes(episode: {plan_notes?: unknown}) {
  return Array.isArray(episode.plan_notes) ? episode.plan_notes.filter((note): note is string => typeof note === 'string' && note.trim() !== '') : [];
}

/** Cumulative shot ranges for the merged-scene rule. Older segments keep their original duration label. */
export function shotRanges(segment: {plan_rule?: string; shots: {duration?: number}[]}) {
  if (segment.plan_rule !== 'scene_merge_v1') return null;
  let cursor = 0;
  return segment.shots.map(shot => {
    const start = cursor;
    cursor += Number(shot.duration) || 0;
    return {start, end: cursor};
  });
}

export function isMinimaxStartEnd(mode?: string, model?: string) {
  if (mode !== 'start_end_frame') return false;
  return (model || '').split('/').filter(Boolean).pop() === 'MiniMax-H3-Max-Turbo';
}

export function continuationLabel(segment: {continues_previous?: boolean}, mode?: string, model?: string) {
  if (segment.continues_previous !== true) return '';
  return isMinimaxStartEnd(mode, model) ? '接上一段画面' : '同场景';
}

/** Validate shape before rendering raw JSON; preserve all extra/legacy metadata. */
export function readStoryboard(value: unknown): StoryboardEpisode[] {
  if (!Array.isArray(value)) throw new Error('分镜数据必须是剧集数组。');
  for (const ep of value) {
    if (!record(ep) || typeof ep.episode_number !== 'number' || !Array.isArray(ep.segments) ||
        (ep.episode_title !== undefined && typeof ep.episode_title !== 'string')) throw new Error('每集需要集数、文字标题和分镜片段数组。');
    for (const seg of ep.segments) {
      if (!record(seg) || typeof seg.segment_id !== 'string' || typeof seg.segment_number !== 'number' ||
          typeof seg.total_duration !== 'number' || !Array.isArray(seg.shots) ||
          (seg.location !== undefined && typeof seg.location !== 'string') ||
          (seg.characters !== undefined && !strings(seg.characters))) throw new Error('片段编号、人物、时长或镜头数据格式不正确。');
      for (const shot of seg.shots) {
        if (!record(shot) || typeof shot.shot_number !== 'number' || typeof shot.shot_type !== 'string' ||
            typeof shot.duration !== 'number' || typeof shot.content !== 'string') throw new Error('每个镜头需要编号、景别、时长和画面描述。');
      }
      if (seg.shot_control != null) {
        const control = seg.shot_control;
        if (!record(control) || control.version !== 1 || !Array.isArray(control.beats)) throw new Error('对白时间表版本或列表格式不正确。');
        for (const beat of control.beats) {
          if (!record(beat) || typeof beat.unit_id !== 'string' || !['dialogue', 'action'].includes(String(beat.kind)) ||
              typeof beat.start !== 'number' || typeof beat.end !== 'number' || typeof beat.text !== 'string' ||
              (beat.speaker !== undefined && typeof beat.speaker !== 'string') || (beat.listeners !== undefined && !strings(beat.listeners))) throw new Error('对白需要起止时间、内容、说话人和倾听者列表。');
        }
      }
    }
  }
  return value.map(ep => ({...ep, segments: ep.segments.map((seg: Record<string, unknown>) => ({
    ...seg, location: seg.location ?? '', characters: seg.characters ?? [],
    ...(seg.shot_control ? {shot_control: {...(seg.shot_control as ShotControl), beats: (seg.shot_control as ShotControl).beats.map(beat => ({...beat, speaker: beat.speaker ?? '', listeners: beat.listeners ?? []}))}} : {}),
  }))})) as StoryboardEpisode[];
}

/** Mirrors public timing rules for immediate feedback; the server still validates saves. */
export function storyboardIssues(episodes: StoryboardEpisode[]): StoryboardIssue[] {
  const issues: StoryboardIssue[] = [];
  if (!episodes.length) issues.push({segmentId: '', message: '至少保留一集分镜。'});
  const episodeIds = new Set<number>(), segmentIds = new Set<string>();
  for (const ep of episodes) {
    if (!positiveInt(ep.episode_number) || episodeIds.has(ep.episode_number)) issues.push({segmentId: '', message: '集数必须为不重复的正整数。'});
    episodeIds.add(ep.episode_number);
    if (!ep.segments.length) issues.push({segmentId: '', message: '每集至少保留一个片段。'});
    const segmentNumbers = new Set<number>();
    for (const seg of ep.segments) {
      const add = (message: string, beatId?: string) => issues.push({segmentId: seg.segment_id, beatId, message: `片段 ${seg.segment_number}：${message}`});
      if (!seg.segment_id.trim() || segmentIds.has(seg.segment_id) || !positiveInt(seg.segment_number) || segmentNumbers.has(seg.segment_number)) add('片段编号不能为空或重复。');
      segmentIds.add(seg.segment_id); segmentNumbers.add(seg.segment_number);
      if (!positiveInt(seg.total_duration)) add('片段时长须为正整数秒。');
      if (!seg.shots.length) add('至少保留一个镜头。');
      const shotNumbers = new Set<number>();
      for (const shot of seg.shots) {
        if (!positiveInt(shot.shot_number) || shotNumbers.has(shot.shot_number)) add('镜头编号须为不重复的正整数。');
        shotNumbers.add(shot.shot_number);
        if (!positiveInt(shot.duration) || !shot.shot_type.trim() || !shot.content.trim()) add('镜头须填写景别、画面描述和正整数时长。');
      }
      if (seg.shots.reduce((sum, shot) => sum + shot.duration, 0) !== seg.total_duration) add('镜头时长之和须等于片段总时长。');
      if (!seg.shot_control) continue;
      if (!seg.shot_control.beats.length) add('时间表至少保留一条对白或动作。');
      let previous = 0;
      const ids = new Set<string>(), actors = new Set(seg.characters || []);
      for (const beat of seg.shot_control.beats) {
        if (!Number.isFinite(beat.start) || !Number.isFinite(beat.end) || beat.start < 0 || beat.end <= beat.start || beat.end > seg.total_duration) add('起止时间须有效，结束时间不能超出片段。', beat.unit_id);
        if (beat.start < previous) add('时间与上一条重叠，请按先后顺序调整。', beat.unit_id);
        if (!beat.unit_id.trim() || ids.has(beat.unit_id)) add('对白/动作编号不能为空或重复。', beat.unit_id);
        if (!beat.text.trim()) add('请填写对白或动作内容。', beat.unit_id);
        if (beat.kind === 'dialogue') {
          if (!actors.has(beat.speaker) || !beat.speaker.trim()) add('请选择本片段的说话人。', beat.unit_id);
          if (beat.listeners.includes(beat.speaker)) add('说话人不能同时作为倾听者。', beat.unit_id);
          if (beat.end - beat.start < speechSeconds(beat.text)) add(`这句台词预计至少需要 ${speechSeconds(beat.text)} 秒，请延长时间或精简台词。`, beat.unit_id);
        } else if (beat.speaker) add('动作不能指定说话人。', beat.unit_id);
        if (beat.listeners.some(name => !actors.has(name))) add('倾听者须属于本片段人物。', beat.unit_id);
        previous = beat.end; ids.add(beat.unit_id);
      }
    }
  }
  return issues;
}

export function nextBeat(segment: StoryboardSegment, kind: ShotBeat['kind']): ShotBeat {
  const beats = segment.shot_control?.beats || [];
  const start = beats.length ? beats[beats.length - 1].end + 0.5 : 0.5;
  let index = 1;
  while (beats.some(beat => beat.unit_id === `U${String(index).padStart(3, '0')}`)) index++;
  const speaker = kind === 'dialogue' ? segment.characters?.[0] || '' : '';
  return {unit_id: `U${String(index).padStart(3, '0')}`, kind, start, end: Math.min(start + 2, segment.total_duration),
    text: '', speaker, listeners: kind === 'dialogue' ? (segment.characters || []).filter(name => name !== speaker) : []};
}
