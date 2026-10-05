'use client';
import {useEffect, useRef, useState} from 'react';
import {CheckCircle2, CircleAlert, Info, X} from 'lucide-react';
import {TOAST_EVENT, type ToastMessage} from '@/lib/uiFeedback';

export default function ToastHost() {
  const [items, setItems] = useState<ToastMessage[]>([]);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  useEffect(() => {
    const pending = timers.current;
    const show = (event: Event) => {
      const item = (event as CustomEvent<ToastMessage>).detail;
      if (!item?.message) return;
      setItems(current => [...current.filter(old => old.message !== item.message), item].slice(-4));
      pending.set(item.id, setTimeout(() => {
        setItems(current => current.filter(old => old.id !== item.id));
        pending.delete(item.id);
      }, item.tone === 'error' ? 6000 : 3000));
    };
    window.addEventListener(TOAST_EVENT, show);
    return () => {window.removeEventListener(TOAST_EVENT, show); pending.forEach(clearTimeout); pending.clear();};
  }, []);
  return <div className="yc-toast-host" aria-label="操作反馈">{items.map(item => {
    const Icon = item.tone === 'success' ? CheckCircle2 : item.tone === 'error' ? CircleAlert : Info;
    return <div key={item.id} className="yc-toast" data-tone={item.tone} role={item.tone === 'error' ? 'alert' : 'status'}><Icon size={18}/><span>{item.message}</span><button type="button" aria-label="关闭提示" onClick={() => {clearTimeout(timers.current.get(item.id)); timers.current.delete(item.id); setItems(current => current.filter(old => old.id !== item.id));}}><X size={14}/></button></div>;
  })}</div>;
}
