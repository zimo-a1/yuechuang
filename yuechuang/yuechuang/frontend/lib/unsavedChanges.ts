'use client';
import {useEffect} from 'react';

const editors = new Set<symbol>();
export function hasUnsavedChanges(){return editors.size>0;}
export function canLeaveEditor() {
  return !editors.size || window.confirm('当前修改尚未保存，确定离开吗？');
}
export function useUnsavedChanges(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const token = Symbol();
    editors.add(token);
    const unload = (event: BeforeUnloadEvent) => {event.preventDefault(); event.returnValue = '';};
    const click = (event: MouseEvent) => {
      const anchor = (event.target as Element)?.closest?.('a[href]');
      if (anchor && !event.defaultPrevented && !event.metaKey && !event.ctrlKey && !canLeaveEditor()) {
        event.preventDefault(); event.stopPropagation();
      }
    };
    window.addEventListener('beforeunload', unload);
    document.addEventListener('click', click, true);
    return () => {editors.delete(token); window.removeEventListener('beforeunload', unload); document.removeEventListener('click', click, true);};
  }, [dirty]);
}
