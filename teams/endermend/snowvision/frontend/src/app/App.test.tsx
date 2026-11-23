// @vitest-environment jsdom
import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("../features/cameras/Snapshot", () => ({ Snapshot: () => null }));
vi.mock("../features/alerts/AlertPanel", () => ({ AlertPanel: () => null }));
vi.mock("../features/city/CityPanel", () => ({ CityPanel: () => null }));
import { App } from "./App";
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
});
it("uses one SSE connection and polling only after connection loss, cleans up on unmount", async () => {
  vi.useFakeTimers();
  let stream:
    | {
        onopen?: () => void;
        onerror?: () => void;
        close: ReturnType<typeof vi.fn>;
        addEventListener?: ReturnType<typeof vi.fn>;
      }
    | undefined;
  const close = vi.fn();
  const events = vi.fn(function () {
    stream = { close, addEventListener: vi.fn() };
    return stream;
  });
  vi.stubGlobal("EventSource", events);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => [] }),
  );
  const view = render(<App />);
  await act(async () => {});
  expect(events).toHaveBeenCalledTimes(1);
  act(() => stream?.onopen?.());
  const before = vi.mocked(fetch).mock.calls.length;
  await act(async () => vi.advanceTimersByTime(10000));
  expect(fetch).toHaveBeenCalledTimes(before);
  act(() => stream?.onerror?.());
  await act(async () => {});
  expect(fetch).toHaveBeenCalledTimes(before + 1);
  await act(async () => vi.advanceTimersByTime(10000));
  expect(fetch).toHaveBeenCalledTimes(before + 2);
  const toggle = screen.getByRole("checkbox", {
    name: /Автообновление изображений/,
  });
  fireEvent.click(toggle);
  expect(localStorage.getItem("snowvision-auto")).toBe("off");
  expect(events).toHaveBeenCalledTimes(1);
  view.unmount();
  expect(close).toHaveBeenCalledTimes(1);
});

it("keeps search, additional filters and reset available in the simplified toolbar", async () => {
  vi.stubGlobal("EventSource", vi.fn(function () {
    return { close: vi.fn(), addEventListener: vi.fn() };
  }));
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [
    { id: "street", name: "Улица Чехова", location: "Город", type: "jpeg", observation: { source_status: "online" } },
    { id: "hill", name: "Гора", location: "Край", type: "jpeg", observation: { source_status: "offline" } },
    { id: "demo", name: "Тестовая сцена", location: "Демо", type: "demo" },
  ] }));
  render(<App />);
  await screen.findByRole("heading", { name: "Улица Чехова" });
  fireEvent.change(screen.getByRole("combobox", { name: "Другие камеры" }), { target: { value: "offline" } });
  expect(screen.queryByRole("heading", { name: "Улица Чехова" })).toBeNull();
  expect(screen.getByRole("heading", { name: "Гора" })).toBeDefined();
  fireEvent.change(screen.getByRole("textbox", { name: "Поиск камеры" }), { target: { value: "Чехова" } });
  expect(screen.getByText("Камеры не найдены")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Сбросить фильтры" }));
  expect(screen.getByRole("heading", { name: "Улица Чехова" })).toBeDefined();
  expect((screen.getByRole("combobox", { name: "Другие камеры" }) as HTMLSelectElement).value).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "В сети" }));
  expect(screen.queryByRole("heading", { name: "Гора" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Тестовая сцена" })).toBeNull();
});
