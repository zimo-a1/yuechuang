'use client';
import {useCallback, useEffect, useSyncExternalStore} from 'react';

const EVENT = 'yuechuang-editor-recovery';
function subscribe(listener: () => void) {
  window.addEventListener(EVENT, listener);
  return () => window.removeEventListener(EVENT, listener);
}

// Tab-local recovery also covers browser Back, which Next's router cannot cancel.
export function useEditorRecovery(key: string, value: string, dirty: boolean) {
  const read = useCallback(() => {
    try {return window.sessionStorage.getItem(key);} catch {return null;}
  }, [key]);
  const recovery = useSyncExternalStore(subscribe, read, () => null);
  const clearRecovery = useCallback(() => {
    try {window.sessionStorage.removeItem(key); window.dispatchEvent(new Event(EVENT));} catch { /* in-memory input remains available */ }
  }, [key]);
  useEffect(() => {
    if (!dirty) return;
    try {window.sessionStorage.setItem(key, value); window.dispatchEvent(new Event(EVENT));} catch { /* navigation warning remains enabled */ }
  }, [key, value, dirty]);
  return {recovery, clearRecovery};
}
