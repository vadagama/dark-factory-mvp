import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
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
