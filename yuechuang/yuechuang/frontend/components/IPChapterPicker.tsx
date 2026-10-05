'use client';

import {useEffect, useId, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {Check, ChevronDown, ChevronLeft, ChevronRight, Search, X} from 'lucide-react';
import type {IPChapter} from '@/lib/ipApi';
import {searchChapters} from '@/lib/chapterSelection';

const PAGE_SIZE = 50;

export default function IPChapterPicker({label, chapters, value, onChange, disabled = false}: {
  label: string;
  chapters: IPChapter[];
  value: number;
  onChange: (ordinal: number) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const matches = useMemo(() => searchChapters(chapters, query), [chapters, query]);
  const shown = matches.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const current = chapters[value - 1];

  function close(returnFocus = false) {
    setOpen(false);
    if (returnFocus) trigger.current?.focus();
  }
  function show() {
    if (disabled || !chapters.length) return;
    const index = Math.max(0, value - 1);
    setQuery(''); setActive(index); setPage(Math.floor(index / PAGE_SIZE)); setOpen(true);
  }
  function choose(ordinal: number) {onChange(ordinal); close(true);}
  function changePage(next: number) {setPage(next); setActive(next * PAGE_SIZE);}

  useEffect(() => {
    if (!open) return;
    search.current?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) close();
    };
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [open]);

  useLayoutEffect(() => {
    if (!open) return;
    const position = () => {
      const rect = trigger.current?.getBoundingClientRect();
      const panel = popup.current;
      if (!rect || !panel) return;
      const viewport = window.visualViewport;
      const topEdge = (viewport?.offsetTop || 0) + 12;
      const viewportHeight = viewport?.height || window.innerHeight;
      const bottomEdge = topEdge + viewportHeight - 24;
      const width = Math.min(Math.max(rect.width, 380), window.innerWidth - 32);
      panel.style.width = `${width}px`;
      panel.style.maxHeight = `${Math.min(420, viewportHeight - 24)}px`;
      const height = panel.getBoundingClientRect().height;
      const below = rect.bottom + 6;
      const above = rect.top - height - 6;
      panel.style.top = `${Math.max(topEdge, Math.min(below + height <= bottomEdge ? below : above, bottomEdge - height))}px`;
      panel.style.left = `${Math.max(16, Math.min(rect.left, window.innerWidth - width - 16))}px`;
    };
    position();
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    window.visualViewport?.addEventListener('resize', position);
    window.visualViewport?.addEventListener('scroll', position);
    return () => {
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
      window.visualViewport?.removeEventListener('resize', position);
      window.visualViewport?.removeEventListener('scroll', position);
    };
  }, [open, query, page]);

  useEffect(() => {
    if (!open) return;
    const option = document.getElementById(`${id}-option-${active}`);
    const parent = list.current;
    if (option && parent) {
      const row = option.getBoundingClientRect();
      const bounds = parent.getBoundingClientRect();
      if (row.top < bounds.top) parent.scrollTop -= bounds.top - row.top;
      else if (row.bottom > bounds.bottom) parent.scrollTop += row.bottom - bounds.bottom;
    }
  }, [active, open, page, query, id]);

  return <div ref={root} className="hb-chapter-picker" onBlur={event => {
    if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node)) close();
  }} onKeyDown={event => {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    if (event.key === 'Escape' && open) {
      event.preventDefault(); event.stopPropagation(); close(true); return;
    }
    if (!open && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault(); show(); return;
    }
    if (!open || event.target !== search.current) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      if (matches[active]) choose(matches[active].ordinal);
    } else if (['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp'].includes(event.key)) {
      event.preventDefault();
      const delta = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : event.key === 'PageDown' ? PAGE_SIZE : -PAGE_SIZE;
      const next = Math.max(0, Math.min(matches.length - 1, active + delta));
      setActive(next); setPage(Math.floor(next / PAGE_SIZE));
    }
  }}>
    <span id={`${id}-label`} className="hb-chapter-label">{label}</span>
    <button ref={trigger} type="button" className="hb-chapter-trigger" aria-labelledby={`${id}-label ${id}-value`} aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? `${id}-list` : undefined} disabled={disabled || !chapters.length} onClick={() => open ? close() : show()}>
      <span id={`${id}-value`}><small>{current ? `目录 ${value.toLocaleString()}` : '暂无目录'}</small><strong>{current?.title || '请先导入章节'}</strong></span><ChevronDown size={16}/>
    </button>
    {open && <div ref={popup} className="hb-chapter-popup">
      <div className="hb-chapter-search"><Search size={15}/><input ref={search} role="combobox" aria-label={`搜索${label}`} aria-autocomplete="list" aria-expanded="true" aria-controls={`${id}-list`} aria-activedescendant={matches[active] ? `${id}-option-${active}` : undefined} value={query} placeholder="搜索章节名或目录序号" autoComplete="off" onChange={event => {setQuery(event.target.value); setPage(0); setActive(0);}}/>{query && <button type="button" aria-label="清空章节搜索" onClick={() => {setQuery(''); setPage(0); setActive(0); search.current?.focus();}}><X size={14}/></button>}<button type="button" aria-label={`关闭${label}列表`} onClick={() => close(true)}><X size={16}/></button></div>
      <p className="hb-chapter-search-help">输入 #序号 可精确定位，如 #2606；序号不等于书中章号。</p>
      <div ref={list} id={`${id}-list`} className="hb-chapter-options" role="listbox" aria-label={label}>
        {shown.map(({chapter, ordinal}, index) => {
          const matchIndex = page * PAGE_SIZE + index;
          return <button type="button" role="option" tabIndex={-1} id={`${id}-option-${matchIndex}`} key={ordinal} aria-selected={ordinal === value} aria-posinset={matchIndex + 1} aria-setsize={matches.length} data-active={matchIndex === active} onPointerMove={() => setActive(matchIndex)} onClick={() => choose(ordinal)}><span><small>目录 {ordinal.toLocaleString()}</small><strong>{chapter.title}</strong></span><span>{ordinal === value ? <Check size={15}/> : <small>{chapter.characters.toLocaleString()} 字符</small>}</span></button>;
        })}
      </div>
      {!matches.length && <div className="hb-chapter-no-results" role="status"><Search size={22}/><strong>没有找到章节</strong><span>换个关键词，或输入 #加目录序号。</span></div>}
      <div className="hb-chapter-pagination"><span role="status">{matches.length ? `${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, matches.length)} / ${matches.length.toLocaleString()} 项` : '0 项匹配'}</span><button type="button" aria-label="上一页章节" disabled={page === 0} onClick={() => changePage(page - 1)}><ChevronLeft size={16}/></button><button type="button" aria-label="下一页章节" disabled={(page + 1) * PAGE_SIZE >= matches.length} onClick={() => changePage(page + 1)}><ChevronRight size={16}/></button></div>
    </div>}
  </div>;
}
