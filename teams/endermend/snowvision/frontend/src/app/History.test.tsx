// @vitest-environment jsdom
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { History } from "./App";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("plots a validated model score only for its frame version", async () => {
  const scene = { format_valid: true, snow_probability: .4, snowing_probability: .7,
    ice_confidence: .5, image_confidence: .8 };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [
    { timestamp: 100, frame_version: "a", scene_frame_version: "a", scene, snow_probability: null },
    { timestamp: 101, frame_version: "b", scene_frame_version: "a", scene, snow_probability: null },
  ] }));
  const view = render(<History id="camera" />);
  await screen.findByText("1 оценок");
  expect(view.container.querySelectorAll("circle")).toHaveLength(1);
  expect(view.container.querySelectorAll("line")).toHaveLength(0);
});
