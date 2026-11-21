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
  expect(screen.getAllByText("—").length).toBe(2);
  expect(screen.queryByText("0%")).toBeNull();
});

it("uses snow ensemble probability alongside the precipitation forecast", async () => {
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
            ensemble: {
              status: "available",
              snow_probability: 0.25,
              member_count: 40,
              snowy_members: 10,
            },
            hours: [
              {
                timestamp: 2000000000,
                precipitation_probability: 90,
                snowfall_cm: 0.2,
                temperature_c: -2,
                snow_expected: true,
              },
            ],
          },
        }),
      }),
  );
  render(<CityPanel />);
  await screen.findByText("25%");
  expect(screen.getByText(/10 из 40 сценариев/)).toBeDefined();
  expect(screen.getByText(/Осадки/).textContent).toContain("90%");
});
