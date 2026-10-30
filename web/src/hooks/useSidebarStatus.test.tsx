// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProfileContext } from "@/contexts/profile-context";
import { ProfileProvider } from "@/contexts/ProfileProvider";
import { api } from "@/lib/api";
import type { StatusResponse } from "@/lib/api";
import { useSidebarStatus } from "./useSidebarStatus";

vi.mock("@/lib/api", () => ({
  api: { getStatus: vi.fn(), getProfiles: vi.fn(), getActiveProfile: vi.fn() },
  setManagementProfile: vi.fn(),
}));

let container: HTMLDivElement;
let root: Root;
function Probe() {
  const status = useSidebarStatus();
  return <output>{status ? status.gateway_state : "unavailable"}</output>;
}
function scoped(profile: string) {
  return <ProfileContext.Provider value={{ profile, currentProfile: "default", profiles: ["default", "ito"], setProfile: () => {} }}><Probe /></ProfileContext.Provider>;
}
async function render(value: ReactNode) { await act(async () => { root.render(value); }); }
function deferred() {
  let resolve!: (value: StatusResponse) => void;
  const promise = new Promise<StatusResponse>(yes => { resolve = yes; });
  return { promise, resolve };
}
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.useFakeTimers();
  vi.mocked(api.getStatus).mockReset();
  vi.mocked(api.getProfiles).mockResolvedValue({ profiles: [] } as unknown as Awaited<ReturnType<typeof api.getProfiles>>);
  vi.mocked(api.getActiveProfile).mockResolvedValue({ active: "default", current: "default" });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("sidebar profile hook", () => {
  it("reads the selected profile under the real provider on initial render", async () => {
    vi.mocked(api.getStatus).mockResolvedValue({ gateway_state: "ito" } as StatusResponse);
    await render(<MemoryRouter initialEntries={["/sessions?profile=ito"]}><ProfileProvider><Probe /></ProfileProvider></MemoryRouter>);
    expect(api.getStatus).toHaveBeenCalledWith("ito");
    expect(container.textContent).toBe("ito");
  });

  it("hides old scope immediately and ignores an old response after switching", async () => {
    const oldRefresh = deferred(), ito = deferred();
    vi.mocked(api.getStatus).mockResolvedValueOnce({ gateway_state: "default" } as StatusResponse)
      .mockReturnValueOnce(oldRefresh.promise).mockReturnValueOnce(ito.promise);
    await render(scoped(""));
    expect(container.textContent).toBe("default");
    await act(async () => { vi.advanceTimersByTime(10000); });
    await render(scoped("ito"));
    expect(container.textContent).toBe("unavailable");
    await act(async () => ito.resolve({ gateway_state: "ito" } as StatusResponse));
    expect(container.textContent).toBe("ito");
    await act(async () => oldRefresh.resolve({ gateway_state: "default" } as StatusResponse));
    expect(container.textContent).toBe("ito");
  });

  it("discards the previous lifetime when returning to a profile before replacement reads finish", async () => {
    const buyer = deferred(), deskAgain = deferred();
    vi.mocked(api.getStatus).mockResolvedValueOnce({ gateway_state: "desk-first" } as StatusResponse)
      .mockReturnValueOnce(buyer.promise).mockReturnValueOnce(deskAgain.promise);
    await render(scoped("desk"));
    expect(container.textContent).toBe("desk-first");
    await render(scoped("buyer"));
    expect(container.textContent).toBe("unavailable");
    await render(scoped("desk"));
    expect(container.textContent).toBe("unavailable");
    await act(async () => buyer.resolve({ gateway_state: "buyer" } as StatusResponse));
    expect(container.textContent).toBe("unavailable");
    await act(async () => deskAgain.resolve({ gateway_state: "desk-fresh" } as StatusResponse));
    expect(container.textContent).toBe("desk-fresh");
  });

  it("coalesces timer ticks, clears failed reads, and removes its timer on unmount", async () => {
    const pending = deferred();
    vi.mocked(api.getStatus).mockReturnValueOnce(pending.promise).mockRejectedValueOnce(new Error("offline"));
    await render(scoped("ito"));
    await act(async () => { vi.advanceTimersByTime(30000); });
    expect(api.getStatus).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve({ gateway_state: "ito" } as StatusResponse));
    await act(async () => { vi.advanceTimersByTime(10000); });
    expect(container.textContent).toBe("unavailable");
    await render(null);
    expect(vi.getTimerCount()).toBe(0);
  });
});
