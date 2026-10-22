import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { Series } from './api';
import { useResource } from './useResource';

export default function Trend({ token, query, color }: { token: string; query: string; color: string }) {
  const resource = useResource<Series>(`/monitoring/series?query=${encodeURIComponent(query)}&minutes=60`, token, 60_000);
  const data = resource.data?.series[0]?.points.map(([time, value]) => ({ time, value })) || [];
  return <div className="trend">{resource.error && <p className="notice error" role="alert">{resource.error}</p>}
    {resource.loading && !resource.data ? <p className="muted">Cargando histórico…</p> : data.length ?
      <ResponsiveContainer width="100%" height={182}><LineChart data={data} margin={{ top: 10, right: 8, bottom: 0, left: -24 }}><CartesianGrid stroke="#425660" strokeDasharray="2 5" vertical={false} /><XAxis dataKey="time" tickFormatter={t => new Date(t * 1000).toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })} tick={{ fill: '#aebfc5', fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={30} /><YAxis domain={[0, 100]} tick={{ fill: '#aebfc5', fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={t => `${t}%`} /><Tooltip labelFormatter={t => new Date(Number(t) * 1000).toLocaleTimeString('es-MX')} formatter={v => [`${Number(v).toFixed(1)}%`, 'Valor']} contentStyle={{ background: '#192833', border: '1px solid #41525c', borderRadius: 8, color: '#eef4f3' }} /><Line type="monotone" dataKey="value" stroke={color} strokeWidth={2.5} dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer> : <div className="empty"><strong>Sin histórico disponible</strong><span>{resource.data?.error || 'Prometheus aún no tiene muestras para la última hora.'}</span></div>}
  </div>;
}
