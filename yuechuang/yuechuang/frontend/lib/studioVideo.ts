import type {AssetItem,WorkflowArtifact,WorkflowSegment} from './workflowTypes';
export function boundAssets(segment:WorkflowSegment, artifact:WorkflowArtifact):AssetItem[]{
  const assets=[...(artifact.characters||[]),...(artifact.settings||[]),...(artifact.props||[])].filter(a=>a.selected);
  if(segment.reference_asset_ids!==undefined&&segment.reference_asset_ids!==null)return segment.reference_asset_ids.flatMap(id=>assets.filter(a=>a.id===id));
  return assets.filter(a=>(segment.characters||[]).some(n=>n===a.name||n.includes(a.name)||a.name.includes(n))||Boolean(segment.location&&(segment.location.includes(a.name)||a.name.includes(segment.location))));
}
export function segmentDuration(segment:WorkflowSegment){return segment.total_duration||segment.shots.reduce((sum,s)=>sum+(s.duration||0),0);}
export function segmentSummary(segment:WorkflowSegment){return segment.shots.map(s=>s.content||s.plot||'').join('\n');}
export function videoItemStatus(item?:AssetItem){return (item?.generation_status||item?.status)==='failed'?'failed':(item?.generation_status||item?.status)==='running'?'running':item?.selected?'done':'pending';}
export function exportOrder(clips:AssetItem[],saved?:string[]){return [...(saved||[]).filter(id=>clips.some(c=>c.id===id)),...clips.filter(c=>!saved?.includes(c.id)).map(c=>c.id)];}
