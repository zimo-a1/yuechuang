/** Keep a model switch and its supported output format in one saved change. */
export function compatibleVideoFormat(current:{video_resolution:string;video_ratio:string},caps?:{resolutions?:unknown;ratios?:unknown}){
  const resolutions=Array.isArray(caps?.resolutions)?caps.resolutions.filter((v):v is string=>typeof v==='string'):[];
  const ratios=Array.isArray(caps?.ratios)?caps.ratios.filter((v):v is string=>typeof v==='string'):[];
  return {video_resolution:resolutions.length&&!resolutions.includes(current.video_resolution)?resolutions[0]:current.video_resolution,
    video_ratio:ratios.length&&!ratios.includes(current.video_ratio)?ratios[0]:current.video_ratio};
}
