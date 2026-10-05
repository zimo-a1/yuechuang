'use client';
import {useRef,useSyncExternalStore,type ReactNode,type CSSProperties} from 'react';

// Pixel preferences are separate from the previous percentage layout.
const KEY='yc-studio-video-columns-v2-px';
const DEFAULT='236,340';
const subscribe=(listener:()=>void)=>{
  window.addEventListener('yc-studio-layout',listener);window.addEventListener('storage',listener);
  return()=>{window.removeEventListener('yc-studio-layout',listener);window.removeEventListener('storage',listener);};
};
function read(){try{return localStorage.getItem(KEY)||DEFAULT;}catch{return DEFAULT;}}
function clamp(value:number,min:number,max:number){return Math.max(min,Math.min(max,value));}

export default function StudioColumns({children}:{children:ReactNode}){
  const value=useSyncExternalStore(subscribe,read,()=>DEFAULT);
  const ref=useRef<HTMLDivElement>(null);const parts=value.split(',').map(Number);
  const left=Number.isFinite(parts[0])?clamp(parts[0],180,360):236;
  const right=Number.isFinite(parts[1])?clamp(parts[1],260,480):340;
  function save(a:number,b:number){
    try{localStorage.setItem(KEY,`${Math.round(a)},${Math.round(b)}`);window.dispatchEvent(new Event('yc-studio-layout'));}
    catch{/* Default columns remain usable when preference storage is unavailable. */}
  }
  const separator=(side:'left'|'right')=><div role="separator"
    aria-label={side==='left'?'调整镜头列表宽度':'调整预览区域宽度'} aria-orientation="vertical"
    aria-valuenow={side==='left'?left:right} aria-valuetext={`${side==='left'?left:right} 像素`}
    aria-valuemin={side==='left'?180:260} aria-valuemax={side==='left'?360:480}
    tabIndex={0} className={`yc-column-resizer yc-resizer-${side}`}
    onDoubleClick={()=>save(236,340)}
    onKeyDown={event=>{
      if(event.key==='Home'){event.preventDefault();save(236,340);return;}
      if(event.key!=='ArrowLeft'&&event.key!=='ArrowRight')return;
      event.preventDefault();const delta=event.key==='ArrowRight'?10:-10;
      save(side==='left'?clamp(left+delta,180,360):left,side==='right'?clamp(right-delta,260,480):right);
    }}
    onPointerDown={event=>{event.currentTarget.setPointerCapture(event.pointerId);event.preventDefault();}}
    onPointerMove={event=>{
      if(!event.currentTarget.hasPointerCapture(event.pointerId)||!ref.current)return;
      const rect=ref.current.getBoundingClientRect();
      save(side==='left'?clamp(event.clientX-rect.left,180,360):left,side==='right'?clamp(rect.right-event.clientX,260,480):right);
    }}
    onPointerUp={event=>{if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);}}
    onPointerCancel={event=>{if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);}}
    title="拖动或用左右方向键调整，双击或 Home 恢复默认"/>;
  return <div ref={ref} className="yc-video-columns" style={{'--studio-left':`${left}px`,'--studio-right':`${right}px`} as CSSProperties}>{children}{separator('left')}{separator('right')}</div>;
}
