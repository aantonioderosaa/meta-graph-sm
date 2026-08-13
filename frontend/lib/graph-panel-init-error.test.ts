/**
 * @vitest-environment jsdom
 *
 * GraphPanel WebGL init-error overlay: mock InteractiveNvlWrapper to fire
 * onInitializationError on mount (no real canvas).
 */

import { createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/dynamic", () => ({
  default: () =>
    function MockInteractiveNvlWrapper(props: {
      onInitializationError?: (error: unknown) => void;
    }) {
      queueMicrotask(() => {
        props.onInitializationError?.(
          new Error("Could not create shader object"),
        );
      });
      return null;
    },
}));

import { GraphInitErrorOverlay, GraphPanel } from "@/components/GraphPanel";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function render(element: ReturnType<typeof createElement>): {
  container: HTMLDivElement;
  root: Root;
} {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(element);
  });
  return { container, root };
}

async function waitForText(
  container: HTMLElement,
  text: string,
  tries = 40,
): Promise<void> {
  for (let i = 0; i < tries; i += 1) {
    if (container.textContent?.includes(text)) return;
    await act(async () => {
      await Promise.resolve();
    });
  }
  throw new Error(`Never found "${text}". Got: ${container.textContent ?? ""}`);
}

describe("GraphInitErrorOverlay", () => {
  let root: Root | undefined;
  let container: HTMLDivElement | undefined;

  afterEach(() => {
    act(() => {
      root?.unmount();
    });
    container?.remove();
  });

  it("renders the Italian banner and Riprova button", () => {
    const mounted = render(
      createElement(GraphInitErrorOverlay, { onRetry: () => undefined }),
    );
    root = mounted.root;
    container = mounted.container;
    expect(container.textContent).toContain(
      "Impossibile inizializzare la visualizzazione.",
    );
    expect(container.textContent).toContain(
      "Il browser potrebbe aver esaurito i contesti grafici disponibili",
    );
    expect(container.textContent).toContain("Riprova");
  });
});

describe("GraphPanel onInitializationError", () => {
  let root: Root | undefined;
  let container: HTMLDivElement | undefined;

  afterEach(() => {
    act(() => {
      root?.unmount();
    });
    container?.remove();
  });

  it("shows the init-error banner when NVL fails to initialize", async () => {
    const mounted = render(
      createElement(GraphPanel, {
        title: "Entità",
        fetcher: async () => ({
          nodes: [
            { id: "alice", caption: "Alice", properties: { type: "entity" } },
          ],
          relationships: [],
        }),
      }),
    );
    root = mounted.root;
    container = mounted.container;

    await waitForText(
      container,
      "Impossibile inizializzare la visualizzazione.",
    );
    expect(container.textContent).toContain("Riprova");
  });
});
