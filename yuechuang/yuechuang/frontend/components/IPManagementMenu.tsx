'use client';
import {useEffect, useRef, useState} from 'react';
import {ListTree, MoreHorizontal, Pencil, Upload} from 'lucide-react';
export default function IPManagementMenu({hasVersion, canManageChapters, onAction}: {
  hasVersion: boolean; canManageChapters: boolean; onAction: (action: 'metadata' | 'version' | 'chapters') => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  function close() {setOpen(false); trigger.current?.focus();}
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
    const outside = (event: PointerEvent) => {if (!root.current?.contains(event.target as Node)) setOpen(false);};
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  function choose(action: 'metadata' | 'version' | 'chapters') {close(); onAction(action);}
  return <div className="ex-ip-manage" ref={root} onBlur={event => {if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false);}} onKeyDown={event => {
    if (event.key === 'Escape' && open) {event.preventDefault(); event.stopPropagation(); close();}
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      if (!open) {setOpen(true); return;}
      const options = Array.from(root.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') || []);
      const current = options.indexOf(document.activeElement as HTMLButtonElement);
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : event.key === 'ArrowDown' ? (current + 1) % options.length : (current - 1 + options.length) % options.length;
      options[index]?.focus();
    }
  }}><button type="button" ref={trigger} className="yc-secondary-button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}><MoreHorizontal size={17}/>管理</button>
    {open && <div className="ex-ip-manage-menu" role="menu" aria-label="作品管理"><button type="button" role="menuitem" onClick={() => choose('metadata')}><Pencil size={15}/>编辑资料</button><button type="button" role="menuitem" onClick={() => choose('version')}><Upload size={15}/>{hasVersion ? '导入新版' : '导入正文'}</button><button type="button" role="menuitem" disabled={!canManageChapters} title={canManageChapters ? undefined : '请先选择最新正文版本'} onClick={() => choose('chapters')}><ListTree size={15}/>管理章节</button></div>}
  </div>;
}
