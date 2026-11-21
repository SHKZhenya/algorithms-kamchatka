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
vi.mock("./AlertPanel", () => ({ AlertPanel: () => null }));
vi.mock("./CityPanel", () => ({ CityPanel: () => null }));
import { App } from "./main";
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
