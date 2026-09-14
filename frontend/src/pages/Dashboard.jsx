import { useEffect, useState, useCallback } from "react";
import MetricsSummary from "../components/MetricsSummary";
import EventList from "../components/EventList";
import EventDetail from "../components/EventDetail";
import { getMetricsSummary, listWebhooks, getWebhookDetail, retryWebhook } from "../services/api";

const STATUS_FILTERS = ["ALL", "PENDING", "PROCESSING", "SUCCESS", "FAILED", "DEAD"];
const POLL_INTERVAL_MS = 4000;

export default function Dashboard() {
  const [metrics, setMetrics] = useState(null);
  const [events, setEvents] = useState([]);
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [selectedEventId, setSelectedEventId] = useState(null);
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [metricsData, eventsData] = await Promise.all([
        getMetricsSummary(),
        listWebhooks({ status: statusFilter === "ALL" ? undefined : statusFilter, pageSize: 50 }),
      ]);
      setMetrics(metricsData);
      setEvents(eventsData.items);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [statusFilter]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [refresh]);

  useEffect(() => {
    if (!selectedEventId) {
      setSelectedEvent(null);
      return;
    }
    getWebhookDetail(selectedEventId).then(setSelectedEvent).catch((e) => setError(e.message));
  }, [selectedEventId, events]);

  const handleRetry = async () => {
    if (!selectedEventId) return;
    setRetrying(true);
    try {
      await retryWebhook(selectedEventId);
      await refresh();
      const detail = await getWebhookDetail(selectedEventId);
      setSelectedEvent(detail);
    } catch (e) {
      setError(e.message);
    } finally {
      setRetrying(false);
    }
  };

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>Webhook Reliability Dashboard</h1>
        {error && <div className="error-banner">{error}</div>}
      </header>

      <MetricsSummary metrics={metrics} />

      <div className="filter-bar">
        {STATUS_FILTERS.map((s) => (
          <button
            key={s}
            className={`filter-chip ${statusFilter === s ? "active" : ""}`}
            onClick={() => setStatusFilter(s)}
          >
            {s}
          </button>
        ))}
      </div>

      <div className="dashboard-body">
        <div className="dashboard-list-panel">
          <EventList events={events} onSelect={setSelectedEventId} selectedEventId={selectedEventId} />
        </div>
        <div className="dashboard-detail-panel">
          <EventDetail event={selectedEvent} onRetry={handleRetry} retrying={retrying} />
        </div>
      </div>
    </div>
  );
}
