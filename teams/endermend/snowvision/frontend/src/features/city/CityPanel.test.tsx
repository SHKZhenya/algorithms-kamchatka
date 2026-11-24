// @vitest-environment jsdom
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CityPanel } from "./CityPanel";
vi.mock("../cameras/Snapshot", () => ({ Snapshot: ({ camera }: { camera: { observation?: { frame_version?: string | null; frame_timestamp?: number | null } } }) => <div data-testid="snapshot" data-version={camera.observation?.frame_version} data-timestamp={camera.observation?.frame_timestamp} /> }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("shows unknown when city coverage and weather are unavailable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        observations: {
          snow_score: null,
          eligible_cameras: 0,
          independent_zones: 0,
          status: "insufficient_coverage",
        },
        forecast: { status: "unavailable", hours: [] },
      }),
    }),
  );
  render(<CityPanel />);
  await screen.findByText("Недостаточно свежих данных");
  expect(screen.getByText(/нужно минимум 2 для городского балла/)).toBeDefined();
  expect(screen.queryByText(/0 пригодных камер · 0 представителей в индексе/)).toBeNull();
  expect(screen.queryByText("0%")).toBeNull();
});

it("shows inconclusive evidence without technical threshold notes", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    observations: {
      snowfall_state: "inconclusive", snow_score: null, independent_zones: 1,
      eligible_cameras: 1, calculated_at: 2000000000, last_evidence_at: 2000000000,
      negative_evidence_count: 0, inconclusive_evidence_count: 1,
      signal_policy: "Городской индекс: от 0,20; журнал: от 0,25",
    },
    forecast: { status: "unavailable", hours: [] },
  }) }));
  render(<CityPanel now={2000000000} />);
  await screen.findByText("Снегопад не удалось оценить");
  expect(screen.getByText(/неопределённые свидетельства: 1/)).toBeDefined();
  expect(screen.queryByText(/Городской индекс: от 0,20; журнал: от 0,25/)).toBeNull();
  expect(screen.queryByText("Снегопад не обнаружен")).toBeNull();
});

it("shows the Yandex forecast and keeps past warnings separate", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          observations: {
            snow_score: null,
            eligible_cameras: 0,
            independent_zones: 0,
            status: "insufficient_coverage",
          },
          forecast: {
            status: "available",
            hours: [
              {
                timestamp: 2000000000,
                precipitation_probability: 90,
                precipitation: "sleet",
                precipitation_mm: 0.2,
                snowfall_cm: null,
                temperature_c: -2,
              },
            ],
          },
          warnings: {
            status: "available",
            source: "Росгидромет",
            source_url: "https://dfo.meteorf.gov.ru/ekstrennaya-informatsiya/",
            items: [
              { text: "Ожидается сильный ветер в городе.", city_mentioned: true, date_status: "today", last_date: "2026-11-22" },
              { text: "20 ноября в крае был сильный снег.", city_mentioned: false, date_status: "past", last_date: "2026-11-20" },
            ],
          },
        }),
      }),
  );
  render(<CityPanel />);
  await screen.findByRole("link", { name: /Яндекс Погода/ });
  expect(screen.getByText(/дождь со снегом/i)).toBeDefined();
  expect(screen.getByText(/вероятность осадков 90%/i)).toBeDefined();
  expect(screen.getByText(/Петропавловск-Камчатский упомянут/)).toBeDefined();
  screen.getByText("Подробнее").click();
  expect(screen.getByText(/20 ноября/).closest("details")?.open).toBe(false);
});

it("links to official warnings when the city API is unreachable", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Сбой API")));
  render(<CityPanel />);

  await screen.findByText("Связь с сервисом потеряна");
  screen.getByText("Официальные источники предупреждений").click();
  const destinations = screen
    .getAllByRole("link")
    .map((link) => link.getAttribute("href"));
  expect(destinations).toEqual([
    "https://yandex.ru/pogoda/",
    "https://kammeteo.ru/",
    "https://dfo.meteorf.gov.ru/ekstrennaya-informatsiya/",
    "https://meteoinfo.ru/hdmapsmeteoalarmdv",
    "https://41.mchs.gov.ru/deyatelnost/press-centr/operativnaya-informaciya/shtormovye-i-ekstrennye-preduprezhdeniya",
  ]);
});

it.each([
  ["available", "past", false],
  ["available", "unknown", true],
  ["stale", "past", true],
])("keeps warning visibility appropriate for %s bulletin and %s date", async (status, date_status, expanded) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    observations: { snow_score: null, eligible_cameras: 0, independent_zones: 0, status: "insufficient_coverage" },
    forecast: { status: "unavailable", hours: [] },
    warnings: { status, source: "Росгидромет", source_url: "https://dfo.meteorf.gov.ru/ekstrennaya-informatsiya/",
      items: [{ text: "Сообщение бюллетеня", date_status, city_mentioned: false, last_date: null }] },
  }) }));
  render(<CityPanel />);
  const summary = await screen.findByRole("region", { name: "Штормовое предупреждение" });
  expect(summary.textContent).toMatch(expanded ? /недоступен|устарел|не определён/i : /нет актуальных сообщений/i);
});

