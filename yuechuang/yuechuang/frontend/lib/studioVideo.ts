import type {AssetItem,WorkflowArtifact,WorkflowSegment} from './workflowTypes';
export function boundAssets(segment:WorkflowSegment, artifact:WorkflowArtifact):AssetItem[]{
  const assets=[...(artifact.characters||[]),...(artifact.settings||[]),...(artifact.props||[])].filter(a=>a.selected);
  if(segment.reference_asset_ids!==undefined&&segment.reference_asset_ids!==null)return segment.reference_asset_ids.flatMap(id=>assets.filter(a=>a.id===id));
  return assets.filter(a=>(segment.characters||[]).some(n=>n===a.name||n.includes(a.name)||a.name.includes(n))||Boolean(segment.location&&(segment.location.includes(a.name)||a.name.includes(segment.location))));
}
export function segmentDuration(segment:WorkflowSegment){return segment.total_duration||segment.shots.reduce((sum,s)=>sum+(s.duration||0),0);}

/** Set the segment length in whole seconds. Extra or missing time is applied from the last shot backward, and no shot goes below 1 second. */
export function applySegmentDuration(segment:WorkflowSegment,seconds:number,limits?:{min:number;max:number}):Partial<WorkflowSegment>{
  if(!segment.shots.length||!Number.isFinite(seconds))return {};
  const minimum=Math.max(limits?.min??1,segment.shots.length);
  const maximum=Math.max(minimum,limits?.max??600);
  const total=Math.min(maximum,Math.max(minimum,Math.trunc(seconds)));
  const shots=segment.shots.map(shot=>({...shot}));
  let delta=total-shots.reduce((sum,shot)=>sum+(shot.duration||0),0);
  for(let index=shots.length-1;index>=0&&delta!==0;index-=1){
    const duration=shots[index].duration||1;
    const next=delta>0?duration+delta:Math.max(1,duration+delta);
    delta-=next-duration;
    shots[index]={...shots[index],duration:next};
  }
  const next:Partial<WorkflowSegment>={shots,total_duration:total};
  const control=segment.shot_control;
  if(control&&control.beats.some(beat=>beat.end>total)){
    const previous=segmentDuration(segment)||total;
    const scale=total/previous;
    let cursor=0;
    next.shot_control={...control,beats:control.beats.map(beat=>{
      const start=Math.min(total,Math.max(cursor,Math.round(beat.start*scale*10)/10));
      const end=Math.min(total,Math.max(start+0.1,Math.round(beat.end*scale*10)/10));
      cursor=end;
      return {...beat,start,end};
    })};
  }
  return next;
}
export function segmentSummary(segment:WorkflowSegment){return segment.shots.map(s=>s.content||s.plot||'').join('\n');}
export function videoItemStatus(item?:AssetItem){return (item?.generation_status||item?.status)==='failed'?'failed':(item?.generation_status||item?.status)==='running'?'running':item?.selected?'done':'pending';}
export function exportOrder(clips:AssetItem[],saved?:string[]){return [...(saved||[]).filter(id=>clips.some(c=>c.id===id)),...clips.filter(c=>!saved?.includes(c.id)).map(c=>c.id)];}
