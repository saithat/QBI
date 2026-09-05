import { display, sourceURL, type CatalogRecord } from './api';

interface Props {
  records: CatalogRecord[];
  filtered: boolean;
  recordHref: (id: number) => string;
  onOpen: (id: number) => void;
}

export function RecordTable({ records, filtered, recordHref, onOpen }: Props) {
  if (!records.length) {
    return <div className="message"><p>{filtered
      ? 'No records match these filters. Try a broader search or reset the filters.'
      : 'No records have been published yet.'}</p></div>;
  }

  return (
    <div className="table-wrap" tabIndex={0} role="region"
      aria-label="Western blot records; scroll horizontally for all columns">
      <table>
        <caption className="sr-only">Western blot observations. Open a target to inspect the source record.</caption>
        <thead><tr>{['Target', 'Sample / organism', 'Condition', 'Band state', 'Lane / row', 'Paper / source'].map(label => (
          <th key={label} scope="col">{label}</th>
        ))}</tr></thead>
        <tbody>{records.map(record => {
          const source = sourceURL(record);
          return (
            <tr key={record.id} onClick={event => {
              if (event.target instanceof Element && !event.target.closest('a, button')) onOpen(record.id);
            }}>
              <td>
                <a className="record-link" href={recordHref(record.id)} aria-haspopup="dialog"
                  aria-label={`View ${record.target}, record ${record.id}`} onClick={event => {
                    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                    event.preventDefault();
                    onOpen(record.id);
                  }}>{display(record.target)}</a>
                {record.western_blot_type && <span className="secondary">{record.western_blot_type.replaceAll('_', ' ')}</span>}
              </td>
              <td>{display(record.sample)}{record.organism && <span className="secondary">{record.organism}</span>}</td>
              <td>{display(record.condition)}
                {record.treatment_context && record.treatment_context !== record.condition &&
                  <span className="secondary">{record.treatment_context}</span>}
              </td>
              <td><span className={`state ${record.band_state}`}>{record.band_state}</span></td>
              <td>Lane {display(record.lane_index)} · row {display(record.row_index)}
                {record.is_loading_control && <span className="secondary">Loading control</span>}
              </td>
              <td>
                {source ? <a href={source} target="_blank" rel="noopener noreferrer">{record.paper_id}</a> : record.paper_id}
                <span className="secondary">{[`p. ${record.page}`, record.figure_label, record.panel_label].filter(Boolean).join(' · ')}</span>
              </td>
            </tr>
          );
        })}</tbody>
      </table>
    </div>
  );
}
