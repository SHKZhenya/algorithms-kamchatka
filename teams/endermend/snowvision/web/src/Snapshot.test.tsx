// @vitest-environment jsdom
import React from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Snapshot } from "./Snapshot";
let observers: Array<(entries: unknown[]) => void>;
const camera = {
  id: "a",
  name: "Камера",
  type: "jpeg",
  observation: {
    frame_version: "one",
    frame_timestamp: 100,
    source_status: "online",
  },
};
beforeEach(() => {
  observers = [];
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(callback: (entries: unknown[]) => void) {
        observers.push(callback);
      }
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    "Image",
    class {
      src = "";
      decode() {
        return Promise.resolve();
      }
    },
  );
  URL.createObjectURL = vi.fn().mockReturnValue("blob:one");
  URL.revokeObjectURL = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue({ ok: true, blob: async () => new Blob(["image"]) }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function visible(value: boolean) {
  act(() =>
    observers.forEach((callback) => callback([{ isIntersecting: value }])),
  );
}
describe("lazy image updates", () => {
  it("waits for viewport and checks version on return", async () => {
    const { rerender } = render(<Snapshot camera={camera} />);
    expect(fetch).not.toHaveBeenCalled();
    visible(true);
    await screen.findByAltText("Кадр камеры «Камера»");
    expect(fetch).toHaveBeenCalledTimes(1);
    visible(false);
    visible(true);
    expect(fetch).toHaveBeenCalledTimes(1);
    visible(false);
    rerender(
      <Snapshot
        camera={{
          ...camera,
          observation: { ...camera.observation, frame_version: "two" },
        }}
      />,
    );
    expect(fetch).toHaveBeenCalledTimes(1);
    visible(true);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  });
  it("keeps the previous image on network error", async () => {
    const { rerender } = render(<Snapshot camera={camera} />);
    visible(true);
    const image = await screen.findByAltText("Кадр камеры «Камера»");
    vi.mocked(fetch).mockRejectedValueOnce(Error("offline"));
    rerender(
      <Snapshot
        camera={{
          ...camera,
          observation: { ...camera.observation, frame_version: "two" },
        }}
      />,
    );
    await screen.findByText("Последний доступный кадр");
    expect(image.getAttribute("src")).toBe("blob:one");
  });
  it("disables automatic downloads but permits manual checks", async () => {
    const { rerender } = render(<Snapshot camera={camera} auto={false} />);
    visible(true);
    expect(fetch).not.toHaveBeenCalled();
    rerender(<Snapshot camera={camera} auto={false} manual={1} />);
    await screen.findByAltText("Кадр камеры «Камера»");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("ignores a late response from an older version", async () => {
    let resolve: (value: Response) => void = () => {};
    vi.mocked(fetch).mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const { rerender } = render(<Snapshot camera={camera} />);
    visible(true);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    URL.createObjectURL = vi
      .fn()
      .mockReturnValueOnce("blob:new")
      .mockReturnValueOnce("blob:old");
    rerender(
      <Snapshot
        camera={{
          ...camera,
          observation: { ...camera.observation, frame_version: "two" },
        }}
      />,
    );
    const image = await screen.findByAltText("Кадр камеры «Камера»");
    await act(async () =>
      resolve({ ok: true, blob: async () => new Blob(["old"]) } as Response),
    );
    expect(image.getAttribute("src")).toBe("blob:new");
  });
});

it("releases object URLs on unmount", async () => {
  const view = render(<Snapshot camera={camera} />);
  visible(true);
  await screen.findByAltText("Кадр камеры «Камера»");
  view.unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:one");
});
