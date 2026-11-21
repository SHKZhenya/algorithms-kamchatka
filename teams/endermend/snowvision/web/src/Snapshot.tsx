import { memo, useEffect, useRef, useState } from "react";

type SnapshotCamera = {
  id: string;
  name: string;
  type: string;
  observation?: {
    frame_version?: string | null;
    frame_timestamp: number | null;
    source_status: string;
    is_fetching?: boolean;
  };
};
let active = 0;
const waiting: Array<() => void> = [];
async function slot(signal: AbortSignal) {
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  if (active < 4) {
    active++;
    return;
  }
  await new Promise<void>((resolve, reject) => {
    const ready = () => {
      signal.removeEventListener("abort", abort);
      active++;
      resolve();
    };
    const abort = () => {
      const index = waiting.indexOf(ready);
      if (index >= 0) waiting.splice(index, 1);
      reject(new DOMException("Aborted", "AbortError"));
    };
    waiting.push(ready);
    signal.addEventListener("abort", abort, { once: true });
  });
}
function release() {
  active--;
  waiting.shift()?.();
}
export async function loadSnapshot(
  url: string,
  signal: AbortSignal,
): Promise<string> {
  await slot(signal);
  let objectUrl: string | undefined;
  try {
    const response = await fetch(url, { signal });
    if (!response.ok) throw Error("Не удалось получить кадр");
    objectUrl = URL.createObjectURL(await response.blob());
    const image = new Image();
    image.src = objectUrl;
    await image.decode();
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    return objectUrl;
  } catch (error) {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    throw error;
  } finally {
    release();
  }
}
export const Snapshot = memo(function Snapshot({
  camera,
  auto = true,
  manual = 0,
  large = false,
  now = Date.now() / 1000,
}: {
  camera: SnapshotCamera;
  auto?: boolean;
  manual?: number;
  large?: boolean;
  now?: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [display, setDisplay] = useState<{
    url: string;
    version: string;
    timestamp: number | null;
  }>();
  const [previous, setPrevious] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const owned = useRef(new Set<string>());
  const manualSeen = useRef(manual);
  const version = camera.observation?.frame_version;
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => setVisible(entries[0].isIntersecting),
      { rootMargin: "300px" },
    );
    observer.observe(container.current!);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const requested = manual !== manualSeen.current;
    if (
      !visible ||
      (!auto && !requested) ||
      !version ||
      display?.version === version
    )
      return;
    manualSeen.current = manual;
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    loadSnapshot(
      `/api/cameras/${encodeURIComponent(camera.id)}/snapshot?v=${encodeURIComponent(version)}`,
      controller.signal,
    )
      .then((url) => {
        if (controller.signal.aborted) {
          URL.revokeObjectURL(url);
          return;
        }
        owned.current.add(url);
        setPrevious(display?.url);
        setDisplay({
          url,
          version,
          timestamp: camera.observation?.frame_timestamp ?? null,
        });
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      setLoading(false);
    };
  }, [camera.id, version, visible, auto, manual, display?.version]);
  useEffect(() => {
    if (!previous) return;
    const timer = setTimeout(() => {
      URL.revokeObjectURL(previous);
      owned.current.delete(previous);
      setPrevious(undefined);
    }, 240);
    return () => {
      clearTimeout(timer);
      URL.revokeObjectURL(previous);
      owned.current.delete(previous);
    };
  }, [previous]);
  useEffect(
    () => () => {
      for (const url of owned.current) URL.revokeObjectURL(url);
      owned.current.clear();
    },
    [],
  );
  const degraded = ["degraded", "offline", "stale"].includes(
    camera.observation?.source_status || "",
  );
  return (
    <div ref={container} className={`snapshot ${large ? "large" : ""}`}>
      {previous && <img className="previous-frame" src={previous} alt="" />}
      {display && (
        <img
          key={display.url}
          className="current-frame"
          src={display.url}
          alt={`Кадр камеры «${camera.name}»`}
        />
      )}
      {!display && (
        <div className={`image-fallback ${loading ? "first-loading" : ""}`}>
          <span>◌</span>
          <p>
            {!auto && !loading
              ? "Автообновление выключено"
              : error
                ? "Кадр пока недоступен"
                : version
                  ? "Получаем изображение"
                  : "Ожидание первого кадра"}
          </p>
          <small>
            {!auto && !loading
              ? "Нажмите «Обновить», чтобы получить кадр"
              : error
                ? "Последний кадр появится после успешной загрузки"
                : "Изображение загрузится в видимой области"}
          </small>
        </div>
      )}
      {camera.type === "demo" && (
        <span className="demo-label">Демо · синтетическая сцена</span>
      )}
      {(loading || camera.observation?.is_fetching) && display && (
        <span className="updating">Обновляется</span>
      )}
      {display && (degraded || error) && (
        <span className="last-frame">Последний доступный кадр</span>
      )}
      <span className="frame-time">
        {display?.timestamp
          ? new Date(display.timestamp * 1000).toLocaleTimeString("ru-RU", {
              timeZone: "Asia/Kamchatka",
            })
          : "—"}{" "}
        · Камчатка
        {display?.timestamp && (
          <>
            {" "}
            · {Math.max(0, Math.floor((now - display.timestamp) / 60))} мин
            назад
          </>
        )}
      </span>
    </div>
  );
});
