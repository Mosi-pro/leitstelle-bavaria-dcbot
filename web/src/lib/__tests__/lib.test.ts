import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, qs, setCsrfToken } from "../api";
import { fmtHours, toDate, tone } from "../format";

describe("format", () => {
  it("interpretiert Serverzeiten als UTC", () => {
    expect(toDate("2026-01-01T12:00:00")!.toISOString()).toBe("2026-01-01T12:00:00.000Z");
  });
  it("formatiert Dauer", () => {
    expect(fmtHours(0.5)).toBe("30 Min.");
    expect(fmtHours(null)).toBe("–");
    expect(fmtHours(72)).toBe("3 Tage");
  });
  it("ordnet Status Tonalitäten zu", () => {
    expect(tone("fixed")).toBe("ok");
    expect(tone("failed")).toBe("danger");
  });
  it("baut Query-Strings ohne leere Werte", () => {
    expect(qs({ a: "1", b: "", c: undefined, d: 0 })).toBe("?a=1&d=0");
  });
});

describe("api", () => {
  afterEach(() => vi.restoreAllMocks());
  it("sendet CSRF-Header bei POST, nicht bei GET", async () => {
    const calls: RequestInit[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
      calls.push(init);
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
    }));
    setCsrfToken("tok");
    await api.get("/api/x");
    await api.post("/api/x", { a: 1 });
    expect((calls[0].headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
    expect((calls[1].headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok");
  });
  it("liefert verständliche Fehlermeldungen", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Keine Berechtigung" }), { status: 403, headers: { "content-type": "application/json" } })));
    await expect(api.get("/api/y")).rejects.toEqual(new ApiError(403, "Keine Berechtigung"));
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    await expect(api.get("/api/y")).rejects.toMatchObject({ status: 0 });
  });
});
