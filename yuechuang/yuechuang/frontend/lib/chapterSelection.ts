import type {IPChapter} from './ipApi';

export type ChapterOption = {ordinal: number; chapter: IPChapter};

function normalize(value: string): string {
  return value.normalize('NFKC').replace(/\s+/gu, '').toLowerCase();
}

/** Ordinals refer to the version's directory order, never numbers parsed from titles. */
export function searchChapters(chapters: IPChapter[], query: string): ChapterOption[] {
  const options = chapters.map((chapter, index) => ({ordinal: index + 1, chapter}));
  const term = normalize(query);
  if (!term) return options;

  const exactOrdinal = term.match(/^#(\d+)$/u);
  if (exactOrdinal) {
    const ordinal = Number(exactOrdinal[1]);
    return Number.isSafeInteger(ordinal) && ordinal >= 1 && ordinal <= options.length
      ? [options[ordinal - 1]] : [];
  }

  const matches = options.filter(option => normalize(option.chapter.title).includes(term));
  if (/^\d+$/u.test(term)) {
    const ordinal = Number(term);
    if (Number.isSafeInteger(ordinal) && ordinal >= 1 && ordinal <= options.length) {
      return [options[ordinal - 1], ...matches.filter(option => option.ordinal !== ordinal)];
    }
  }
  return matches;
}

export function isValidChapterRange(first: number, last: number, total: number): boolean {
  return Number.isSafeInteger(first) && Number.isSafeInteger(last) && Number.isSafeInteger(total)
    && first >= 1 && first <= last && last <= total;
}

/** A second click may precede the anchor; range endpoints remain inclusive ordinals. */
export function selectChapterEndpoint(anchor: number | null, ordinal: number, total: number): {first: number; last: number; anchor: number | null} | null {
  if (!isValidChapterRange(ordinal, ordinal, total)) return null;
  if (anchor === null || !isValidChapterRange(anchor, anchor, total)) return {first: ordinal, last: ordinal, anchor: ordinal};
  return {first: Math.min(anchor, ordinal), last: Math.max(anchor, ordinal), anchor: null};
}

export function virtualChapterWindow(total: number, scrollTop: number, height = 300, rowHeight = 60, overscan = 4): {start: number; end: number} {
  const count = Math.max(0, Math.floor(total));
  const firstVisible = Math.min(Math.max(0, count - 1), Math.floor(Math.max(0, scrollTop) / rowHeight));
  return {start: Math.max(0, firstVisible - overscan), end: Math.min(count, firstVisible + Math.ceil(height / rowHeight) + overscan)};
}
