import StatusBadge from "./StatusBadge";

export default function EventDetail({ event, onRetry, retrying }) {
  if (!event) {
    return <p className="empty-state">Select an event to see details.</p>;
  }

  return (
    <div className="event-detail">
      <div className="event-detail-header">
        <h3>{event.event_id}</h3>
        <StatusBadge status={event.status} />
      </div>

      <dl className="event-detail-fields">
        <dt>Type</dt>
        <dd>{event.event_type}</dd>
        <dt>Retry Count</dt>
        <dd>
          {event.retry_count} / {event.max_retries}
        </dd>
        <dt>Last Attempt</dt>
        <dd>{event.last_attempt_at ? new Date(event.last_attempt_at).toLocaleString() : "—"}</dd>
        <dt>Next Retry</dt>
        <dd>{event.next_retry_at ? new Date(event.next_retry_at).toLocaleString() : "—"}</dd>
        <dt>Processed At</dt>
        <dd>{event.processed_at ? new Date(event.processed_at).toLocaleString() : "—"}</dd>
        <dt>Error</dt>
        <dd>{event.error_message || "—"}</dd>
      </dl>

      <h4>Payload</h4>
      <pre className="payload-block">{JSON.stringify(event.payload, null, 2)}</pre>

      <h4>Attempt History</h4>
      {event.attempts.length === 0 ? (
        <p className="empty-state">No attempts recorded yet.</p>
      ) : (
        <table className="attempt-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Status</th>
              <th>Response Code</th>
              <th>Error</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            {event.attempts.map((a) => (
              <tr key={a.attempt_number}>
                <td>{a.attempt_number}</td>
                <td>
                  <StatusBadge status={a.status} />
                </td>
                <td>{a.response_code ?? "—"}</td>
                <td>{a.error_message ?? "—"}</td>
                <td>{new Date(a.started_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {event.status === "DEAD" && (
        <button className="retry-button" onClick={onRetry} disabled={retrying}>
          {retrying ? "Retrying…" : "Manually Retry This Event"}
        </button>
      )}
    </div>
  );
}