it("distinguishes local signs from city coverage and forecast", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    observations: {
      snowfall_state: "local_positive", snow_score: null, local_observation_score: 0.8,
      independent_zones: 1, eligible_cameras: 1, status: "insufficient_coverage",
      calculated_at: 2000000000, last_evidence_at: 2000000000, evidence_count: 1,
      local_evidence: [{ camera_id: "one", name: "Улица", location: "Чехова", positive: true, negative: false, conflict: false, stage: "early", timestamp: 2000000000, method: "weather_vlm_v2" }],
      contributors: [],
    },
    weather: { status: "available", precipitation: "snow", temperature_c: -1, observed_at: 2000000000 },
    forecast: { status: "available", hours: [{ timestamp: 2000003600, precipitation: "snow", precipitation_probability: 80, temperature_c: -2 }] },
    warnings: { status: "available", source: "Росгидромет", source_url: "https://example.com", region: "Камчатский край", items: [] },
  }) }));
  render(<CityPanel />);
  await screen.findByText("Первые признаки снегопада");
  expect(screen.getByText(/недостаточно независимых зон для городской оценки/)).toBeDefined();
  expect(screen.getByText(/Текущее наблюдение/)).toBeDefined();
  expect(screen.getByText(/Ближайшие часы/)).toBeDefined();
});

it("shows at most three positive contributors in backend order and opens camera details", async () => {
  const open = vi.fn();
  const cameras = ["a", "b", "c", "d"].map((id) => ({ id, name: `Камера ${id}`, location: "Город", type: "hls", observation: { frame_timestamp: 2000000000, source_status: "online" } }));
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    observations: {
      snowfall_state: "observed", snow_score: 0.7, independent_zones: 4, eligible_cameras: 4,
      calculated_at: 2000000000, last_evidence_at: 2000000000, local_evidence: [],
      contributors: ["a", "b", "c", "d"].map((id, index) => ({ id, name: `Камера ${id}`, location: "Город", score: 0.8, contribution: 0.4 - index * 0.05, timestamp: 2000000000, age_seconds: 0, frame_version: id, method: "opencv_motion", reason: "Свежее свидетельство из отдельной зоны", positive: true })),
    }, forecast: { status: "unavailable", hours: [] },
  }) }));
  const { container } = render(<CityPanel cameras={cameras} onSelectCamera={open} />);
  await screen.findByRole("heading", { name: "Камеры, влияющие на оценку" });
  expect(container.querySelectorAll(".priority-camera")).toHaveLength(3);
  expect(screen.queryByRole("heading", { name: "Камера d" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Открыть кадр и историю: Камера a" }));
  expect(open).toHaveBeenCalledWith("a");
});

it("shows a valid local model signal without inventing an index contribution", async () => {
  const cameras = [{ id: "vlm", name: "Чехова", location: "Петропавловск-Камчатский", type: "jpeg", observation: { frame_timestamp: 2000000000, source_status: "online" } }];
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    observations: {
      snowfall_state: "local_positive", snow_score: null, independent_zones: 1, eligible_cameras: 0,
      calculated_at: 2000000000, last_evidence_at: 2000000000,
      local_evidence: [{ camera_id: "vlm", name: "Чехова", location: "Петропавловск-Камчатский", positive: true, negative: false, conflict: false, stage: "early", timestamp: 2000000000, frame_timestamp: 1999999900, method: "weather_vlm_v2", model_only: true, frame_version: "vlm-evidence" }],
      contributors: [],
    }, forecast: { status: "disabled", hours: [] },
  }) }));
  render(<CityPanel cameras={cameras} />);
  expect(await screen.findByText("Локальное свидетельство")).toBeDefined();
  expect(screen.getByText("Без численного вклада")).toBeDefined();
  expect(screen.getByTestId("snapshot").getAttribute("data-version")).toBe("vlm-evidence");
  expect(screen.getByTestId("snapshot").getAttribute("data-timestamp")).toBe("1999999900");
  expect(screen.queryByText(/не входит в формулу городского индекса/)).toBeNull();
  expect(screen.queryByText(/\+\d+\/100 к индексу/)).toBeNull();
});

it("shows every recent camera's score, normalized weight and source", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    observations: { evidence_scope: "demo_all_cameras", snowfall_state: "inconclusive", snow_score: .75,
      independent_zones: 0, eligible_cameras: 2, status: "available", calculated_at: 2000000000,
      last_evidence_at: 2000000000, local_evidence: [], contributors: [], representatives: [
        { id: "a", name: "Камера A", score: 1, weight: .5, score_source: "model" },
        { id: "b", name: "Камера B", score: .5, weight: .5, score_source: "neutral" },
      ] }, forecast: { status: "unavailable", hours: [] },
  }) }));
  render(<CityPanel />);
  await screen.findByText("Оценки и веса всех камер (2)");
  expect(screen.getByText(/Камера A/).parentElement?.textContent).toContain("100/100 · вес 50.0% · Gemma");
  expect(screen.getByText(/Камера B/).parentElement?.textContent).toContain("50/100 · вес 50.0% · нейтрально");
});
