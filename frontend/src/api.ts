export const filterNames = ['q', 'target', 'sample', 'condition', 'paper_id'] as const;
export type Filters = Record<(typeof filterNames)[number], string>;

export interface CatalogRecord {
  id: number;
  paper_id: string;
  source_id: string | null;
  page: number;
  figure_label: string | null;
  panel_label: string | null;
  row_index: number | null;
  lane_index: number | null;
  target: string;
  is_loading_control: boolean;
  western_blot_type: string;
  sample: string | null;
  organism: string | null;
  treatment_context: string | null;
  condition: string | null;
  band_state: 'present' | 'absent' | 'uncertain';
  confidence: number | null;
  source_url: string | null;
  doi: string | null;
  image_sha256: string | null;
  model_version: string | null;
  updated_at: string;
}

export interface RecordPage {
  results: CatalogRecord[];
  count: number;
  total: number;
  limit: number;
  offset: number;
}

export interface RecordDetails {
  record: CatalogRecord;
  image_url: string | null;
  figure_caption: string;
  paper_context: string;
  warnings: string[];
}

async function request<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, headers: { Accept: 'application/json' } });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : null;
    throw new Error(typeof detail === 'string' ? detail : `Could not load records (${response.status}).`);
  }
  if (!data) throw new Error('The server returned an unreadable response.');
  return data as T;
}

export async function fetchRecords(query: string, signal: AbortSignal): Promise<RecordPage> {
  const data = await request<RecordPage>(`/api/records?${query}`, signal);
  if (!Array.isArray(data.results) || !Number.isInteger(data.total) || data.total < 0) {
    throw new Error('The server returned an invalid record list.');
  }
  return data;
}

export async function fetchRecord(id: number, signal: AbortSignal): Promise<RecordDetails> {
  const data = await request<RecordDetails>(`/api/records/${id}`, signal);
  if (!data.record || !Number.isInteger(data.record.id) || !Array.isArray(data.warnings)) {
    throw new Error('The server returned an invalid source record.');
  }
  return data;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The request failed. Please try again.';
}

export function safeURL(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value, location.origin);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

export function sourceURL(record: CatalogRecord): string | null {
  const doi = record.doi?.split('/').map(encodeURIComponent).join('/');
  return safeURL(record.source_url) || (doi ? safeURL(`https://doi.org/${doi}`) : null);
}

export function display(value: string | number | null | undefined): string {
  return value === null || value === undefined || value === '' ? '—' : String(value);
}
