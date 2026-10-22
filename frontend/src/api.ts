export type Role = 'viewer' | 'operator' | 'admin';
export type User = { username: string; role: Role; name?: string };
export type Device = {
  id: string; hostname: string; port: number; driver: string; protocol?: string;
  type: string; group?: string | null; tags: string[]; description?: string;
};
export type DeviceHealth = Record<string, { online: boolean; latency_ms: number | null; rx?: string; tx?: string }>;
export type Alert = {
  name: string; state: string; severity: string; device_id: string;
  summary: string; description: string; active_at: string;
};
export type AlertResponse = { status: string; total: number; firing: number; alerts: Alert[]; detail?: string };
export type Monitoring = {
  services: Record<string, { url: string; up: boolean }>;
  metrics: Record<'host_cpu' | 'host_mem' | 'host_disk' | 'devices_up' | 'devices_total', number | null>;
  targets: { job: string; url: string; health: string }[];
  links: { grafana_dashboard: string; prometheus: string; loki: string };
};
export type Series = { series: { label: string; points: [number, number][] }[]; error?: string };

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers },
  });
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('netpulse:unauthorized'));
    let message = `Solicitud fallida (HTTP ${response.status})`;
    try {
      const body = await response.json() as { detail?: string | { msg: string }[] };
      if (typeof body.detail === 'string') message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map(e => e.msg).join('; ');
    } catch { /* respuesta sin JSON */ }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const endpoints = {
  devices: '/devices',
  health: '/devices/health',
  alerts: '/alerts/active',
  monitoring: '/monitoring/overview',
  users: '/admin/users',
} as const;
