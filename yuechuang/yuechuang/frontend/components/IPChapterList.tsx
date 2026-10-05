'use client';

import {useId, useMemo, useRef, useState} from 'react';
import {Check, Search, X} from 'lucide-react';
import type {IPChapter} from '@/lib/ipApi';
import {searchChapters, virtualChapterWindow} from '@/lib/chapterSelection';

const ROW_HEIGHT = 60;
const VIEWPORT = 300;

/** Fixed-height rows keep large novels bounded; focus stays on the search control. */
export default function IPChapterList({chapters, label = '章节目录', selected, range, onChoose, disabled = false}: {
  chapters: IPChapter[]; label?: string; selected?: number; range?: {first: number; last: number};
  onChoose: (ordinal: number) => void; disabled?: boolean;
}) {
  const id = useId();
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const [scrollTop, setScrollTop] = useState(0);
  const [active, setActive] = useState(0);
  const matches = useMemo(() => searchChapters(chapters, query), [chapters, query]);
  const window = virtualChapterWindow(matches.length, scrollTop, VIEWPORT, ROW_HEIGHT);
  const shown = matches.slice(window.start, window.end);
  const activeVisible = active >= window.start && active < window.end;
  function search(value: string) {setQuery(value); setActive(0); setScrollTop(0); if (list.current) list.current.scrollTop = 0;}
  function activate(index: number) {
    const next = Math.max(0, Math.min(matches.length - 1, index));
    setActive(next);
    const node = list.current;
    if (!node) return;
    const top = next * ROW_HEIGHT;
    if (top < node.scrollTop) node.scrollTop = top;
    else if (top + ROW_HEIGHT > node.scrollTop + VIEWPORT) node.scrollTop = top + ROW_HEIGHT - VIEWPORT;
    setScrollTop(node.scrollTop);
  }
  return <div className="ex-ip-chapter-list">
    <div className="ex-ip-chapter-search"><Search size={16}/><input ref={input} role="combobox" aria-label={`搜索${label}`} aria-autocomplete="list" aria-expanded="true" aria-controls={`${id}-list`} aria-describedby={`${id}-help`} aria-activedescendant={matches[active] && activeVisible ? `${id}-row-${active}` : undefined} disabled={disabled} autoComplete="off" value={query} placeholder="搜索章节名或 #目录序号" onChange={event => search(event.target.value)} onKeyDown={event => {
      if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) {if (event.key === 'Enter') event.preventDefault(); return;}
      if (event.key === 'Enter') {event.preventDefault(); if (matches[active]) onChoose(matches[active].ordinal);}
      else if (['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp'].includes(event.key)) {event.preventDefault(); activate(active + (event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : event.key === 'PageDown' ? 5 : -5));}
      else if ((event.ctrlKey || event.metaKey) && ['Home', 'End'].includes(event.key)) {event.preventDefault(); activate(event.key === 'Home' ? 0 : matches.length - 1);}
    }}/>{query && <button type="button" disabled={disabled} aria-label="清空章节搜索" onClick={() => {search(''); input.current?.focus();}}><X size={15}/></button>}</div>
    <p id={`${id}-help`} className="ex-ip-chapter-help">#12 定位目录第 12 项；前言也计入序号，与书中章号可能不同。方向键选择，回车确认。</p>
    <div ref={list} id={`${id}-list`} className="ex-ip-virtual-list" role="listbox" aria-label={label} aria-multiselectable={range ? true : undefined} aria-disabled={disabled} style={{height: VIEWPORT}} onScroll={event => setScrollTop(event.currentTarget.scrollTop)}>
      <div className="ex-ip-virtual-inner" style={{height: matches.length * ROW_HEIGHT}}>
        {shown.map(({chapter, ordinal}, offset) => {
          const index = window.start + offset;
          const chosen = range ? ordinal >= range.first && ordinal <= range.last : ordinal === selected;
          return <button type="button" id={`${id}-row-${index}`} key={ordinal} role="option" tabIndex={-1} disabled={disabled} aria-selected={chosen} aria-posinset={index + 1} aria-setsize={matches.length} className="ex-ip-chapter-row" data-active={active === index} data-endpoint={range && (ordinal === range.first || ordinal === range.last)} style={{height: ROW_HEIGHT, top: index * ROW_HEIGHT}} title={`目录 ${ordinal} · ${chapter.title}`} onPointerDown={event => {if (event.pointerType === 'mouse') {event.preventDefault(); input.current?.focus({preventScroll: true});}}} onClick={() => {setActive(index); onChoose(ordinal);}}>
            <span className="ex-ip-chapter-ordinal">{ordinal}</span><span className="ex-ip-chapter-name"><strong>{chapter.title}</strong><small>{chapter.characters.toLocaleString()} 字符</small></span>{chosen && <Check size={15} aria-hidden="true"/>}
          </button>;
        })}
      </div>
      {!matches.length && <p className="ex-ip-chapter-empty" role="status">{chapters.length ? '没有找到章节，试试标题关键词或 #序号。' : '此版本还没有章节。'}</p>}
    </div>
    <p className="ex-ip-chapter-count" role="status">{query ? `${matches.length.toLocaleString()} 项匹配 · ` : ''}共 {chapters.length.toLocaleString()} 项目录</p>
  </div>;
}
