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

it("retries a failed download without requiring a new frame version", async () => {
  vi.useFakeTimers();
  try {
    vi.mocked(fetch).mockRejectedValueOnce(Error("temporary"));
    render(<Snapshot camera={camera}/>); visible(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
    expect(screen.getByAltText("Кадр камеры «Камера»")).toBeDefined();
    expect(fetch).toHaveBeenCalledTimes(2);
  } finally {vi.useRealTimers();}
});

it("releases a stuck decoder and retries instead of exhausting the queue", async () => {
  vi.useFakeTimers();
  try {
    let decodes=0;
    vi.stubGlobal("Image",class {src="";decode(){decodes++; return decodes===1?new Promise<void>(()=>{}):Promise.resolve();}});
    render(<Snapshot camera={camera}/>); visible(true);
    await act(async () => {await vi.advanceTimersByTimeAsync(14000);});
    expect(screen.getByAltText("Кадр камеры «Камера»")).toBeDefined();
    expect(fetch).toHaveBeenCalledTimes(2);
  } finally {vi.useRealTimers();}
});

it("cancels queued work without leaking slots and admits later requests", async () => {
  const {loadSnapshot} = await import('./Snapshot');
  const decoders:Array<()=>void>=[];
  vi.stubGlobal("Image",class {src="";decode(){return new Promise<void>(resolve=>decoders.push(resolve));}});
  const controllers=Array.from({length:5},()=>new AbortController());
  const first=controllers.slice(0,4).map((c,i)=>loadSnapshot(`/frame/${i}`,c.signal));
  await waitFor(()=>expect(decoders).toHaveLength(4));
  const queued=loadSnapshot('/frame/queued',controllers[4].signal);
  const rejected=expect(queued).rejects.toMatchObject({name:'AbortError'});
  controllers[4].abort();await rejected;
  expect(fetch).toHaveBeenCalledTimes(4);
  decoders.splice(0).forEach(resolve=>resolve());
  const urls=await Promise.all(first);urls.forEach(url=>URL.revokeObjectURL(url));
  const last=loadSnapshot('/frame/last',new AbortController().signal);
  await waitFor(()=>expect(decoders).toHaveLength(1));
  decoders[0]();URL.revokeObjectURL(await last);
  expect(fetch).toHaveBeenCalledTimes(5);
});
