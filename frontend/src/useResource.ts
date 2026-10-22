import { useCallback, useEffect, useState } from 'react';
import { api } from './api';

export function useResource<T>(path: string, token: string, intervalMs = 30_000) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const reload = useCallback(async () => {
    try {
      const result = await api<T>(path, token);
      setData(result);
      setError('');
      setUpdatedAt(new Date());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'No se pudieron cargar los datos.');
    } finally { setLoading(false); }
  }, [path, token]);
  useEffect(() => {
    let active = true;
    const refresh = async () => { if (active) await reload(); };
    void refresh();
    const timer = intervalMs > 0 ? window.setInterval(refresh, intervalMs) : 0;
    return () => { active = false; window.clearInterval(timer); };
  }, [reload, intervalMs]);
  return { data, loading, error, updatedAt, reload };
}
