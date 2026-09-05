import { useEffect, useRef, useState } from 'react';
import { display, errorMessage, fetchRecord, safeURL, sourceURL, type RecordDetails } from './api';

interface Props {
  recordId: number;
  permalink: string;
  onDismiss: () => void;
}

interface DetailState {
  id: number;
  data?: RecordDetails;
  error?: string;
}

export function RecordDetail({ recordId, permalink, onDismiss }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const [result, setResult] = useState<DetailState>({ id: recordId });
  const [retry, setRetry] = useState(0);
  const data = result.id === recordId ? result.data : undefined;
  const error = result.id === recordId ? result.error : undefined;
  const loading = !data && !error;

  useEffect(() => {
    const element = dialog.current;
    const opener = document.activeElement;
    element?.showModal();
    closeButton.current?.focus();
    return () => {
      if (element?.open) element.close();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setResult({ id: recordId });
    if (dialog.current) dialog.current.scrollTop = 0;
    fetchRecord(recordId, controller.signal).then(data => {
      if (!controller.signal.aborted) setResult({ id: recordId, data });
    }).catch(error => {
      if (!controller.signal.aborted) setResult({ id: recordId, error: errorMessage(error) });
    });
    return () => controller.abort();
  }, [recordId, retry]);

  return (
    <dialog id="record-dialog" ref={dialog} aria-labelledby="detail-title"
      onClose={event => { if (!event.currentTarget.open) onDismiss(); }}
      onClick={event => {
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.target === event.currentTarget && (event.clientX < bounds.left || event.clientX > bounds.right ||
          event.clientY < bounds.top || event.clientY > bounds.bottom)) event.currentTarget.close();
      }}>
      <header className="dialog-header">
        <div>
          <h2 id="detail-title">{data ? `${data.record.target} · record ${recordId}` : `Record ${recordId}`}</h2>
          <a id="record-permalink" href={permalink}>Link to this record</a>
        </div>
        <button id="close-dialog" type="button" ref={closeButton} onClick={() => dialog.current?.close()}>Close</button>
      </header>
      <div id="detail-content" aria-busy={loading}>
        {loading && <div className="message"><p>Loading source record…</p></div>}
        {error && <div className="message error" role="alert"><p>{error}</p>
          <button type="button" onClick={() => setRetry(value => value + 1)}>Try again</button></div>}
        {data && <SourceRecord key={data.record.id} data={data} />}
      </div>
    </dialog>
  );
}

function Section({ label, text }: { label: string; text: string | null }) {
  return <section className="detail-section"><h3>{label}</h3><p>{text || 'Not available for this record.'}</p></section>;
}

function SourceRecord({ data }: { data: RecordDetails }) {
  const record = data.record;
  const [imageFailed, setImageFailed] = useState(false);
  const imageURL = safeURL(data.image_url);
  const showImage = imageURL !== null && new URL(imageURL).origin === location.origin && !imageFailed;
  const source = sourceURL(record);
  const fields: [string, string | number | null][] = [
    ['Paper', record.paper_id], ['Page', record.page],
    ['Figure / panel', [record.figure_label, record.panel_label].filter(Boolean).join(' / ')],
    ['Target', record.target], ['Sample', record.sample], ['Organism', record.organism],
    ['Condition', record.condition], ['Band state', record.band_state],
    ['Lane / row', `${display(record.lane_index)} / ${display(record.row_index)}`],
    ['Loading control', record.is_loading_control ? 'Yes' : 'No'],
    ['Blot type', record.western_blot_type.replaceAll('_', ' ')],
    ['Model confidence', record.confidence === null ? null : `${Math.round(record.confidence * 100)}%`],
    ['Extraction model', record.model_version], ['Source image', record.source_id],
  ];

  return <div className="detail-grid">
    <div className="detail-media">
      {showImage ? <>
        <img src={imageURL} alt={`${record.figure_label || 'Western blot source'}${record.panel_label ? ', panel ' + record.panel_label : ''}, page ${record.page}`}
          onError={() => setImageFailed(true)} />
        <a href={imageURL} target="_blank" rel="noopener noreferrer">Open full image</a>
      </> : <div className="message"><p>Source image is unavailable.</p></div>}
    </div>
    <div className="detail-copy">
      <dl className="detail-meta">{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{display(value)}</dd></div>)}</dl>
      {source && <a href={source} target="_blank" rel="noopener noreferrer">Open source paper</a>}
      {data.warnings.length > 0 && <section className="detail-section warnings"><h3>Extraction warnings</h3>
        <ul>{data.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></section>}
      {record.treatment_context && <Section label="Treatment context" text={record.treatment_context} />}
      <Section label="Figure caption" text={data.figure_caption} />
      <Section label="Paper context" text={data.paper_context} />
      {record.image_sha256 && <section className="detail-section"><h3>Source image SHA-256</h3><p className="fingerprint">{record.image_sha256}</p></section>}
    </div>
  </div>;
}
