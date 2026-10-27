// Tiny fetch wrapper. Every call goes to the same-origin /api (proxied to the backend in
// dev, served by FastAPI in prod). Errors always come back as { error } so the UI can
// humanise them rather than throwing raw responses at the user.

export type ApiResult<T> = T & { error?: string };

async function request<T = any>(path: string, init?: RequestInit): Promise<ApiResult<T>> {
  let res: Response;
  const requestId=crypto.randomUUID();
  window.dispatchEvent(new CustomEvent("ace-api-start",{detail:requestId}));
  try {
    res = await fetch(`/api${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    return { error: "network error — is the HypoLab engine running?" } as ApiResult<T>;
  } finally {
    window.dispatchEvent(new CustomEvent("ace-api-end",{detail:requestId}));
  }
  const text = await res.text();
  if (!text) return (res.ok ? {} : { error: `server error ${res.status}` }) as ApiResult<T>;
  try {
    const data = JSON.parse(text);
    if (!res.ok) return { ...data, error: data.error || data.detail || `server error ${res.status}` } as ApiResult<T>;
    return data as ApiResult<T>;
  } catch {
    return { error: `server error ${res.status}` } as ApiResult<T>;
  }
}

async function upload<T = any>(path: string, form: FormData): Promise<ApiResult<T>> {
  let res: Response;
  const requestId=crypto.randomUUID();
  window.dispatchEvent(new CustomEvent("ace-api-start",{detail:requestId}));
  try { res = await fetch(`/api${path}`, { method: "POST", body: form }); }
  catch { return { error: "network error — is the HypoLab engine running?" } as ApiResult<T>; }
  finally { window.dispatchEvent(new CustomEvent("ace-api-end",{detail:requestId})); }
  const text = await res.text();
  if (!text) return (res.ok ? {} : { error: `server error ${res.status}` }) as ApiResult<T>;
  try { const data=JSON.parse(text); return (res.ok?data:{...data,error:data.error||data.detail||`server error ${res.status}`}) as ApiResult<T>; } catch { return { error: text.slice(0, 200) } as ApiResult<T>; }
}

// Any request that starts a background job wakes the header's job tray immediately.
function announceJob<T>(r: ApiResult<T>): ApiResult<T> {
  const id = (r as any)?.job_id;
  if (id) window.dispatchEvent(new CustomEvent("ace-job-started", { detail: id }));
  return r;
}

export const api = {
  get: <T = any>(path: string) => request<T>(path),
  post: <T = any>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }).then(announceJob),
  del: <T = any>(path: string) => request<T>(path, { method: "DELETE" }),
  upload,
};

