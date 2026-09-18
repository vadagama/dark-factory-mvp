import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAsync } from "./hooks";
import { ApiError } from "./client";

describe("useAsync", () => {
  it("resolves data and turns loading off", async () => {
    const fetcher = vi.fn(async () => ({ value: 42 }));
    const { result } = renderHook(() => useAsync(fetcher, []));
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ value: 42 });
    expect(result.current.error).toBeNull();
  });

  it("keeps ApiError instances as-is", async () => {
    const fetcher = vi.fn(async () => {
      throw new ApiError(403, { title: "Forbidden", detail: "no scope" }, "403");
    });
    const { result } = renderHook(() => useAsync(fetcher, []));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.error?.status).toBe(403);
    expect(result.current.error?.detail).toBe("no scope");
  });

  it("wraps non-ApiError failures into ApiError with status 0", async () => {
    const fetcher = vi.fn(async () => {
      throw new Error("boom");
    });
    const { result } = renderHook(() => useAsync(fetcher, []));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.error?.status).toBe(0);
    expect(result.current.error?.detail).toBe("Error: boom");
  });

  it("refetches on reload()", async () => {
    const fetcher = vi.fn(async () => ({ calls: 0 }));
    const { result } = renderHook(() => useAsync(fetcher, []));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      result.current.reload();
    });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(result.current.data).toEqual({ calls: 0 });
  });
});

describe("useAsync polling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    // Restore a plain "visible" state in case a test left an override behind.
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    vi.useRealTimers();
  });

  /** Settles the in-flight fetch without moving the fake clock. */
  const flushFetch = async (): Promise<void> => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
      await Promise.resolve();
    });
  };

  it("polls on the interval: silent in-place updates stamped with updatedAt", async () => {
    let tick = 0;
    const fetcher = vi.fn(async () => ({ tick: ++tick }));
    const { result } = renderHook(() => useAsync(fetcher, [], { pollMs: 1_000 }));
    expect(result.current.loading).toBe(true);
    await flushFetch(); // call 1: initial load succeeds
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toEqual({ tick: 1 });
    expect(result.current.updatedAt).not.toBeNull();
    expect(result.current.updatedAt).toBeGreaterThan(0);
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(2); // call 2: first background tick
    expect(result.current.loading).toBe(false); // silent refresh
    expect(result.current.data).toEqual({ tick: 2 });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(3); // call 3: second background tick
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toEqual({ tick: 3 });
  });

  it("keeps data and error on a background failure and re-arms after one interval", async () => {
    let failing = false;
    let tick = 0;
    const fetcher = vi.fn(async () => {
      if (failing) {
        throw new Error("boom");
      }
      return { tick: ++tick };
    });
    const { result } = renderHook(() => useAsync(fetcher, [], { pollMs: 1_000 }));
    await flushFetch(); // call 1: success (tick 1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    }); // call 2: success (tick 2)
    expect(result.current.data).toEqual({ tick: 2 });
    expect(result.current.error).toBeNull();

    failing = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    }); // call 3: background failure
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toEqual({ tick: 2 }); // last good data kept
    expect(result.current.error).toBeNull(); // error untouched by a background failure

    // The chain is re-planned after exactly one interval.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(999);
    });
    expect(fetcher).toHaveBeenCalledTimes(3);

    failing = false;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetcher).toHaveBeenCalledTimes(4); // call 4: next success restores data
    expect(result.current.data).toEqual({ tick: 3 });
    expect(result.current.error).toBeNull();
  });

  it("does not poll after a failed initial load until reload()", async () => {
    let failing = true;
    let tick = 0;
    const fetcher = vi.fn(async () => {
      if (failing) {
        throw new Error("boom");
      }
      return { tick: ++tick };
    });
    const { result } = renderHook(() => useAsync(fetcher, [], { pollMs: 1_000 }));
    await flushFetch(); // call 1: initial load fails
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.data).toBeNull();
    expect(result.current.updatedAt).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(1); // no retry storm over a dead API

    // reload() starts a fresh cycle; its success re-arms the polling chain.
    failing = false;
    await act(async () => {
      result.current.reload();
    });
    await flushFetch(); // call 2: success
    expect(result.current.error).toBeNull();
    expect(result.current.data).toEqual({ tick: 1 });
    expect(result.current.updatedAt).not.toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(3); // call 3: polling resumed
    expect(result.current.data).toEqual({ tick: 2 });
  });

  it("pauses while the tab is hidden and refreshes immediately on return", async () => {
    let tick = 0;
    const fetcher = vi.fn(async () => ({ tick: ++tick }));
    const { result } = renderHook(() => useAsync(fetcher, [], { pollMs: 1_000 }));
    await flushFetch(); // call 1: initial load
    expect(fetcher).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(1); // nothing runs while hidden

    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    await flushFetch();
    expect(fetcher).toHaveBeenCalledTimes(2); // immediate refresh, no interval wait
    expect(result.current.data).toEqual({ tick: 2 });

    // The chain resumes from the base interval after the resume.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("performs exactly one immediate refetch on reload() while polling", async () => {
    let tick = 0;
    const fetcher = vi.fn(async () => ({ tick: ++tick }));
    const { result } = renderHook(() => useAsync(fetcher, [], { pollMs: 1_000 }));
    await flushFetch(); // call 1: initial load, chain armed
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      result.current.reload();
    });
    await flushFetch();
    expect(fetcher).toHaveBeenCalledTimes(2); // one immediate refetch, pending timer cleared
    expect(result.current.data).toEqual({ tick: 2 });

    // The loop continues at the normal rhythm instead of doubling.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(result.current.data).toEqual({ tick: 3 });
  });

  it("stops polling after unmount", async () => {
    let tick = 0;
    const fetcher = vi.fn(async () => ({ tick: ++tick }));
    const { unmount } = renderHook(() => useAsync(fetcher, [], { pollMs: 1_000 }));
    await flushFetch(); // call 1
    expect(fetcher).toHaveBeenCalledTimes(1);

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(1); // no dangling timers after unmount
  });

  it("does not poll when polling is disabled", async () => {
    let tick = 0;
    const fetcher = vi.fn(async () => ({ tick: ++tick }));

    const noOptions = renderHook(() => useAsync(fetcher, []));
    await flushFetch(); // call 1
    expect(noOptions.result.current.data).toEqual({ tick: 1 });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(1); // no timers scheduled without options

    const zeroInterval = renderHook(() => useAsync(fetcher, [], { pollMs: 0 }));
    await flushFetch(); // call 2
    expect(zeroInterval.result.current.data).toEqual({ tick: 2 });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(2); // pollMs: 0 also disables polling
  });
});
