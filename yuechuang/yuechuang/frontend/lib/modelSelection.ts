const VIDEO_MODE_LABELS: Record<string, string> = {text: '文生视频', first_frame: '首帧生视频', start_end_frame: '首尾帧', reference: '参考图'};

/** List only the video settings that actually change, so a mode switch is not described as a model rename. */
export function videoConfigChangeLines(before: {video_generation_mode?: string; video_model?: string; video_resolution?: string}, after: {video_generation_mode?: string; video_model?: string; video_resolution?: string}, modelName: (id: string) => string) {
  const lines: string[] = [];
  if ((before.video_generation_mode || 'first_frame') !== (after.video_generation_mode || 'first_frame')) {
    lines.push(`视频方式：${VIDEO_MODE_LABELS[before.video_generation_mode || 'first_frame'] || before.video_generation_mode} → ${VIDEO_MODE_LABELS[after.video_generation_mode || 'first_frame'] || after.video_generation_mode}`);
  }
  if ((before.video_model || '') !== (after.video_model || '')) lines.push(`模型：${modelName(before.video_model || '')} → ${modelName(after.video_model || '')}`);
  if ((before.video_resolution || '') !== (after.video_resolution || '')) lines.push(`分辨率：${before.video_resolution || '未设置'} → ${after.video_resolution || '未设置'}`);
  return lines;
}

/** Keep a model switch and its supported output format in one saved change. */
export function compatibleVideoFormat(current:{video_resolution:string;video_ratio:string},caps?:{resolutions?:unknown;ratios?:unknown}){
  const resolutions=Array.isArray(caps?.resolutions)?caps.resolutions.filter((v):v is string=>typeof v==='string'):[];
  const ratios=Array.isArray(caps?.ratios)?caps.ratios.filter((v):v is string=>typeof v==='string'):[];
  return {video_resolution:resolutions.length&&!resolutions.includes(current.video_resolution)?resolutions[0]:current.video_resolution,
    video_ratio:ratios.length&&!ratios.includes(current.video_ratio)?ratios[0]:current.video_ratio};
}
