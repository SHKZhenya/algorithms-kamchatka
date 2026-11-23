// @vitest-environment jsdom
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CityPanel } from "./CityPanel";
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
  await screen.findByText("Недостаточно независимых городских наблюдений");
  expect(screen.getByText("—")).toBeDefined();
  expect(screen.queryByText("0%")).toBeNull();
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
  await screen.findByRole("link", { name: "Яндекс Погода" });
  expect(screen.getByText(/Дождь со снегом/)).toBeDefined();
  expect(screen.getByText("Вероятность осадков 90%")).toBeDefined();
  expect(screen.getByText("Упомянут Петропавловск-Камчатский").closest("details")?.open).toBe(true);
  expect(screen.getByText(/20 ноября/).closest("details")?.open).toBe(false);
});

it("links to official warnings when the city API is unreachable", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Сбой API")));
  render(<CityPanel />);

  await screen.findByText("Сбой API");
  screen.getByText("Официальные штормовые предупреждения").click();
  const destinations = screen
    .getAllByRole("link")
    .map((link) => link.getAttribute("href"));
  expect(destinations).toEqual([
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
  const summary = await screen.findByText("Штормовые предупреждения Камчатского края");
  expect(summary.closest("details")?.open).toBe(expanded);
});
