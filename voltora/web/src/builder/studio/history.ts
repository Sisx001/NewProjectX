/** Bounded undo/redo history with coalescing of rapid edits to the same field. */
export type History<T> = { past: T[]; present: T; future: T[]; lastKey: string | null; lastAt: number };

export const HISTORY_LIMIT = 100;
export const COALESCE_MS = 800;

export function initHistory<T>(present: T): History<T> {
  return { past: [], present, future: [], lastKey: null, lastAt: 0 };
}

export function push<T>(h: History<T>, next: T, key: string | null = null, now = Date.now()): History<T> {
  if (next === h.present) return h;
  if (key && key === h.lastKey && now - h.lastAt < COALESCE_MS) {
    return { ...h, present: next, future: [], lastAt: now };
  }
  const past = [...h.past, h.present];
  if (past.length > HISTORY_LIMIT) past.splice(0, past.length - HISTORY_LIMIT);
  return { past, present: next, future: [], lastKey: key, lastAt: now };
}

export function undo<T>(h: History<T>): History<T> {
  if (!h.past.length) return h;
  const prev = h.past[h.past.length - 1]!;
  return { past: h.past.slice(0, -1), present: prev, future: [h.present, ...h.future], lastKey: null, lastAt: 0 };
}

export function redo<T>(h: History<T>): History<T> {
  if (!h.future.length) return h;
  const [next, ...rest] = h.future;
  return { past: [...h.past, h.present], present: next!, future: rest, lastKey: null, lastAt: 0 };
}
