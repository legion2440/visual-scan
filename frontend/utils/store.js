/**
 * store.js — localStorage persistence, sorting, and filtering for the results
 * table. Step 1 deliberately keeps the archive browser-local.
 */

const KEY = 'visual-scan.scans';

export const store = {
  all() {
    try {
      const items = JSON.parse(localStorage.getItem(KEY) || '[]');
      return Array.isArray(items) ? items : [];
    } catch {
      return [];
    }
  },
  replaceAll(items) {
    localStorage.setItem(KEY, JSON.stringify(items));
    return items;
  },
  add(scan) {
    const items = [scan, ...store.all().filter((s) => s.id !== scan.id)];
    return store.replaceAll(items);
  },
  remove(id) {
    return store.replaceAll(store.all().filter((s) => s.id !== id));
  },
  clear() {
    localStorage.removeItem(KEY);
    return [];
  },
};

export function newId() {
  return `scan_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

export function snippet(text, n = 140) {
  const flat = (text || '').replace(/\s+/g, ' ').trim();
  return flat.length > n ? `${flat.slice(0, n - 1)}…` : flat;
}

export function formatDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString(undefined, {
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

/** Filter by free text + classification, then sort by column. */
export function view(items, { query = '', classification = 'all', sortKey = 'scanned_at', sortDir = -1 } = {}) {
  const q = query.trim().toLowerCase();
  const filtered = items.filter((s) => {
    if (classification !== 'all' && (s.classification || 'unclassified') !== classification) return false;
    if (!q) return true;
    return [s.filename, s.text, s.summary, s.classification, (s.tags || []).join(' ')]
      .join(' ')
      .toLowerCase()
      .includes(q);
  });
  const val = (s) => {
    if (sortKey === 'scanned_at') return new Date(s.scanned_at).getTime() || 0;
    if (sortKey === 'confidence') return s.confidence || 0;
    return String(s[sortKey] || '').toLowerCase();
  };
  return filtered.sort((a, b) => {
    const av = val(a), bv = val(b);
    if (av === bv) return 0;
    return (av > bv ? 1 : -1) * sortDir;
  });
}

export function classifications(items) {
  return Array.from(new Set(items.map((s) => s.classification || 'unclassified'))).sort();
}
