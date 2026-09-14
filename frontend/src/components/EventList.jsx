import StatusBadge from "./StatusBadge";

export default function EventList({ events, onSelect, selectedEventId }) {
  if (events.length === 0) {
    return <p className="empty-state">No events found for this filter.</p>;
  }

  return (
    <table className="event-table">
      <thead>
        <tr>
          <th>Event ID</th>
          <th>Type</th>
          <th>Status</th>
          <th>Retries</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        {events.map((e) => (
          <tr
            key={e.event_id}
            className={e.event_id === selectedEventId ? "selected-row" : ""}
            onClick={() => onSelect(e.event_id)}
          >
            <td>{e.event_id}</td>
            <td>{e.event_type}</td>
            <td>
              <StatusBadge status={e.status} />
            </td>
            <td>
              {e.retry_count} / {e.max_retries}
            </td>
            <td>{new Date(e.created_at).toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
