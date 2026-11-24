import React, { memo, useCallback, useEffect, useRef, useState } from "react";
import { Feedback } from "../features/cameras/Feedback";
import { Snapshot } from "../features/cameras/Snapshot";
import { CityPanel } from "../features/city/CityPanel";
import { AlertPanel } from "../features/alerts/AlertPanel";
type Observation = {
  camera_id: string;
  snow_probability: number | null;
  confidence: number;
  status: string;
  timestamp: number;
  frame_timestamp: number | null;
  source_status: string;
  source_timestamp?: number | null;
  is_demo: boolean;
  error: string | null;
  analysis_method: string;
  analysis_reason?: string | null;
  features?: Record<string, number>;
  frame_version?: string | null;
  scene_frame_version?: string | null;
  scene?: {
    format_valid: boolean;
    snow_probability?: number;
    snowing_probability?: number;
    ice_confidence?: number;
    image_confidence?: number;
    ground_view?: "good" | "limited" | "unusable";
    air_view?: "good" | "limited" | "unusable";
    snowfall_intensity?: "none" | "light" | "moderate" | "heavy" | "uncertain";
    road_coverage?: "clear" | "partial" | "mostly_covered" | "not_visible" | "uncertain";
    possible_icing?: "yes" | "no" | "uncertain";
    road_view?: "good" | "limited" | "unusable";
    visual_evidence?: string;
    snow_on_ground?: "yes" | "no" | "uncertain";
  } | null;
  revision?: number;
  last_analysis_at?: number | null;
  last_success_at?: number | null;
  last_attempt_at?: number | null;
  next_attempt_at?: number;
  is_fetching?: boolean;
  is_stale?: boolean;
  consecutive_failures?: number;
};
type Camera = {
  id: string;
  name: string;
  location: string;
  type: string;
  url?: string;
  motion_analysis?: boolean;
  interval_seconds?: number;
  observation?: Observation;
};
const time = (value?: number | null) =>
  value
    ? new Date(value * 1000).toLocaleTimeString("ru-RU", {
        timeZone: "Asia/Kamchatka",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "—";
const reasons: Record<string, string> = {
  insufficient_distinct_frames: "В видеосерии недостаточно различных кадров",
  sampling_too_sparse: "Кадры слишком редкие для анализа падающего снега",
  poor_visibility: "Недостаточная видимость",
  camera_motion: "Обнаружено движение камеры",
  lighting_change: "Резкое изменение освещения",
  scene_change: "Сцена резко изменилась",
  insufficient_frames: "Накапливается последовательность кадров",
  invalid_frames: "Ошибка формата последовательности",
};
const score = (value?: number | null) =>
  value == null ? "—" : `${Math.round(value * 100)}/100`;
function modelNumbers(o?: Observation) {
  const s = o?.scene;
  if (!o || !s?.format_valid || !o.frame_version || o.scene_frame_version !== o.frame_version) return null;
  const values = [s.snow_probability, s.snowing_probability, s.ice_confidence, s.image_confidence];
  return values.every((v) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1) ? s : null;
}
function cameraScore(o?: Observation) {
  const numeric = modelNumbers(o);
  if (numeric && !o?.is_stale && o?.source_status === "online")
    return { label: "Оценка снегопада модели", value: numeric.snowing_probability };
  if (o?.analysis_method !== "weather_vlm_v2" && o?.snow_probability != null)
    return { label: "Балл признаков снега", value: o.snow_probability };
  return { label: "Оценка снегопада модели", value: null };
}
function historyScore(o: Observation) {
  return modelNumbers(o)?.snowing_probability ?? o.snow_probability;
}
function groundSnow(observation?: Observation) {
  const scene = observation?.scene;
  if (!observation?.frame_version || observation.is_stale || observation.source_status !== "online"
      || observation.scene_frame_version !== observation.frame_version || !scene?.format_valid
      || (scene.ground_view !== "good" && scene.ground_view !== "limited")) {
    return "нет пригодной оценки";
  }
  if (scene.snow_on_ground === "yes") return "виден по оценке модели";
  if (scene.snow_on_ground === "no") return "модель не видит снега";
  return "неясно по кадру";
}

function visualIce(o?: Observation) {
  const s = o?.scene;
  if (!s?.format_valid || o?.is_stale || o?.source_status !== "online"
      || !o.frame_version || o.scene_frame_version !== o.frame_version
      || !["good", "limited"].includes(s.road_view ?? "")) return "нет пригодной визуальной оценки";
  return s.possible_icing === "yes" ? "видимые признаки возможного обледенения"
    : "по изображению не удалось надёжно определить наличие льда";
}

function state(c: Camera) {
  const o = c.observation;
  if (o?.source_status === "offline" || o?.source_status === "degraded")
    return { text: "Источник недоступен", tone: "red" };
  if (o?.source_status === "stale")
    return { text: "Данные устарели", tone: "amber" };
  if (o?.analysis_method === "weather_vlm_v2" && o.scene?.format_valid
      && o.scene_frame_version === o.frame_version && !o.is_stale
      && ["good", "limited"].includes(o.scene.air_view ?? "")) {
    return o.status === "snowing" ? { text: "Модель видит снегопад", tone: "blue" }
      : o.status === "clear" ? { text: "Модель не видит снегопада", tone: "gray" }
      : { text: "Визуальная оценка неопределённа", tone: "amber" };
  }
  if (!o?.is_stale && o?.source_status === "online" && modelNumbers(o))
    return { text: "Оценка модели доступна", tone: "gray" };
  if (o?.snow_probability == null)
    return { text: "Недостаточно данных", tone: "amber" };
  return o.status === "snowing"
    ? { text: "Признаки снегопада", tone: "blue" }
    : { text: "Снегопад не обнаружен", tone: "gray" };
}
async function get<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, { signal });
  if (!r.ok) throw Error(`Ошибка сервера · ${r.status}`);
  return r.json();
}
export function History({ id }: { id: string }) {
  const [rows, setRows] = useState<Observation[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    const refresh = () =>
      get<Observation[]>(
        `/api/cameras/${encodeURIComponent(id)}/history`,
        controller.signal,
      )
        .then((v) => {
          setRows(v);
          setError("");
        })
        .catch((e) => {
          if (e.name !== "AbortError") setError(e.message);
        })
        .finally(() => setLoading(false));
    refresh();
    const timer = setInterval(refresh, 10000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [id]);
  if (loading) return <p>Загрузка истории…</p>;
  if (error) return <p role="alert">{error}</p>;
  if (!rows.length) return <p>Наблюдения ещё не накоплены.</p>;
  const known = rows.filter((r) => historyScore(r) != null);
  return (
    <>
      <div
        className="chart"
        role="img"
        aria-label={`История оценки снегопада. ${known.length} наблюдений с оценкой из ${rows.length}`}
      >
        <div className="axis">
          <span>100</span>
          <span>50</span>
          <span>0</span>
        </div>
        <svg viewBox="0 0 600 150" preserveAspectRatio="none">
          <path
            d="M0 5H600 M0 75H600 M0 145H600"
            stroke="#e7e8eb"
            fill="none"
          />
          {rows.slice(1).map((r, i) => {
            const p = rows[i];
            const current = historyScore(r), previous = historyScore(p);
            return current != null && previous != null && Boolean(modelNumbers(r)) === Boolean(modelNumbers(p)) ? (
              <line
                key={i}
                x1={(i * 600) / Math.max(rows.length - 1, 1)}
                y1={145 - previous * 140}
                x2={((i + 1) * 600) / Math.max(rows.length - 1, 1)}
                y2={145 - current * 140}
                stroke="#3478ee"
                strokeWidth="2.5"
              />
            ) : null;
          })}
          {rows.map((r, i) => {
            const value = historyScore(r);
            return value == null ? null : <circle key={`point-${i}`} cx={(i * 600) / Math.max(rows.length - 1, 1)} cy={145 - value * 140} r="3" fill="#3478ee" />;
          })}
        </svg>
      </div>
      <div className="chart-labels">
        <span>{time(rows[0].timestamp)}</span>
        <span>
          {known.length ? `${known.length} оценок` : "Оценок пока нет"}
        </span>
        <span>{time(rows[rows.length - 1].timestamp)}</span>
      </div>
      <p className="note">Новые точки — оценка снегопада моделью; старые — балл прежнего алгоритма.</p>
    </>
  );
}
function Detail({
  camera,
  auto,
  close,
}: {
  camera: Camera;
  auto: boolean;
  close: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  const s = state(camera),
    o = camera.observation, numbers = modelNumbers(o), metric = cameraScore(o);
  return (
    <dialog
      ref={ref}
      onCancel={close}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div className="dialog-head">
        <div>
          <small>{camera.location}</small>
          <h2>{camera.name}</h2>
        </div>
        <button
          className="icon-button"
          onClick={close}
          aria-label="Закрыть подробности"
        >
          ×
        </button>
      </div>
      <div className="dialog-content">
        <Snapshot camera={camera} auto={auto} large />
        {camera.type !== "demo" && o?.frame_version && <Feedback cameraId={camera.id} version={o.frame_version} />}
        <div className="detail-metrics">
          <div><small>{metric.label}</small><strong>{score(metric.value)}</strong></div>
          {o?.analysis_method !== "weather_vlm_v2" && <div>
            <small>Уверенность алгоритма</small>
            <strong>{score(o?.confidence)}</strong>
          </div>}
          <span className={`status ${s.tone}`}>{s.text}</span>
        </div>
        {numbers ? <p className="note">Оценка модели{(o?.is_stale || o?.source_status !== "online") ? " для последнего кадра (неактуальна)" : ""}: снег на земле — {score(numbers.snow_probability)}, снегопад — {score(numbers.snowing_probability)}, признаки льда — {score(numbers.ice_confidence)}, качество изображения — {score(numbers.image_confidence)}.</p> : o?.analysis_method === "weather_vlm_v2" && !o.scene ? <p className="note">Модель ещё не вернула оценку для этого кадра.</p> : <p className="note">
          Снег на земле: {groundSnow(o)}. Обледенение: {visualIce(o)}.
        </p>}
        {o?.scene?.format_valid && o.scene_frame_version === o.frame_version && !o.is_stale && o.source_status === "online" && <>
          <p className="note">Интенсивность снегопада: {["good", "limited"].includes(o.scene.air_view ?? "") ? ({ none: "не виден", light: "слабый", moderate: "умеренный", heavy: "сильный", uncertain: "неопределённо" }[o.scene.snowfall_intensity ?? "uncertain"]) : "нет пригодного обзора"}.
            Снег на видимой дороге: {["good", "limited"].includes(o.scene.road_view ?? "") ? ({ clear: "покрытие выглядит чистым", partial: "частичное покрытие снегом", mostly_covered: "преимущественно покрыта снегом", not_visible: "дорога не видна", uncertain: "неопределённо" }[o.scene.road_coverage ?? "uncertain"]) : "нет пригодного обзора"}.</p>
          {o.scene.visual_evidence && <p className="note">Обоснование модели: {o.scene.visual_evidence}</p>}
        </>}
        {o?.analysis_reason && (
          <p className="note">
            {reasons[o.analysis_reason] || o.analysis_reason}
          </p>
        )}
        {o?.features && Object.keys(o.features).length > 0 && (
          <details>
            <summary>Признаки качества и движения</summary>
            <dl>
              {Object.entries(o.features).map(([key, value]) => (
                <React.Fragment key={key}>
                  <dt>
                    {(
                      {
                        brightness: "Яркость",
                        contrast: "Контраст",
                        sharpness: "Резкость",
                        particle_count: "Малые движущиеся объекты",
                        spatial_coverage: "Пространственное покрытие",
                        temporal_persistence: "Временная устойчивость",
                      } as Record<string, string>
                    )[key] || key}
                  </dt>
                  <dd>{value.toFixed(2)}</dd>
                </React.Fragment>
              ))}
            </dl>
          </details>
        )}
        <h3>История наблюдений</h3>
        <History id={camera.id} />
        <details className="source-details">
          <summary>О получении данных</summary>
          <dl>
            <dt>Источник</dt>
            <dd>
              {camera.type === "demo"
                ? "Генератор тестовых кадров"
                : camera.type === "hls"
                  ? "Публичная HLS-трансляция"
                  : "Публичные JPEG-снимки"}
            </dd>
            <dt>Последнее получение</dt>
            <dd>{time(o?.frame_timestamp)}</dd>
            {o?.source_timestamp != null && (
              <>
                <dt>Дата файла у оператора</dt>
                <dd>
                  {new Date(o.source_timestamp * 1000).toLocaleString("ru-RU", {
                    timeZone: "Asia/Kamchatka",
                  })}
                </dd>
              </>
            )}
            <dt>Последняя попытка</dt>
            <dd>{time(o?.last_attempt_at)}</dd>
            <dt>Следующая попытка</dt>
            <dd>{time(o?.next_attempt_at)}</dd>
            <dt>Последний анализ</dt>
            <dd>{time(o?.last_analysis_at)}</dd>
            <dt>Метод</dt>
            <dd>
              {o?.analysis_method === "opencv_snapshot"
                ? "Признаки на одном кадре"
                : o?.analysis_method || "Ожидание анализа"}
            </dd>
          </dl>
          {camera.url && (
            <a href={camera.url} target="_blank" rel="noreferrer">
              Открыть источник изображения
            </a>
          )}
        </details>
        {(o?.source_status === "offline" ||
          o?.source_status === "degraded") && (
          <p className="error" role="status">
            {o.error ||
              "Источник не ответил вовремя. Подключение будет повторено автоматически."}
          </p>
        )}
      </div>
    </dialog>
  );
}
function mergeCameras(previous: Camera[], incoming: Camera[]): Camera[] {
  const byId = new Map(previous.map((camera) => [camera.id, camera]));
  return incoming.map((camera) => {
    const old = byId.get(camera.id);
    if (!old) return camera;
    if ((old.observation?.revision ?? 0) > (camera.observation?.revision ?? 0))
      return old;
    return JSON.stringify(old) === JSON.stringify(camera) ? old : camera;
  });
}
export function App() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState("Подключение");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [auto, setAuto] = useState(
    () => localStorage.getItem("snowvision-auto") !== "off",
  );
  const [clock, setClock] = useState(Date.now() / 1000);
  const catalogue = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    localStorage.setItem("snowvision-auto", auto ? "on" : "off");
  }, [auto]);
  const [updated, setUpdated] = useState<number>();
  async function refresh() {
    try {
      const rows = await get<Camera[]>("/api/cameras");
      setCameras((previous) => mergeCameras(previous, rows));
      setError("");
      setUpdated(Date.now() / 1000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    let connected = false;
    let pollActive = false;
    const poll = async () => {
      if (pollActive) return;
      pollActive = true;
      try {
        const rows = await get<Camera[]>("/api/cameras", controller.signal);
        if (alive) {
          setCameras((previous) => mergeCameras(previous, rows));
          setLoading(false);
          setError("");
          setUpdated(Date.now() / 1000);
        }
      } catch (e) {
        if (alive && (e as Error).name !== "AbortError") {
          setError((e as Error).message);
          setLoading(false);
        }
      } finally {
        pollActive = false;
      }
    };
    poll();
    const source = new EventSource("/api/stream/status");
    source.addEventListener("alerts", (event) => {
      try {
        window.dispatchEvent(
          new CustomEvent("snowvision-alerts", {
            detail: JSON.parse((event as MessageEvent).data),
          }),
        );
      } catch {
        setError("Ошибка чтения журнала сигналов");
      }
    });
    source.onopen = () => {
      connected = true;
      setConnection("Обновляется автоматически");
    };
    source.onmessage = (e) => {
      try {
        setCameras((previous) => mergeCameras(previous, JSON.parse(e.data)));
        setUpdated(Date.now() / 1000);
        setError("");
        setLoading(false);
      } catch {
        setError("Не удалось прочитать обновление");
      }
    };
    source.onerror = () => {
      connected = false;
      setConnection("Переподключение · резервное обновление");
      poll();
    };
    const timer = setInterval(() => {
      if (!connected) poll();
    }, 10000);
    const clockTimer = setInterval(() => setClock(Date.now() / 1000), 1000);
    return () => {
      alive = false;
      controller.abort();
      source.close();
      clearInterval(timer);
      clearInterval(clockTimer);
    };
  }, []);
  useEffect(() => {
    const openCatalogue = () => {
      if (catalogue.current) {
        catalogue.current.open = true;
        catalogue.current.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    };
    window.addEventListener("snowvision-show-cameras", openCatalogue);
    return () => window.removeEventListener("snowvision-show-cameras", openCatalogue);
  }, []);
  const updateObservation = useCallback(
    (id: string, observation: Observation) =>
      setCameras((rows) =>
        rows.map((c) =>
          c.id === id ? mergeCameras([c], [{ ...c, observation }])[0] : c,
        ),
      ),
    [],
  );
  const visible = cameras.filter(
    (c) =>
      (c.name + " " + c.location).toLowerCase().includes(query.toLowerCase()) &&
      (filter === "all" ||
        (filter === "real" && c.type !== "demo") ||
        (filter === "snow" && c.observation?.status === "snowing") ||
        (filter === "offline" &&
          ["offline", "degraded"].includes(
            c.observation?.source_status || "",
          )) ||
        (filter === "online" && c.observation?.source_status === "online") ||
        (filter === "stale" && c.observation?.is_stale) ||
        (filter === "demo" && c.type === "demo")),
  );
  const chosen = cameras.find((c) => c.id === selected);
  return (
    <>
      <header>
        <a className="brand" href="/" aria-label="SnowVision, главная">
          <span className="brand-mark">св</span>SnowVision
          <span className="brand-region">Камчатка</span>
        </a>
        <nav className="dashboard-nav" aria-label="Навигация по дашборду">
          <a href="#overview">Обзор</a>
          <a href="#signals">Сигналы</a>
          <a href="#weather">Погода</a>
          <button onClick={() => window.dispatchEvent(new CustomEvent("snowvision-show-cameras"))}>
            Камеры
          </button>
        </nav>
        <div className="connection">
          <i />
          {connection}
        </div>
      </header>
      <main>
        <div className="heading compact-heading" id="overview">
          <div><p className="eyebrow">КАМЧАТСКИЙ КРАЙ</p><h1>Оперативная обстановка</h1></div>
          <div className="updated">Обновление<strong>{time(updated)}</strong><small>{connection} · UTC+12</small></div>
        </div>
        <CityPanel cameras={cameras} onSelectCamera={setSelected} auto={auto} now={clock} alerts={<AlertPanel />} />
        <details className="camera-catalogue" ref={catalogue}>
          <summary>Все камеры · {loading ? "загрузка" : `${cameras.length} источников`}</summary>
          <section className="cameras-section" aria-label="Все камеры наблюдения">
          <div className="toolbar">
            <h2>
              Камеры <span>{cameras.length}</span>
            </h2>
            <label className="search">
              <span>⌕</span>
              <input
                aria-label="Поиск камеры"
                placeholder="Название или место"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
          </div>
          <div className="camera-controls">
            <div className="filters" aria-label="Фильтр камер">
              {[["all", "Все камеры"], ["online", "В сети"], ["snow", "Признаки снега"]].map(([value, label]) => (
                <button key={value} aria-pressed={filter === value}
                  className={filter === value ? "active" : ""}
                  onClick={() => setFilter(value)}>{label}</button>
              ))}
              <label className="extra-filter">
                <span className="sr-only">Другие камеры</span>
                <select value={["real", "stale", "demo", "offline"].includes(filter) ? filter : ""}
                  onChange={(e) => setFilter(e.target.value || "all")}>
                  <option value="">Ещё фильтры</option>
                  <option value="real">Реальные</option>
                  <option value="stale">Устаревшие</option>
                  <option value="demo">Демонстрационные</option>
                  <option value="offline">Недоступные</option>
                </select>
              </label>
            </div>
            <label className="auto-toggle">
              <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
              Автообновление изображений {auto ? "включено" : "выключено"}
            </label>
          </div>
          {error && (
            <div className="error" role="alert">
              {error}. Последние полученные данные сохранены на экране.{" "}
              <button onClick={refresh}>Повторить</button>
            </div>
          )}
          {loading ? (
            <div className="grid" aria-busy="true">
              {[1, 2, 3].map((i) => (
                <div className="skeleton" key={i}>
                  <div />
                  <p>Загрузка камеры…</p>
                </div>
              ))}
            </div>
          ) : visible.length ? (
            <div className="grid">
              {visible.map((c) => (
                <CameraCard
                  key={c.id}
                  camera={c}
                  auto={auto}
                  now={clock}
                  open={setSelected}
                  onObservation={updateObservation}
                />
              ))}
            </div>
          ) : (
            <div className="empty">
              <h3>
                {cameras.length
                  ? "Камеры не найдены"
                  : "Камеры пока не настроены"}
              </h3>
              <p>
                {cameras.length
                  ? "Попробуйте другое название или выберите все камеры."
                  : "Добавьте источники в конфигурацию сервиса."}
              </p>
              {cameras.length > 0 && (
                <button
                  onClick={() => {
                    setQuery("");
                    setFilter("all");
                  }}
                >
                  Сбросить фильтры
                </button>
              )}
            </div>
          )}
          </section>
        </details>
        <footer>
          <span>SnowVision Kamchatka</span>
        </footer>
      </main>
      {chosen && (
        <Detail camera={chosen} auto={auto} close={() => setSelected(null)} />
      )}
    </>
  );
}
const age = (value: number | null | undefined, now: number) =>
  value == null
    ? "—"
    : `${Math.max(0, Math.floor((now - value) / 60))} мин ${Math.max(0, Math.floor(now - value) % 60)} сек назад`;
const CameraCard = memo(function CameraCard({
  camera: c,
  auto,
  now,
  open,
  onObservation,
}: {
  camera: Camera;
  auto: boolean;
  now: number;
  open: (id: string) => void;
  onObservation: (id: string, observation: Observation) => void;
}) {
  const [manual, setManual] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const s = state(c),
    o = c.observation;
  const controller = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => controller.current?.abort(), []);
  async function refreshOne() {
    if (busy || o?.is_fetching) return;
    setBusy(true);
    controller.current = new AbortController();
    try {
      const response = await fetch(
        `/api/cameras/${encodeURIComponent(c.id)}/refresh`,
        { method: "POST", signal: controller.current.signal },
      );
      if (!response.ok) throw Error("Не удалось проверить камеру");
      const result = await response.json();
      onObservation(c.id, result.observation);
      setManual((v) => v + 1);
      setMessage(
        result.result === "rate_limited"
          ? "Интервал источника ещё не истёк. Проверены доступные данные."
          : result.result === "in_progress"
            ? "Запрос уже выполняется"
            : result.result === "error"
              ? "Источник недоступен. Последний кадр сохранён."
              : "Данные проверены",
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const metric = cameraScore(o);
  return (
    <article>
      <Snapshot camera={c} auto={auto} manual={manual} now={now} />
      <div className="card-body">
        <div className="card-place">{c.location}</div>
        <h3>{c.name}</h3>
        <span className={`status ${s.tone}`}>
          <i />
          {s.text}
        </span>
        <div className="card-score">
          <span>{metric.label}</span>
          <strong className={s.tone === "blue" ? "blue-text" : ""}>{score(metric.value)}</strong>
        </div>
        <p className="freshness">Кадр на сервере {age(o?.frame_timestamp, now)}</p>
        <div className="card-bottom">
          <button
            onClick={refreshOne}
            disabled={busy || o?.is_fetching}
            aria-label={`Обновить ${c.name}`}
          >
            {busy ? "Проверяем…" : "Обновить"}
          </button>
          <button onClick={() => open(c.id)}>Подробнее</button>
        </div>
        {message && (
          <p className="refresh-message" role="status">
            {message}
          </p>
        )}
      </div>
    </article>
  );
});
