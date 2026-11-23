import { useEffect, useMemo, useState } from "react";
import { Snapshot } from "../cameras/Snapshot";

type Camera = { id: string; name: string; location: string; type: string; observation?: { frame_version?: string | null; frame_timestamp: number | null; source_status: string; is_fetching?: boolean; freshness_reason?: string | null } };
type ForecastHour = {
  timestamp: number;
  precipitation_probability: number | null;
  precipitation: keyof typeof precipitationNames | null;
  precipitation_mm: number | null;
  snowfall_cm: number | null;
  temperature_c: number | null;
};
type WarningItem = { text: string; city_mentioned: boolean; date_status: "past" | "today" | "upcoming" | "unknown"; last_date: string | null };
type CityOutlook = {
  observations: {
    snow_score: number | null;
    local_observation_score: number | null;
    independent_zones: number;
    eligible_cameras: number;
    status: string;
    snowfall_state?: string;
    calculated_at?: number;
    last_evidence_at?: number | null;
    evidence_count?: number;
    local_evidence?: { camera_id: string; name: string; location: string; positive: boolean; negative: boolean; conflict: boolean; stage: string | null; timestamp: number; method: string; model_only?: boolean }[];
    last_known?: { camera_id: string; name?: string; location?: string; stage?: string; timestamp?: number }[];
    contributors?: { id: string; name: string; location: string; score: number; contribution: number; timestamp: number; frame_timestamp?: number | null; age_seconds: number; frame_version: string; method: string; reason: string; positive: boolean }[];
    excluded_cameras?: { id: string; name?: string; reason: string; reason_label: string; frame_version?: string | null; analysis_at?: number | null }[];
  };
  weather?: { status: "available" | "stale" | "unavailable" | "disabled"; precipitation?: keyof typeof precipitationNames; temperature_c?: number | null; observed_at?: number };
  forecast: { status: "available" | "stale" | "unavailable" | "disabled"; fetched_at?: number; hours: ForecastHour[]; city?: string; location_note?: string; source?: string; source_url?: string };
  warnings?: {
    status: "available" | "stale" | "unavailable";
    fetched_at?: number;
    source: string;
    source_url: string;
    region?: string;
    items: WarningItem[];
  };
};

const precipitationNames = { none: "без осадков", rain: "дождь", sleet: "дождь со снегом", snow: "снег", hail: "град" };
const time = (value?: number | null) => value == null ? "—" : new Date(value * 1000).toLocaleTimeString("ru-RU", { timeZone: "Asia/Kamchatka", hour: "2-digit", minute: "2-digit" });
const fullDate = (value?: number | null) => value == null ? "—" : new Date(value * 1000).toLocaleString("ru-RU", { timeZone: "Asia/Kamchatka", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });

function WarningSources({ showBulletinLink = true }: { showBulletinLink?: boolean }) {
  return <details className="warning-sources"><summary>Официальные источники предупреждений</summary><ul>
    <li><a href="https://kammeteo.ru/" target="_blank" rel="noreferrer">Камчатское УГМС</a></li>
    {showBulletinLink && <li><a href="https://dfo.meteorf.gov.ru/ekstrennaya-informatsiya/" target="_blank" rel="noreferrer">Росгидромет · бюллетень по Дальнему Востоку</a></li>}
    <li><a href="https://meteoinfo.ru/hdmapsmeteoalarmdv" target="_blank" rel="noreferrer">Гидрометцентр России · карта предупреждений</a></li>
    <li><a href="https://41.mchs.gov.ru/deyatelnost/press-centr/operativnaya-informaciya/shtormovye-i-ekstrennye-preduprezhdeniya" target="_blank" rel="noreferrer">МЧС Камчатки · экстренные предупреждения</a></li>
  </ul></details>;
}

