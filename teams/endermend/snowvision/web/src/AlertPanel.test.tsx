// @vitest-environment jsdom
import React from "react";
import { cleanup, render, screen, fireEvent, act } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AlertPanel } from "./AlertPanel";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("separates training events and exposes zero coverage", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () =>
            url === "/api/alerts"
              ? [
                  {
                    id: "demo",
                    name: "Синтетическая камера",
                    is_demo: true,
                    stage: "early",
                    opened_at: 1,
                    last_seen: 1,
                    score: 0.8,
                    confidence: 0.6,
                  },
                ]
              : { onset_online: 0, onset_capable: 0, real_cameras: 22 },
        }),
      ),
  );
  render(<AlertPanel />);
  await screen.findByText(/0 из 22 камер/);
  expect(screen.queryByText("Синтетическая камера")).toBeNull();
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Показать тренировочные сигналы" }),
  );
  expect(screen.getByText("Синтетическая камера")).toBeDefined();
});

it("does not roll back acknowledgement or resurrect ended events on delayed responses", async () => {
  const now = Date.now() / 1000;
  const event = {id:"street", camera_id:"street", name:"Камера улицы", is_demo:false,
    stage:"early", opened_at:now, last_seen:now, score:.8, confidence:.7,
    acknowledged_at:null, ended_at:null, evidence_stale:false};
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url:string) => Promise.resolve({ok:true,
    json: async () => url.endsWith("/acknowledge") ? {...event, acknowledged_at:now+1} :
      url === "/api/alerts" ? [event] : {onset_online:1,onset_capable:1,real_cameras:22}})));
  render(<AlertPanel/>);
  fireEvent.click(await screen.findByRole("button", {name:"Принять сигнал"}));
  await screen.findByRole("button", {name:"Принято оператором"});
  act(() => window.dispatchEvent(new CustomEvent("snowvision-alerts", {detail:[event]})));
  expect(screen.getByRole("button", {name:"Принято оператором"})).toBeDefined();
  act(() => window.dispatchEvent(new CustomEvent("snowvision-alerts", {detail:[{...event,ended_at:now+2,stage:"ended"}]})));
  act(() => window.dispatchEvent(new CustomEvent("snowvision-alerts", {detail:[event]})));
  expect(screen.queryByRole("button", {name:"Принять сигнал"})).toBeNull();
  expect(screen.getByText(/Камера улицы · завершён/)).toBeDefined();
});

it("shows a rain discrepancy alongside a current camera alert", async () => {
  const now = Date.now() / 1000;
  const weather = {status:"available", precipitation:"rain", temperature_c:2, observed_at:now};
  const event = {id:"street", camera_id:"street", name:"Чехова", is_demo:false,
    stage:"early", opened_at:now, last_seen:now, score:.8, confidence:.7,
    acknowledged_at:null, ended_at:null, evidence_stale:false, weather_context:weather};
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url:string) => Promise.resolve({ok:true,
    json: async () => url === "/api/alerts" ? [event] : {
      onset_online:1, onset_capable:1, real_cameras:22, yandex_weather:weather,
    }})));
  render(<AlertPanel/>);
  expect(await screen.findByText(/Яндекс показывает: дождь.*Проверьте/)).toBeDefined();
  expect(screen.getByText("Первые признаки · нужна проверка")).toBeDefined();
  expect(await screen.findByRole("link", {name:"Яндекс Погода"})).toBeDefined();
});

it("notifies once for a new signal and its confirmation, and skips historical signals", async () => {
  const deliver = vi.fn(function (_title: string, _options?: NotificationOptions) {});
  vi.stubGlobal("Notification", Object.assign(deliver, {
    permission: "granted", requestPermission: vi.fn().mockResolvedValue("granted"),
  }));
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url:string) => Promise.resolve({ok:true,
    json:async () => url === "/api/alerts" ? [] : {onset_online:1,onset_capable:1,real_cameras:22}})));
  render(<AlertPanel/>);
  await screen.findByText(/1 из 22 камер/);
  fireEvent.click(screen.getByRole("button", {name:"Включить уведомления"}));
  await screen.findByRole("button", {name:"Уведомления включены"});
  const now = Date.now()/1000;
  const event = {id:"new",camera_id:"street",name:"Новая камера",is_demo:false,
    stage:"early",opened_at:now+1,last_seen:now+1,score:.8,confidence:.7,
    acknowledged_at:null,ended_at:null,evidence_stale:false};
  const emit = (rows:object[]) => act(() => window.dispatchEvent(new CustomEvent("snowvision-alerts", {detail:rows})));
  emit([{...event,id:"historical",opened_at:now-60}]);
  expect(deliver).toHaveBeenCalledTimes(0);
  emit([event]); emit([event]);
  expect(deliver).toHaveBeenCalledTimes(1);
  const confirmed = {...event,stage:"confirmed",last_seen:now+2,confirmed_at:now+2};
  emit([confirmed]); emit([confirmed]);
  expect(deliver).toHaveBeenCalledTimes(2);
  expect(deliver.mock.calls[0][0]).toBe("Первые признаки снегопада");
  expect(deliver.mock.calls[1][0]).toBe("Снегопад подтверждается");
});
