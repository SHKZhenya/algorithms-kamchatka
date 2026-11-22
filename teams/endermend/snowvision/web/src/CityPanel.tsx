import { useEffect, useState } from "react";
type ForecastHour = {
  timestamp: number;
  precipitation_probability: number | null;
  precipitation: keyof typeof precipitationNames | null;
  precipitation_mm: number | null;
  snowfall_cm: number | null;
  temperature_c: number | null;
};
type StormWarnings = {
  status: "available" | "stale" | "unavailable";
  source: string;
  source_url: string;
  items: {
    text: string;
    city_mentioned: boolean;
    date_status: "past" | "today" | "upcoming" | "unknown";
    last_date: string | null;
  }[];
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
    status: "available" | "stale" | "unavailable" | "disabled";
    fetched_at?: number;
    hours: ForecastHour[];
  };
  warnings?: StormWarnings;
};
const precipitationNames = {
  none: "Без осадков",
  rain: "Дождь",
  sleet: "Дождь со снегом",
  snow: "Снег",
  hail: "Град",
};
const percent = (value: number | null) =>
  value === null ? "—" : `${Math.round(value * 100)}%`;

function WarningBulletin({ warnings, connectionError }: {
  warnings?: StormWarnings;
  connectionError: boolean;
}) {
  const current = warnings?.items.filter((item) => item.date_status !== "past") ?? [];
  const past = warnings?.items.filter((item) => item.date_status === "past") ?? [];
  const stale = connectionError || warnings?.status === "stale";
  const unavailable = !warnings || warnings.status === "unavailable";
  return (
    <section aria-label="Штормовые предупреждения Камчатского края">
      <h3>Штормовые предупреждения Камчатского края</h3>
      {unavailable ? (
        <p>Получить бюллетень пока не удалось.</p>
      ) : (
        <>
          {stale && <p role="status">Показан сохранённый бюллетень.</p>}
          {!stale && !current.length && <p>В бюллетене нет сообщений на сегодня или позже.</p>}
          {!!current.length && (
            <ul>
              {current.map((item) => (
                <li key={item.text}>
                  <strong>{item.city_mentioned ? "Упомянут Петропавловск-Камчатский" : "Камчатский край"}</strong>
                  <p>{item.text}</p>
                  <small>{item.last_date ? "Точное время смотрите в бюллетене." : "Срок по тексту не определён."}</small>
                </li>
              ))}
            </ul>
          )}
          {!!past.length && (
            <details>
              <summary>Сообщения за прошедшие даты</summary>
              <ul>{past.map((item) => <li key={item.text}>{item.text}</li>)}</ul>
            </details>
          )}
        </>
      )}
      {warnings && <a href={warnings.source_url} target="_blank" rel="noreferrer">{warnings.source}</a>}
    </section>
  );
}

function WarningSources({ showBulletinLink = true }: { showBulletinLink?: boolean }) {
  return (
    <details className="note">
      <summary>Официальные штормовые предупреждения</summary>
      <ul>
        <li>
          <a href="https://kammeteo.ru/" target="_blank" rel="noreferrer">
            Камчатское УГМС
          </a>
        </li>
        {showBulletinLink && (
          <li>
            <a
              href="https://dfo.meteorf.gov.ru/ekstrennaya-informatsiya/"
              target="_blank"
              rel="noreferrer"
            >
              Росгидромет · бюллетень по Дальнему Востоку
            </a>
          </li>
        )}
        <li>
          <a
            href="https://meteoinfo.ru/hdmapsmeteoalarmdv"
            target="_blank"
            rel="noreferrer"
          >
            Гидрометцентр России · карта предупреждений
          </a>
        </li>
        <li>
          <a
            href="https://41.mchs.gov.ru/deyatelnost/press-centr/operativnaya-informaciya/shtormovye-i-ekstrennye-preduprezhdeniya"
            target="_blank"
            rel="noreferrer"
          >
            МЧС Камчатки · экстренные предупреждения
          </a>
        </li>
      </ul>
    </details>
  );
}

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
        <WarningSources />
      </section>
    );
  return (
    <section className="city-panel" aria-label="Оценка снега в городе">
      <div className="city-heading">
        <h2>Снег в Петропавловске</h2>
        <span>Три почасовых интервала · UTC+12</span>
      </div>
      <div className="city-assessment">
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
        <div>
          <small>Почасовой прогноз · район Чехова</small>
          <p>
            <a href="https://yandex.ru/pogoda/" target="_blank" rel="noreferrer">Яндекс Погода</a>
          </p>
          {data.forecast.status === "disabled" && <p>Ключ API Яндекс Погоды не настроен.</p>}
          {data.forecast.status === "unavailable" && <p>Почасовой прогноз пока недоступен.</p>}
          {data.forecast.fetched_at && (
            <small>Получен {new Date(data.forecast.fetched_at * 1000).toLocaleString("ru-RU", { timeZone: "Asia/Kamchatka" })}</small>
          )}
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
              {hour.temperature_c ?? "—"} °C · {hour.precipitation ? precipitationNames[hour.precipitation] : "Тип осадков неизвестен"}
            </span>
            <span>
              {hour.precipitation_mm != null && `Осадки ${hour.precipitation_mm} мм`}
              {hour.snowfall_cm != null && ` · свежий снег ${hour.snowfall_cm} см`}
            </span>
            {hour.precipitation_probability != null && (
              <span>Вероятность осадков {hour.precipitation_probability}%</span>
            )}
          </div>
        ))}
      </div>
      <p className="note">
        Камеры одной площадки учитываются как одна зона. Панорамы вулканов и
        демонстрационные сцены исключены из городской оценки.
      </p>
      {(error || data.forecast.status === "stale") && (
        <p role="status">
          {error ||
            (data.forecast.hours.length
              ? "Показан сохранённый устаревший прогноз."
              : "Срок сохранённого прогноза истёк.")}
        </p>
      )}
      <WarningBulletin warnings={data.warnings} connectionError={Boolean(error)} />
      <WarningSources showBulletinLink={!data.warnings} />
    </section>
  );
}
