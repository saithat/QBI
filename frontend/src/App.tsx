import { useCallback, useEffect, useState } from 'react';
import { errorMessage, fetchRecords, filterNames, type Filters, type RecordPage } from './api';
import { RecordDetail } from './RecordDetail';
import { RecordTable } from './RecordTable';

interface LocationState {
  filters: Filters;
  limit: number;
  offset: number;
  record: number | null;
}

interface PageState {
  query: string;
  data?: RecordPage;
  error?: string;
}

function emptyFilters(): Filters {
  return { q: '', target: '', sample: '', condition: '', paper_id: '' };
}

function readLocation(): LocationState {
  const params = new URLSearchParams(location.search);
  const number = (key: string, fallback: number): number => {
    const value = params.get(key) || '';
    return /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) ? Number(value) : fallback;
  };
  const filters = emptyFilters();
  filterNames.forEach(name => { filters[name] = (params.get(name) || '').trim(); });
  const limit = number('limit', 50);
  return { filters, limit: [25, 50, 100, 200].includes(limit) ? limit : 50, offset: number('offset', 0), record: number('record', 0) || null };
}

function filterParams(filters: Filters): URLSearchParams {
  const params = new URLSearchParams();
  filterNames.forEach(name => { if (filters[name]) params.set(name, filters[name]); });
  return params;
}

function pageURL(state: LocationState): string {
  const params = filterParams(state.filters);
  if (state.limit !== 50) params.set('limit', String(state.limit));
  if (state.offset) params.set('offset', String(state.offset));
  if (state.record) params.set('record', String(state.record));
  return `${location.pathname}${params.size ? '?' + params : ''}`;
}

const filterFields: { name: Exclude<keyof Filters, 'q'>; label: string; placeholder: string; maxLength: number }[] = [
  { name: 'target', label: 'Target', placeholder: 'Protein or target', maxLength: 200 },
  { name: 'sample', label: 'Sample or organism', placeholder: 'Cell line, tissue, or species', maxLength: 200 },
  { name: 'condition', label: 'Condition or treatment', placeholder: 'Treatment, dose, or condition', maxLength: 200 },
  { name: 'paper_id', label: 'Paper', placeholder: 'DOI or paper identifier', maxLength: 500 },
];

export function App() {
  const [state, setState] = useState<LocationState>(readLocation);
  const [draft, setDraft] = useState<Filters>(state.filters);
  const [result, setResult] = useState<PageState>({ query: '' });
  const [retry, setRetry] = useState(0);
  const params = filterParams(state.filters);
  params.set('limit', String(state.limit));
  params.set('offset', String(state.offset));
  const query = params.toString();
  const data = result.query === query ? result.data : undefined;
  const error = result.query === query ? result.error : undefined;
  const loading = !data && !error;

  const navigate = useCallback((next: LocationState, replace = false) => {
    const url = pageURL(next);
    if (url !== location.pathname + location.search) {
      history[replace ? 'replaceState' : 'pushState']({}, '', url);
    }
    setState(next);
  }, []);

  useEffect(() => {
    const onPopState = () => setState(readLocation());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  useEffect(() => { setDraft(state.filters); }, [state.filters]);

  useEffect(() => {
    const controller = new AbortController();
    setResult({ query });
    fetchRecords(query, controller.signal).then(data => {
      if (!controller.signal.aborted) setResult({ query, data });
    }).catch(error => {
      if (!controller.signal.aborted) setResult({ query, error: errorMessage(error) });
    });
    return () => controller.abort();
  }, [query, retry]);

  useEffect(() => {
    if (data && data.total > 0 && state.offset >= data.total) {
      navigate({ ...state, offset: Math.floor((data.total - 1) / state.limit) * state.limit }, true);
    }
  }, [data, state, navigate]);

  function applyFilters(reset = false) {
    const filters = emptyFilters();
    if (!reset) filterNames.forEach(name => { filters[name] = draft[name].trim(); });
    setDraft(filters);
    setRetry(value => value + 1);
    navigate({ ...state, filters, offset: 0, record: null });
  }

  const start = data?.results.length ? state.offset + 1 : 0;
  const end = state.offset + (data?.results.length || 0);
  const summary = loading ? 'Loading records…' : error ? 'Records could not be loaded' : data?.total
    ? `${start.toLocaleString()}–${end.toLocaleString()} of ${data.total.toLocaleString()} records` : '0 records';

  return <>
    <main>
      <header className="site-header"><a className="brand" href="/">HiveBlot</a><span className="subtle">Public western blot database</span></header>
      <h1>Western blot records</h1>
      <p className="intro">Browse extracted observations and inspect the source images and paper context.</p>
      <form id="filters" aria-label="Filter records" onSubmit={event => { event.preventDefault(); applyFilters(); }}>
        <div className="search-row">
          <label htmlFor="q">Search records
            <input id="q" name="q" type="search" maxLength={1000} placeholder="Protein, sample, treatment, or paper"
              value={draft.q} onChange={event => setDraft({ ...draft, q: event.target.value })} />
          </label>
          <button className="primary" type="submit">Search</button>
          <button id="reset" type="button" onClick={() => applyFilters(true)}>Reset filters</button>
        </div>
        <div className="filters">{filterFields.map(field => <label key={field.name} htmlFor={field.name}>{field.label}
          <input id={field.name} name={field.name} maxLength={field.maxLength} placeholder={field.placeholder}
            value={draft[field.name]} onChange={event => setDraft({ ...draft, [field.name]: event.target.value })} />
        </label>)}</div>
      </form>
      <div className="toolbar">
        <p id="summary" role="status" aria-live="polite">{summary}</p>
        <label className="page-size" htmlFor="limit">Rows per page
          <select id="limit" value={state.limit} onChange={event => navigate({ ...state, limit: Number(event.target.value), offset: 0 })}>
            {[25, 50, 100, 200].map(size => <option key={size} value={size}>{size}</option>)}
          </select>
        </label>
      </div>
      <div id="content" aria-busy={loading}>
        {loading && <div className="message"><p>Loading records…</p></div>}
        {error && <div className="message error" role="alert"><p>{error}</p>
          <button type="button" onClick={() => setRetry(value => value + 1)}>Try again</button></div>}
        {data && <RecordTable records={data.results} filtered={Object.values(state.filters).some(Boolean)}
          recordHref={id => pageURL({ ...state, record: id })} onOpen={id => navigate({ ...state, record: id })} />}
      </div>
      <div className="pagination">
        <span id="page" className="subtle">{data && data.total > 0 ? `Page ${Math.floor(state.offset / state.limit) + 1} of ${Math.ceil(data.total / state.limit)}` : ''}</span>
        <nav aria-label="Record pages">
          <button id="previous" type="button" disabled={!data || state.offset === 0}
            onClick={() => navigate({ ...state, offset: Math.max(0, state.offset - state.limit) })}>Previous</button>
          <button id="next" type="button" disabled={!data || state.offset + state.limit >= data.total}
            onClick={() => navigate({ ...state, offset: state.offset + state.limit })}>Next</button>
        </nav>
      </div>
      <footer>Each record describes an extracted observation. Open a record to inspect its source and extraction details.</footer>
    </main>
    {state.record !== null && <RecordDetail recordId={state.record} permalink={pageURL(state)}
      onDismiss={() => navigate({ ...state, record: null })} />}
  </>;
}