function WarningBulletin({ warnings, connectionError }: { warnings?: CityOutlook["warnings"]; connectionError: boolean }) {
  const current = warnings?.items.filter((item) => item.date_status !== "past") ?? [];
  const past = warnings?.items.filter((item) => item.date_status === "past") ?? [];
  const stale = connectionError || warnings?.status === "stale";
  const unavailable = !warnings || warnings.status === "unavailable";
  const first = current[0];
  let label = "Бюллетень недоступен";
  let tone = "warning-unknown";
  if (!unavailable && stale) { label = "Бюллетень устарел · проверяйте источник"; tone = "warning-stale"; }
  else if (!unavailable && !current.length) { label = "Нет актуальных сообщений в полученном бюллетене"; tone = "warning-clear"; }
  else if (first?.date_status === "unknown") { label = "Срок предупреждения не определён"; }
  else if (first?.date_status === "upcoming") { label = `Предупреждение ожидается · до ${first.last_date}`; tone = "warning-upcoming"; }
  else if (first) { label = `Предупреждение на сегодня · до ${first.last_date ?? "срок не указан"}`; tone = "warning-current"; }

  return <section className={`warning-strip ${tone}`} aria-label="Штормовое предупреждение">
    <div className="warning-icon" aria-hidden="true">!</div>
    <div className="warning-copy">
      <strong>{label}</strong>
      <small>{first ? `${first.city_mentioned ? "Петропавловск-Камчатский упомянут" : warnings?.region ?? "Камчатский край"} · ${warnings?.source}` : unavailable ? "Проверьте официальный бюллетень" : warnings?.region ?? "Камчатский край"}</small>
      {first && <p>{first.text}</p>}
    </div>
    {warnings?.source_url && <a className="warning-link" href={warnings.source_url} target="_blank" rel="noreferrer">Источник ↗</a>}
    <details className="warning-more">
      <summary>Подробнее</summary>
      {unavailable ? <p>Не удалось получить бюллетень. Данные не позволяют заключить, что предупреждений нет.</p> : <>
        {stale && <p role="status">Показана сохранённая запись. Проверьте свежесть в официальном источнике.</p>}
        {first && <p>{first.text}</p>}
        {current.length > 1 && <ul>{current.slice(1).map((item) => <li key={item.text}>{item.city_mentioned ? "Петропавловск-Камчатский" : warnings?.region}: {item.text} · {item.date_status === "unknown" ? "срок не определён" : item.last_date}</li>)}</ul>}
        {!!past.length && <details><summary>Прошедшие предупреждения ({past.length})</summary><ul>{past.map((item) => <li key={item.text}>{item.text}</li>)}</ul></details>}
        {first?.date_status === "upcoming" && <p>Дата в будущем; предупреждение ещё не обозначено как действующее.</p>}
      </>}
      <WarningSources showBulletinLink={!warnings} />
    </details>
  </section>;
}

function SnowfallSummary({ data, error, now }: { data?: CityOutlook; error: string; now: number }) {
  const observations = data?.observations;
  const expired = observations?.last_evidence_at != null && now - observations.last_evidence_at > 120;
  const state = error || expired ? "unknown" : observations?.snowfall_state ?? "unknown";
  const evidence = observations?.local_evidence ?? [];
  const positive = evidence.filter((item) => item.positive);
  const localConfirmed = positive.some((item) => item.stage === "confirmed");
  const cityCoverage = (observations?.independent_zones ?? 0) >= 2;
  const names = positive.map((item) => item.camera_id);
  const locations = [...new Set(positive.map((item) => item.location))];
  const lastKnown = observations?.last_known?.[0];
  const status = {
    local_positive: [localConfirmed ? "Повторно подтверждено локально" : "Первые признаки снегопада", cityCoverage ? "Есть свежие локальные признаки · городское повторное подтверждение не установлено" : "Локальное свидетельство · недостаточно независимых зон для городской оценки", "amber"],
    observed: ["Первые признаки снегопада", "Локальные сигналы требуют повторного подтверждения", "amber"],
    confirmed: ["Снегопад наблюдается", "Повторное подтверждение в нескольких независимых зонах", "blue"],
    not_detected: ["Снегопад не обнаружен", `На наблюдаемых участках · зон ${observations?.independent_zones ?? 0}`, "gray"],
    conflict: ["Методы расходятся", "Проверьте кадр · общий вывод не сделан", "amber"],
    unknown: ["Недостаточно свежих данных", error ? "Связь с сервисом потеряна · последнее состояние не считается текущим" : "Нет пригодных свежих свидетельств", "amber"],
  }[state] ?? ["Недостаточно свежих данных", "Нет пригодных свежих свидетельств", "amber"];
  const score = observations?.snow_score;
  return <section className="snowfall-card" aria-label="Текущий снегопад">
    <div className="snowfall-main">
      <div className="snowfall-title-row"><span className="eyebrow">СЕЙЧАС · ПЕТРОПАВЛОВСК-КАМЧАТСКИЙ</span><span className={`status-dot ${status[2]}`}>{status[0]}</span></div>
      <p className="snowfall-subtitle">{status[1]}</p>
      {(state === "local_positive" || state === "observed" || state === "confirmed") && <p className="snowfall-places">Место: {locations.join(", ") || "место наблюдения уточняется"} · {names.length} свидетельств</p>}
      {state === "local_positive" && positive.some((item) => item.stage === "early") && <p className="snowfall-places">Стадия сигнала: ранняя · требуется повторная проверка</p>}
      {state === "local_positive" && localConfirmed && <p className="snowfall-places">Стадия сигнала: confirmed в одной зоне · одна зона не подтверждает весь город</p>}
      {state === "unknown" && lastKnown && <p className="snowfall-places">Последнее известное: {lastKnown.name ?? lastKnown.camera_id} · {lastKnown.location ?? "городская камера"} · {fullDate(lastKnown.timestamp)} · сейчас свидетельство устарело</p>}
      {state === "conflict" && <p className="snowfall-places">Видеопризнаки и модельная оценка дают разные ответы. Откройте свидетельство и проверьте изображение.</p>}
      <div className="snowfall-meta">
        <span>Последнее пригодное свидетельство <strong>{fullDate(observations?.last_evidence_at)}</strong></span>
        <span>Покрытие <strong>{observations?.independent_zones ?? 0} независимых зон</strong> · нужно минимум 2 для городского балла</span>
        {score != null && <span>Индекс <strong>{Math.round(score * 100)}/100</strong> · не вероятность</span>}
      </div>
    </div>
    <div className="snowfall-stamp" aria-label="Актуальность данных">
      <small>{error ? "Последнее известное обновление" : "Расчёт"}</small>
      <strong>{time(observations?.calculated_at)}</strong>
      <small>{expired || error ? "данные неактуальны" : "Камчатское время · UTC+12"}</small>
      {error && <small className="connection-error">Связь с сервисом потеряна</small>}
    </div>
  </section>;
}

