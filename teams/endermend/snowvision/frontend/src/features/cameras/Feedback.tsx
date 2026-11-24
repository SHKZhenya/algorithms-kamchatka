import { useEffect, useState } from "react";

type Label = "yes" | "no" | "uncertain";
type Labels = { snow_on_ground: Label; possible_icing: Label; falling_snow: Label };
const questions: Array<[keyof Labels, string]> = [
  ["snow_on_ground", "Снег на земле"],
  ["possible_icing", "Видимые признаки льда"],
  ["falling_snow", "Снегопад"],
];
const initial: Labels = { snow_on_ground: "uncertain", possible_icing: "uncertain", falling_snow: "uncertain" };

export function Feedback({ cameraId, version }: { cameraId: string; version: string }) {
  const [open, setOpen] = useState(false);
  const [frozen, setFrozen] = useState(version);
  const [labels, setLabels] = useState<Labels>(initial);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [correction, setCorrection] = useState<Labels | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setCorrection(null);
    fetch(`/api/cameras/${encodeURIComponent(cameraId)}/feedback?version=${encodeURIComponent(version)}`, { signal: controller.signal })
      .then(r => r.ok ? r.json() : null).then(data => setCorrection(data?.labels ?? null))
      .catch(() => {});
    return () => controller.abort();
  }, [cameraId, version]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    fetch(`/api/cameras/${encodeURIComponent(cameraId)}/feedback?version=${encodeURIComponent(frozen)}`, { signal: controller.signal })
      .then(r => { if (!r.ok) throw Error(); return r.json(); })
      .then(data => setLabels(data?.labels ?? initial))
      .catch(e => { if (e.name !== "AbortError") setMessage("Не удалось загрузить сохранённую разметку"); });
    return () => controller.abort();
  }, [cameraId, frozen, open]);
  async function submit() {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`/api/cameras/${encodeURIComponent(cameraId)}/feedback`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: frozen, ...labels }),
      });
      if (response.status === 409) throw Error("Кадр изменился. Закройте разметку и откройте её заново.");
      if (!response.ok) throw Error("Не удалось сохранить разметку");
      setCorrection(labels);
      setMessage("Сохранено. Это уточнение относится только к показанному кадру.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Ошибка сохранения"); }
    finally { setBusy(false); }
  }
  if (!open) return <div>{correction && <p className="note">Уточнение оператора для этого кадра: {questions.map(([key, title]) => `${title}: ${{ yes: "да", no: "нет", uncertain: "неясно" }[correction[key]]}`).join("; ")}. Оценка модели показана отдельно.</p>}<button onClick={() => { setFrozen(version); setLabels(initial); setMessage(""); setOpen(true); }}>Разметить кадр</button></div>;
  return <section className="feedback-form">
    <h3>Разметка кадра</h3>
    <img src={`/api/cameras/${encodeURIComponent(cameraId)}/snapshot?v=${encodeURIComponent(frozen)}`} alt="Кадр для разметки" onError={() => setMessage("Кадр больше недоступен. Откройте разметку заново.")} />
    <p className="note">Отметьте только то, что различимо на изображении. Прозрачный лёд может быть невидим.</p>
    {questions.map(([key, title]) => <label key={key}>{title} <select value={labels[key]} onChange={e => setLabels({ ...labels, [key]: e.target.value as Label })}>
      <option value="uncertain">Неясно</option><option value="yes">Да</option><option value="no">Нет</option>
    </select></label>)}
    <div><button disabled={busy} onClick={submit}>Сохранить</button> <button onClick={() => setOpen(false)}>Закрыть</button></div>
    {message && <p role="status">{message}</p>}
  </section>;
}
