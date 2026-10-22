// Kept independent of components so list, workspace and regression tests share the same rules.
export const PROJECT_STAGES = ['script_generation', 'character_design', 'storyboard', 'reference_generation', 'video_generation', 'post_production'];
const STUDIO_STEP: Record<string, string> = {character_design: 'assets', storyboard: 'videos', reference_generation: 'videos', video_generation: 'videos', post_production: 'export'};
export const STAGE_NAMES: Record<string, string> = {script_generation: '剧本', character_design: '素材', storyboard: '分镜', reference_generation: '分镜 · 参考图', video_generation: '视频', post_production: '成片'};
type ProjectState = {status: Record<string, string>; confirmed_stages?: string[]; current_stage?: string};

export function projectProgress(project: ProjectState) {
  const confirmed = (project.confirmed_stages || []).filter(s => PROJECT_STAGES.includes(s));
  const stages = PROJECT_STAGES.map(s => project.status[s]);
  const running = PROJECT_STAGES.find(s => project.status[s] === 'running');
  const attention = PROJECT_STAGES.find(s => ['error', 'stopped'].includes(project.status[s]));
  const review = PROJECT_STAGES.find(s => ['waiting', 'completed', 'session_completed'].includes(project.status[s]) && !confirmed.includes(s));
  const done = PROJECT_STAGES.every(s => confirmed.includes(s));
  const firstPending = PROJECT_STAGES.find(s => !confirmed.includes(s)) || PROJECT_STAGES[5];
  const stage = running || attention || review || firstPending;
  return {stage, completed: new Set(confirmed).size, total: PROJECT_STAGES.length,
    state: running ? 'running' : attention ? 'attention' : done ? 'completed' : review ? 'review' : stages.every(s => !s || s === 'pending') ? 'draft' : 'progress',
    label: running ? '生成中' : attention ? (project.status[attention] === 'stopped' ? '已停止' : '需要处理') : done ? '流程已完成' : review ? '待确认' : stages.every(s => !s || s === 'pending') ? '草稿' : '制作中',
  };
}

export function projectHref(id: string, stage?: string) {
  const encoded = encodeURIComponent(id);
  if (!stage || !PROJECT_STAGES.includes(stage)) return `/studio/${encoded}`;
  return `/studio/${encoded}?step=${STUDIO_STEP[stage] || 'rewrite'}&stage=${stage}`;
}

/** A confirmation only records the gate. It never starts the next stage. */
export function confirmationFollowUp(nextStage: string | undefined, sessionId: string) {
  return {execute: false as const, href: nextStage ? projectHref(sessionId, nextStage) : null};
}

export function nextAssetId(prefix: 'char_' | 'set_', existing: string[]) {
  const used = new Set(existing);
  let number = 1;
  while (used.has(`${prefix}${String(number).padStart(3, '0')}`)) number += 1;
  return `${prefix}${String(number).padStart(3, '0')}`;
}

export const SOURCE_NAMES = {original: '一句话原创', ip: 'IP 改编', legacy: '历史项目'};
