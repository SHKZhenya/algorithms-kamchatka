import { useEffect, useRef, useState } from "react";
type WeatherContext = {
  status: string;
  precipitation?: "none" | "rain" | "sleet" | "snow" | "hail";
  temperature_c?: number | null;
  observed_at?: number;
};
const precipitationLabels = {
  none: "без осадков",
  rain: "дождь",
  sleet: "дождь со снегом",
  snow: "снег",
  hail: "град",
};
type Alert = {
  id: string;
  camera_id: string;
  name: string;
  location: string;
  stage: string;
  opened_at: number;
  last_seen: number;
  acknowledged_at: number | null;
  is_demo: boolean;
  evidence_stale: boolean;
  confidence: number | null;
  score: number | null;
  ended_at: number | null;
  confirmed_at?: number;
  conflict?: boolean;
  method?: string;
  weather_context?: WeatherContext;
  delivery?: {stage:string; status:string; attempts:number}[];
};
export function AlertPanel() {
  const [rows, setRows] = useState<Alert[]>([]);
  const [coverage, setCoverage] = useState<{
    onset_capable: number;
    onset_online: number;
    real_cameras: number;
    webhook_configured?: boolean;
    delivery_cameras?: number;
    delivery_cameras_ready?: number;
    yandex_weather?: WeatherContext;
  } | null>(null);
  const [error, setError] = useState("");
  const [demo, setDemo] = useState(false);
  const [notify, setNotify] = useState(false);
  const mountedAt = useRef(Date.now() / 1000);
  const seen = useRef(new Set<string>());
  const primed = useRef(false);
  const known = useRef(new Map<string, Alert>());
  function reconcile(incoming: Alert[]) {
    for (const row of incoming) {
      const previous = known.current.get(row.id);
      const latest = previous && previous.last_seen > row.last_seen ? previous : row;
      const ended = previous?.ended_at || row.ended_at;
      known.current.set(row.id, {
        ...latest,
        acknowledged_at: previous?.acknowledged_at || row.acknowledged_at,
        ended_at: ended,
        stage: ended ? "ended" : latest.stage,
        evidence_stale: Date.now() / 1000 - latest.last_seen > 120,
      });
    }
    const merged = [...known.current.values()]
      .sort((a, b) => b.opened_at - a.opened_at).slice(0, 100);
    known.current = new Map(merged.map(row => [row.id, row]));
    setRows(merged);
    return merged;
  }
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let pending = false;
    const accept = (incoming: Alert[]) => {
      if (!active) return;
      for (const alert of reconcile(incoming)) {
        const key = alert.id + ":" + alert.stage;
        if (
          primed.current &&
          !seen.current.has(key) &&
          !alert.is_demo &&
          !alert.ended_at &&
          !alert.evidence_stale &&
          Math.max(alert.opened_at, alert.confirmed_at || 0) >= mountedAt.current &&
          notify &&
          typeof Notification !== "undefined" &&
          Notification.permission === "granted"
        ) {
          try { new Notification(
            alert.stage === "confirmed"
              ? "Снегопад подтверждается"
              : "Первые признаки снегопада",
            { body: alert.name + " · проверьте кадр", tag: alert.id },
          ); } catch { /* The durable journal remains available if delivery fails. */ }
        }
        seen.current.add(key);
      }
      primed.current = true;
      setError("");
    };
    const poll = async () => {
      if (pending) return;
      pending = true;
      try {
        const response = await fetch("/api/alerts", {
          signal: controller.signal,
        });
        if (!response.ok) throw Error("Журнал временно недоступен");
        accept(await response.json());
        const coverageResponse = await fetch("/api/monitoring", {
          signal: controller.signal,
        });
        if (coverageResponse.ok && active)
          setCoverage(await coverageResponse.json());
      } catch (e) {
        if (active && (e as Error).name !== "AbortError")
          setError((e as Error).message);
      } finally {
        pending = false;
      }
    };
    const listen = (event: Event) =>
      accept((event as CustomEvent<Alert[]>).detail);
    window.addEventListener("snowvision-alerts", listen);
    poll();
    const timer = setInterval(poll, 10000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(timer);
      window.removeEventListener("snowvision-alerts", listen);
    };
  }, [notify]);
  async function enable() {
    if (typeof Notification === "undefined") {
      setError("Браузер не поддерживает системные уведомления");
      return;
    }
    let permission: NotificationPermission;
    try { permission = await Notification.requestPermission(); }
    catch { setError("Не удалось включить уведомления. Сигналы доступны в журнале."); return; }
    setNotify(permission === "granted");
    if (permission !== "granted")
      setError(
        "Разрешение на уведомления не получено. Сигналы доступны в журнале.",
      );
  }
  async function ack(id: string) {
    setBusy(id);
    try {
      const response = await fetch(`/api/alerts/${id}/acknowledge`, {
        method: "POST",
      });
      if (!response.ok) throw Error("Не удалось принять сигнал");
      const updated = await response.json();
      reconcile([updated]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  const live = rows.filter((row) => (demo || !row.is_demo) && !row.ended_at);
  const fresh = live.filter(
    (row) => !row.evidence_stale && !row.acknowledged_at,
  );
  const weather = coverage?.yandex_weather;
  return (
    <section className="alert-panel" aria-label="Оперативные сигналы">
      <div className="alert-title">
        <div>
          <p className="eyebrow">ЖУРНАЛ СИГНАЛОВ</p>
          <h2>
            {fresh.length
              ? `${fresh.length} непринятых сигналов`
              : "Непринятых сигналов нет"}
          </h2>
        </div>
        <button onClick={enable} disabled={notify}>
          {notify ? "Уведомления включены" : "Включить уведомления"}
        </button>
      </div>
      <details className="alert-journal">
      <summary>Открыть журнал сигналов и принятие</summary>
      {coverage && (
        <div className="coverage-banner">
          <strong>
            {coverage.onset_online} из {coverage.real_cameras} камер дают
            пригодные наблюдения для раннего обнаружения
          </strong>
          {!coverage.onset_online && (
            <p>Срочное обнаружение по всему городу пока не обеспечено. Проверяйте доступные снимки.</p>
          )}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {!live.length ? (
        <div className="alert-empty">
          <strong>Активных сигналов в журнале нет</strong>
          <p>Камеры и прогноз — ниже.</p>
        </div>
      ) : (
        <div className="alert-list">
          {live.map((row) => (
            <div
              key={row.id}
              className={`alert-event ${row.evidence_stale ? "expired" : row.stage}`}
            >
              <div>
                <span className="alert-level">
                  {row.is_demo ? "ТРЕНИРОВКА · " : ""}
                  {row.evidence_stale
                    ? "Требуется свежий кадр"
                    : row.stage === "confirmed"
                      ? "Повторно подтверждено"
                      : "Первые признаки · нужна проверка"}
                </span>
                <h3>{row.name}</h3>
                <p>
                  Первый сигнал{" "}
                  {new Date(row.opened_at * 1000).toLocaleTimeString("ru-RU", {
                    timeZone: "Asia/Kamchatka",
                  })}{" "}
                  · последнее свидетельство{" "}
                  {new Date(row.last_seen * 1000).toLocaleTimeString("ru-RU", {
                    timeZone: "Asia/Kamchatka",
                  })}
                </p>
                <small>
                  Индекс движения{" "}
                  {row.score === null
                    ? "модельная классификация"
                    : Math.round(row.score * 100) + "/100"}{" "}
                  · качество оценки{" "}
                  {row.confidence === null
                    ? "—"
                    : Math.round(row.confidence * 100) + "%"}
                </small>
              </div>
              {row.delivery?.some(item => item.status === "failed") && <p role="alert">Серверная доставка не удалась. Проверьте канал получателя.</p>}
              {row.conflict && (
                <p className="conflict-note">
                  Методы расходятся. Проверьте кадр перед решением.
                </p>
              )}
              {!row.evidence_stale && row.weather_context?.precipitation && (
                <p className="note">
                  {row.weather_context.precipitation === "snow"
                    ? "В районе Чехова Яндекс также показывает снег."
                    : row.weather_context.precipitation === "sleet"
                      ? "В районе Чехова Яндекс показывает смешанные осадки. Проверьте их тип по кадру."
                      : `В районе Чехова Яндекс показывает: ${precipitationLabels[row.weather_context.precipitation]}. Проверьте признаки снега по кадру.`}
                </p>
              )}
              <div className="alert-actions">
                <a
                  href={`/api/cameras/${row.camera_id}/snapshot`}
                  target="_blank"
                  rel="noreferrer"
                >
                  Открыть текущий кадр
                </a>
                <button
                  disabled={!!row.acknowledged_at || busy === row.id}
                  onClick={() => ack(row.id)}
                >
                  {row.acknowledged_at
                    ? "Принято оператором"
                    : busy === row.id
                      ? "Сохраняем…"
                      : "Принять сигнал"}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="alert-secondary">
        <details className="alert-settings">
          <summary>Как работают сигналы и доставка</summary>
          <p className="note">
            {coverage?.webhook_configured
              ? `Серверный канал настроен. Готовы к отправке ${coverage.delivery_cameras_ready ?? 0} из ${coverage.delivery_cameras ?? 0} разрешённых городских камер.`
              : "Серверный канал доставки не настроен. Браузерные уведомления требуют открытой вкладки."}
          </p>
          {weather && weather.status !== "disabled" && (
            <p className="note">
              <a href="https://yandex.ru/pogoda/" target="_blank" rel="noreferrer">Яндекс Погода</a>
              {" · район Чехова · "}
              {weather.status === "available" && weather.precipitation && weather.observed_at
                ? `${precipitationLabels[weather.precipitation]}${weather.temperature_c == null ? "" : ` · ${weather.temperature_c} °C`} · ${new Date(weather.observed_at * 1000).toLocaleTimeString("ru-RU", { timeZone: "Asia/Kamchatka", hour: "2-digit", minute: "2-digit" })}`
                : "свежих данных об осадках нет"}
            </p>
          )}
          <div className="alert-guide">
            <span>
              <b>1</b> Первые признаки
            </span>
            <span>
              <b>2</b> Повторная проверка
            </span>
            <span>
              <b>3</b> Подтверждение
            </span>
          </div>
          <p className="note">
            Ранний сигнал появляется при первом пригодном положительном наблюдении.
            Подтверждение требует трёх различных наблюдений. Открытая вкладка
            получает сигналы автоматически.
          </p>
          <label className="demo-toggle">
            <input
              type="checkbox"
              checked={demo}
              onChange={(e) => setDemo(e.target.checked)}
            />
            Показать тренировочные сигналы
          </label>
        </details>
        <details className="alert-history">
          <summary>История завершённых эпизодов</summary>
          {rows
            .filter((row) => (demo || !row.is_demo) && row.ended_at)
            .slice(0, 10)
            .map((row) => (
              <p key={row.id}>
                {row.name} · завершён{" "}
                {new Date(row.ended_at! * 1000).toLocaleString("ru-RU", {
                  timeZone: "Asia/Kamchatka",
                })}
              </p>
            ))}
        </details>
      </div>
      </details>
    </section>
  );
}