export function CityPanel({ cameras = [], onSelectCamera, auto = true, now = Date.now() / 1000 }: {
  cameras?: Camera[];
  onSelectCamera?: (id: string) => void;
  auto?: boolean;
  now?: number;
}) {
  const [data, setData] = useState<CityOutlook>();
  const [error, setError] = useState("");
  const [clock, setClock] = useState(now);
  useEffect(() => {
    let alive = true;
    let busy = false;
    const refresh = async () => {
      if (busy) return;
      busy = true;
      try {
        const response = await fetch("/api/city/outlook");
        if (!response.ok) throw Error("Не удалось получить городскую оценку");
        const incoming = await response.json() as CityOutlook;
        if (alive) {
          setData((previous) => (previous?.observations.calculated_at ?? 0) > (incoming.observations.calculated_at ?? 0) ? previous : incoming);
          setError("");
        }
      } catch (e) { if (alive) setError((e as Error).message); }
      finally { busy = false; }
    };
    refresh();
    const timer = setInterval(refresh, 5000);
    const tick = setInterval(() => setClock(Date.now() / 1000), 1000);
    return () => { alive = false; clearInterval(timer); clearInterval(tick); };
  }, []);

  const observations = data?.observations;
  const displayCandidates = useMemo(() => {
    if (error) return [];
    const calculationAge = Math.max(0, clock - (observations?.calculated_at ?? clock));
    const selected = (observations?.contributors ?? []).filter((item) => item.age_seconds + calculationAge <= 120).slice(0, 3).map((item) => ({ ...item, localOnly: false }));
    const ids = new Set(selected.map((item) => item.id));
    const local = (observations?.local_evidence ?? []).filter((item) => item.positive && item.model_only && !ids.has(item.camera_id) && clock - item.timestamp <= 120);
    for (const item of local) {
      if (selected.length >= 3) break;
      const camera = cameras.find((row) => row.id === item.camera_id);
      if (camera) selected.push({ id: item.camera_id, name: camera.name, location: camera.location, score: 0, contribution: 0, timestamp: item.timestamp, age_seconds: Math.max(0, clock - item.timestamp), frame_version: "", method: item.method, reason: "Локальный сигнал модели · не входит в формулу городского индекса", positive: true, localOnly: true });
    }
    return selected;
  }, [observations, cameras, clock, error]);
  const warning = data?.warnings;
  const hours = data?.forecast.hours.slice(0, 3) ?? [];

  return <div className="dispatcher-dashboard">
    <SnowfallSummary data={data} error={error} now={clock} />
    <section className="weather-card" aria-label="Погода и штормовое предупреждение">
      <div className="weather-heading"><div><span className="eyebrow">ПОГОДА · ОПОРНЫЙ РАЙОН ЧЕХОВА</span><h2>Погода и предупреждения</h2></div><a href={data?.forecast.source_url ?? "https://yandex.ru/pogoda/"} target="_blank" rel="noreferrer">{data?.forecast.source ?? "Яндекс Погода"} ↗</a></div>
      <div className="weather-fact">
        <div><small>Текущее наблюдение</small>
          {data?.weather?.status === "available" ? <strong>{error ? "Последнее полученное наблюдение · свежесть не подтверждена" : `${data.weather.temperature_c ?? "—"} °C · ${data.weather.precipitation ? precipitationNames[data.weather.precipitation] : "тип осадков неизвестен"}`}</strong> : <strong>{data?.weather?.status === "stale" ? "Последнее наблюдение устарело" : "Текущая погода недоступна"}</strong>}
          <span>{data?.weather?.observed_at ? `Яндекс Погода: ${data.weather.temperature_c ?? "—"} °C · ${data.weather.precipitation ? precipitationNames[data.weather.precipitation] : "тип осадков неизвестен"} · данные на ${fullDate(data.weather.observed_at)}` : "Почасовой прогноз не подменяет текущее наблюдение"}</span>
        </div>
        <div className="forecast-block"><small>Ближайшие часы · прогноз</small>
          {data?.forecast.status === "disabled" && <p>Ключ прогноза не настроен.</p>}
          {data?.forecast.status === "unavailable" && <p>Почасовой прогноз недоступен.</p>}
          {data?.forecast.status === "stale" && <p>Сохранённый прогноз устарел.</p>}
          {error && data?.forecast.status === "available" && <p>Не удалось проверить свежесть сохранённого прогноза.</p>}
          {hours.length > 0 && <div className="forecast-hours compact">{hours.map((hour) => <div key={hour.timestamp}><strong>{time(hour.timestamp)}</strong><span>{hour.temperature_c ?? "—"} °C · {hour.precipitation ? precipitationNames[hour.precipitation] : "тип осадков неизвестен"}</span>{hour.precipitation_probability != null && <small>Вероятность осадков {hour.precipitation_probability}%</small>}</div>)}</div>}
          <small className="forecast-fetched">Источник: Яндекс Погода · район Чехова · вероятность относится ко всем осадкам{data?.forecast.fetched_at ? ` · получен ${time(data.forecast.fetched_at)}` : ""}</small>
        </div>
      </div>
      <WarningBulletin warnings={warning} connectionError={!!error} />
    </section>
    <section className="priority-section" aria-label="Камеры, влияющие на оценку снегопада">
      <div className="priority-heading"><div><span className="eyebrow">СВИДЕТЕЛЬСТВА</span><h2>Камеры, влияющие на оценку</h2><p>Выбраны по положительному вкладу представителей независимых зон</p></div><span className="camera-count">{displayCandidates.length} из 3</span></div>
      {displayCandidates.length ? <div className="priority-grid">{displayCandidates.map((item) => {
        const camera = cameras.find((row) => row.id === item.id);
        if (!camera) return null;
        const age = Math.max(0, Math.floor(clock - item.timestamp));
        const pinnedCamera = { ...camera, observation: { ...camera.observation, source_status: camera.observation?.source_status ?? "unknown", frame_version: item.frame_version, frame_timestamp: item.frame_timestamp ?? item.timestamp } };
        return <article className="priority-camera" key={`${item.id}:${item.frame_version}`}>
          <Snapshot camera={pinnedCamera} auto={auto} now={clock} />
          <div className="priority-camera-body"><small>{item.location}</small><h3>{item.name}</h3>
            <div className="priority-evidence"><strong>{item.localOnly ? "Локальное свидетельство" : "Положительный вклад в индекс"}</strong><span>{item.localOnly ? "Без численного вклада" : `+${Math.round(item.contribution * 100)}/100 к индексу`}</span></div>
            <p>{item.reason}</p>
            <small>{item.method === "weather_vlm_v2" ? "Метод: Weather VLM" : item.method === "opencv_motion" ? "Метод: анализ видеоряда" : "Метод: оценка изображения"} · анализ {age < 60 ? `${age} сек назад` : `${Math.floor(age / 60)} мин назад`}</small>
            <button aria-label={`Открыть кадр и историю: ${item.name}`} onClick={() => onSelectCamera?.(item.id)}>Открыть кадр и историю</button>
          </div>
        </article>;
      })}</div> : <div className="no-priority"><strong>{error ? "Не удалось обновить свидетельства" : "Нет камер с текущим положительным вкладом"}</strong><p>{error ? "Последнее состояние сохранено; оно может быть устаревшим." : observations?.independent_zones ? "Наблюдаемые зоны не показывают пригодных положительных свидетельств." : "Недостаточно свежих пригодных данных из городских камер. Случайные и региональные кадры не подставляются."}</p></div>}
      <p className="coverage-note">{observations?.independent_zones ?? 0} независимых зон · {observations?.eligible_cameras ?? 0} представителей в индексе. Камеры одной площадки, демо, вулканы и одиночные JPEG в городскую формулу не входят. <button className="text-button" onClick={() => window.dispatchEvent(new CustomEvent("snowvision-show-cameras"))}>Все камеры</button></p>
      {!!observations?.excluded_cameras?.length && <details className="excluded-camera-details"><summary>Почему источники не участвуют в индексе ({observations.excluded_cameras.length})</summary><ul>{observations.excluded_cameras.map((camera) => <li key={camera.id}><strong>{camera.name ?? camera.id}</strong> · {camera.reason_label}</li>)}</ul></details>}
    </section>
  </div>;
}
