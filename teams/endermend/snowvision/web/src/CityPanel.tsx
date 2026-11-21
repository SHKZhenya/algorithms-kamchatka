import { useEffect, useState } from "react";
type ForecastHour = {
  timestamp: number;
  precipitation_probability: number | null;
  snowfall_cm: number | null;
  temperature_c: number | null;
  snow_expected: boolean | null;
};
type CityOutlook = {
  observations: {
    snow_score: number | null;
    local_observation_score: number | null;
    independent_zones: number;
    eligible_cameras: number;
    status: string;
  };
  forecast: {
    status: string;
    fetched_at?: number;
    hours: ForecastHour[];
    error?: string;
    note?: string;
    ensemble?: {
      status: string;
      cached?: boolean;
      fetched_at?: number;
      snow_probability?: number;
      member_count?: number;
      snowy_members?: number;
      model?: string;
      period_start?: number;
      period_end?: number;
    };
  };
};
const percent = (value: number | null) =>
  value === null ? "—" : `${Math.round(value * 100)}%`;
export function CityPanel() {
  const [data, setData] = useState<CityOutlook>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    async function refresh() {
      if (busy) return;
      busy = true;
      try {
        const response = await fetch("/api/city/outlook", {
          signal: controller.signal,
        });
        if (!response.ok) throw Error("Не удалось получить городскую оценку");
        setData(await response.json());
        setError("");
      } catch (e) {
        if ((e as Error).name !== "AbortError") setError((e as Error).message);
      } finally {
        busy = false;
      }
    }
    refresh();
    const timer = setInterval(refresh, 30000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);
  if (!data)
    return (
      <section className="city-panel" aria-label="Оценка снега в городе">
        <h2>Снег в Петропавловске</h2>
        <p>{error || "Собираем прогноз и наблюдения…"}</p>
      </section>
    );
  const fresh = data.forecast.status === "available" && !error;
  return (
    <section className="city-panel" aria-label="Оценка снега в городе">
      <div className="city-heading">
        <h2>Снег в Петропавловске</h2>
        <span>Три почасовых интервала · UTC+12</span>
      </div>
      <div className="city-assessment">
        <div>
          <small>Предварительная вероятность снега</small>
          <strong>
            {percent(
              fresh && data.forecast.ensemble?.status === "available"
                ? (data.forecast.ensemble.snow_probability ?? null)
                : null,
            )}
          </strong>
          <p>
            {fresh
              ? data.forecast.ensemble?.status === "available"
                ? `${data.forecast.ensemble.snowy_members} из ${data.forecast.ensemble.member_count} сценариев ICON EPS · район Чехова`
                : "Ансамблевая оценка временно недоступна"
              : "Свежий метеопрогноз недоступен"}
          </p>
          {data.forecast.ensemble?.cached && (
            <small>
              Сохранённый ансамбль ·{" "}
              {data.forecast.ensemble.fetched_at
                ? new Date(
                    data.forecast.ensemble.fetched_at * 1000,
                  ).toLocaleTimeString("ru-RU", { timeZone: "Asia/Kamchatka" })
                : "время в API"}
            </small>
          )}
        </div>
        <div>
          <small>Наблюдения камер сейчас</small>
          <strong>{percent(data.observations.snow_score)}</strong>
          <p>
            {data.observations.status === "available"
              ? "Взвешенный индекс наблюдений"
              : "Недостаточно независимых городских наблюдений"}
          </p>
          <small>
            Пригодных камер {data.observations.eligible_cameras} · зон{" "}
            {data.observations.independent_zones}
          </small>
        </div>
      </div>
      <div className="forecast-hours">
        {data.forecast.hours.map((hour) => (
          <div key={hour.timestamp}>
            <strong>
              {new Date(hour.timestamp * 1000).toLocaleTimeString("ru-RU", {
                timeZone: "Asia/Kamchatka",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </strong>
            <span>
              {hour.temperature_c ?? "—"} °C · снег {hour.snowfall_cm ?? "—"} см
            </span>
            <span>
              Осадки{" "}
              {hour.precipitation_probability === null
                ? "—"
                : hour.precipitation_probability + "%"}
            </span>
          </div>
        ))}
      </div>
      <p className="note">
        Вероятность — доля сценариев ансамбля со снегом ≥0,1 см за час в
        выбранных интервалах. Первый интервал включает текущий час. Оценка
        требует локальной проверки. Процент осадков включает дождь и снег.
        Камеры одной площадки учитываются как одна зона. Панорамы вулканов и
        демонстрационные сцены исключены из городской оценки.
      </p>
      {(error || data.forecast.status === "stale") && (
        <p role="status">
          {error ||
            "Показан сохранённый устаревший прогноз, общая оценка скрыта."}
        </p>
      )}
      <small>
        Прогноз{" "}
        <a href="https://open-meteo.com/" target="_blank" rel="noreferrer">
          Open-Meteo
        </a>
        {data.forecast.fetched_at &&
          ` · получен ${new Date(data.forecast.fetched_at * 1000).toLocaleString("ru-RU", { timeZone: "Asia/Kamchatka" })}`}
      </small>
    </section>
  );
}
