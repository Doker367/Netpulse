import { StrictMode, Suspense, lazy, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity, ArrowDownRight, ArrowRight, Bell, ChevronDown, CircleAlert, Clock3,
  Command, ExternalLink, HardDrive, LayoutDashboard, LifeBuoy, LockKeyhole, LogOut,
  Menu, Monitor, Network, RefreshCw, Search, Server, Settings2, ShieldCheck,
  TerminalSquare, Users, Wifi, X,
} from 'lucide-react';
import { api, type AlertResponse, type Device, type DeviceHealth, type Monitoring, type Role, type User } from './api';
import { useResource } from './useResource';
import './style.css';

const Trend = lazy(() => import('./Trend'));

type View = 'overview' | 'devices' | 'monitoring' | 'alerts' | 'users';
const titles: Record<View, string> = {
  overview: 'Centro de operaciones', devices: 'Inventario de equipos', monitoring: 'Monitoreo',
  alerts: 'Alertas activas', users: 'Usuarios y roles',
};
const items: { id: View; label: string; icon: typeof Activity; admin?: boolean }[] = [
  { id: 'overview', label: 'Vista general', icon: LayoutDashboard },
  { id: 'devices', label: 'Equipos', icon: Network },
  { id: 'monitoring', label: 'Monitoreo', icon: Activity },
  { id: 'alerts', label: 'Alertas', icon: Bell },
  { id: 'users', label: 'Usuarios', icon: Users, admin: true },
];
const more = [
  { label: 'Topología', icon: Wifi }, { label: 'Comandos y consola', icon: TerminalSquare },
  { label: 'Backups', icon: HardDrive }, { label: 'Cumplimiento', icon: ShieldCheck },
  { label: 'Otras herramientas', icon: Settings2 },
];
const readView = (): View => {
  const value = new URLSearchParams(location.search).get('view');
  return items.some(item => item.id === value) ? value as View : 'overview';
};
const errorText = (error: unknown) => error instanceof Error ? error.message : 'Ocurrió un error. Inténtalo otra vez.';
const metric = (n?: number | null, suffix = '') => n == null || !Number.isFinite(n) ? '—' : `${Math.round(n * 10) / 10}${suffix}`;
const hour = (d: Date | null) => d ? d.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' }) : '—';

function Panel({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}>{children}</section>;
}
function Notice({ error, retry }: { error: string; retry?: () => void }) {
  return error ? <div className="notice error" role="alert"><CircleAlert size={17} />{error}{retry && <button className="text-button" onClick={retry}>Reintentar</button>}</div> : null;
}
function Empty({ title, detail }: { title: string; detail: string }) {
  return <div className="empty"><div className="empty-icon"><LifeBuoy size={23} /></div><strong>{title}</strong><span>{detail}</span></div>;
}
function Status({ online, label }: { online: boolean; label: string }) {
  return <span className={`status ${online ? 'positive' : 'negative'}`}><span className="status-dot" />{label}</span>;
}
function SectionHeading({ title, sub, action }: { title: string; sub?: string; action?: ReactNode }) {
  return <div className="section-heading"><div><h2>{title}</h2>{sub && <p>{sub}</p>}</div>{action}</div>;
}
function Login({ onLogin }: { onLogin: (token: string, user: User) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); setBusy(true); setError('');
    try {
      const { access_token } = await api<{ access_token: string }>('/auth/login', '', {
        method: 'POST', body: JSON.stringify({ username, password }),
      });
      const user = await api<User>('/auth/me', access_token);
      setPassword(''); onLogin(access_token, user);
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }
  return <div className="login-wrap"><div className="login-brand"><span className="brand-symbol"><Activity size={27} /></span><strong>NETPULSE<span> / NOC</span></strong></div>
    <div className="login-grid"><div className="login-story"><span className="line-label">OPERACIONES DE RED</span><h1>El estado de tu red.<br /><em>Sin perder de vista lo importante.</em></h1><p>Una vista para detectar, investigar y actuar. El motor de NetPulse continúa detrás de cada operación.</p><div className="login-stripe"><span className="stripe-line" /><span>Inventario · Alertas · Telemetría</span></div></div>
      <form className="login-card" onSubmit={submit}><span className="lock-icon"><LockKeyhole size={21} /></span><h2>Acceder al NOC</h2><p>Inicia sesión con tu cuenta de NetPulse.</p>
        <label htmlFor="username">Usuario</label><input id="username" autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required placeholder="Tu usuario" />
        <label htmlFor="password">Contraseña</label><input id="password" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required placeholder="Tu contraseña" />
        <Notice error={error} /><button className="button primary full" disabled={busy}>{busy ? 'Verificando…' : <>Ingresar <ArrowRight size={17} /></>}</button>
        <p className="login-foot">¿Necesitas la interfaz anterior? <a href="/legacy">Abrir panel clásico <ExternalLink size={12} /></a></p>
      </form></div><div className="login-bottom">NETPULSE NOC <span>Interfaz React · motor FastAPI</span></div></div>;
}

function Overview({ token, go }: { token: string; go: (view: View) => void }) {
  const devices = useResource<Device[]>('/devices', token);
  const health = useResource<DeviceHealth>('/devices/health', token);
  const alerts = useResource<AlertResponse>('/alerts/active', token);
  const mon = useResource<Monitoring>('/monitoring/overview', token);
  const all = devices.data || [];
  const down = all.filter(d => health.data?.[d.id]?.online === false);
  const up = all.filter(d => health.data?.[d.id]?.online === true);
  const firing = (alerts.data?.alerts || []).filter(a => a.state === 'firing');
  const alertsUnavailable = Boolean(alerts.error || alerts.data?.status === 'error');
  const serviceItems = Object.entries(mon.data?.services || {});
  const loading = devices.loading || health.loading;
  return <div className="page-stack">
    <div className="intro"><div><h1>La red, en perspectiva.</h1><p>Prioriza los problemas reales y entra al detalle cuando lo necesites.</p></div><span className="updated"><Clock3 size={15} /> Actualizado {hour(devices.updatedAt)}</span></div>
    <div className="summary-rail" aria-label="Resumen de estado">
      <div><span>Equipos en línea</span><strong>{loading || health.error ? '—' : `${up.length} / ${all.length}`}</strong><small>{health.error ? 'No se pudo verificar conectividad' : 'Inventario operativo'}</small></div>
      <div><span>Equipos fuera de línea</span><strong className={down.length ? 'danger-text' : ''}>{loading || health.error ? '—' : down.length}</strong><small>{health.error ? 'Estado desconocido' : down.length ? down.map(d => d.id).join(', ') : 'Sin desconexiones detectadas'}</small></div>
      <div><span>Alertas activas</span><strong className={firing.length ? 'danger-text' : ''}>{alerts.loading || alertsUnavailable ? '—' : firing.length}</strong><small>{alertsUnavailable ? 'Fuente no disponible' : 'En estado firing'}</small></div>
      <div><span>Servicios disponibles</span><strong>{mon.loading || !mon.data || mon.error ? '—' : `${serviceItems.filter(([, s]) => s.up).length} / ${serviceItems.length}`}</strong><small>Stack de observabilidad</small></div>
    </div>
    <div className="overview-columns">
      <Panel className="priority"><SectionHeading title="Atención prioritaria" sub="Alertas y equipos que necesitan una revisión." action={<button className="text-button" onClick={() => go('alerts')}>Ver alertas <ArrowRight size={16} /></button>} />
        <Notice error={alerts.error || (alerts.data?.status === 'error' ? 'No se pudo consultar Prometheus. Revisa el servicio y vuelve a intentar.' : '')} retry={alerts.reload} />
        {alerts.loading && !alerts.data ? <p className="muted">Consultando alertas…</p> : firing.length || down.length ? <div className="incident-list">
          {firing.slice(0, 4).map((a, i) => <div className="incident" key={`${a.name}-${a.device_id}-${i}`}><span className="incident-icon"><Bell size={16} /></span><div><strong>{a.name}</strong><p>{a.summary || a.description || 'Alerta activa de Prometheus'}</p><small>{a.device_id || 'Infraestructura'} · {a.severity || 'sin severidad'}</small></div><span className="status negative"><span className="status-dot" />Activa</span></div>)}
          {down.slice(0, 4).map(d => <div className="incident" key={d.id}><span className="incident-icon"><Server size={16} /></span><div><strong>{d.id}</strong><p>No responde en {d.hostname}:{d.port}</p><small>Equipo · {d.group || d.type}</small></div><span className="status negative"><span className="status-dot" />Sin conexión</span></div>)}
        </div> : <Empty title={alertsUnavailable ? 'Alertas no verificadas' : 'Sin incidentes prioritarios'} detail={alertsUnavailable ? 'La fuente de alertas no respondió. No asumas que todo está bien.' : 'No hay alertas en firing ni equipos fuera de línea en este momento.'} />}</Panel>
      <Panel className="service-panel"><SectionHeading title="Servicios" sub="Salud del sistema de observabilidad." action={<button className="icon-button" onClick={mon.reload} aria-label="Actualizar servicios"><RefreshCw size={17} /></button>} />
        <Notice error={mon.error} retry={mon.reload} />
        {mon.loading && !mon.data ? <p className="muted">Consultando servicios…</p> : serviceItems.length ? <div className="service-list">{serviceItems.map(([name, service]) => <div className="service-row" key={name}><span className="service-glyph"><Activity size={17} /></span><div><strong>{name}</strong><small>{service.url.replace(/^https?:\/\//, '')}</small></div><Status online={service.up} label={service.up ? 'Disponible' : 'No disponible'} /></div>)}</div> : <Empty title="Sin datos de servicios" detail="Comprueba la conexión con el motor de monitoreo." />}</Panel>
    </div>
    <Panel><SectionHeading title="Inventario reciente" sub="Una lectura rápida de la infraestructura que gestiona NetPulse." action={<button className="text-button" onClick={() => go('devices')}>Abrir inventario <ArrowRight size={16} /></button>} />
      <Notice error={devices.error} retry={devices.reload} /><DeviceTable data={all.slice(0, 6)} health={health.data} compact /></Panel>
  </div>;
}

function DeviceTable({ data, health, compact = false, onEdit, onDelete }: { data: Device[]; health: DeviceHealth | null; compact?: boolean; onEdit?: (device: Device) => void; onDelete?: (device: Device) => void }) {
  if (!data.length) return <Empty title="No hay equipos en esta vista" detail="Prueba otro filtro o agrega un dispositivo al inventario." />;
  return <div className="table-scroll"><table><thead><tr><th>Equipo</th><th>Estado</th><th>Host / Puerto</th><th>Driver</th><th>Grupo</th>{!compact && onEdit && <th><span className="sr-only">Acciones</span></th>}</tr></thead><tbody>{data.map(d => <tr key={d.id}><td className="strong-cell"><span className="device-icon"><Server size={17} /></span><span>{d.id}<small>{d.description || d.type}</small></span></td><td>{!health?.[d.id] ? <span className="status neutral">Sin datos</span> : <Status online={health[d.id].online} label={health[d.id].online ? 'En línea' : 'Sin conexión'} />}</td><td className="mono">{d.hostname}:{d.port}</td><td>{d.driver.toUpperCase()}</td><td>{d.group || '—'}</td>{!compact && onEdit && <td className="actions"><button onClick={() => onEdit(d)} className="text-button">Editar</button><button onClick={() => onDelete?.(d)} className="text-button danger-text">Eliminar</button></td>}</tr>)}</tbody></table></div>;
}

function Devices({ token, role }: { token: string; role: Role }) {
  const devices = useResource<Device[]>('/devices', token);
  const health = useResource<DeviceHealth>('/devices/health', token);
  const [search, setSearch] = useState('');
  const [state, setState] = useState<'all' | 'online' | 'offline'>('all');
  const [editing, setEditing] = useState<Device | 'new' | null>(null);
  const [actionError, setActionError] = useState('');
  const [busy, setBusy] = useState(false);
  const list = (devices.data || []).filter(d => {
    const matches = `${d.id} ${d.hostname} ${d.group || ''} ${d.driver}`.toLowerCase().includes(search.toLowerCase());
    const online = health.data?.[d.id]?.online;
    return matches && (state === 'all' || (state === 'online' ? online === true : online === false));
  });
  const canEdit = role === 'admin';
  async function save(form: HTMLFormElement) {
    const fields = new FormData(form);
    const body = {
      hostname: String(fields.get('hostname') || ''), port: Number(fields.get('port')),
      driver: String(fields.get('driver') || 'ios'), type: String(fields.get('type') || 'router'),
      group: String(fields.get('group') || ''), description: String(fields.get('description') || ''),
      ...(fields.get('password') ? { password: String(fields.get('password')) } : {}),
    };
    setBusy(true); setActionError('');
    try {
      if (editing === 'new') await api('/devices', token, { method: 'POST', body: JSON.stringify({ ...body, id: String(fields.get('id')), username: String(fields.get('username') || '') }) });
      else if (editing) await api(`/devices/${encodeURIComponent(editing.id)}`, token, { method: 'PUT', body: JSON.stringify(body) });
      setEditing(null); await devices.reload(); await health.reload();
    } catch (reason) { setActionError(errorText(reason)); }
    finally { setBusy(false); }
  }
  async function remove(device: Device) {
    if (!window.confirm(`¿Eliminar ${device.id} del inventario? Esta acción no se puede deshacer.`)) return;
    setActionError('');
    try { await api(`/devices/${encodeURIComponent(device.id)}`, token, { method: 'DELETE' }); await devices.reload(); await health.reload(); }
    catch (reason) { setActionError(errorText(reason)); }
  }
  return <div className="page-stack"><div className="intro"><div><h1>Inventario de red</h1><p>Equipos, conectividad y contexto, en un solo lugar.</p></div>{canEdit && <button className="button primary" onClick={() => { setActionError(''); setEditing('new'); }}>Agregar equipo <ArrowRight size={16} /></button>}</div>
    <Panel><div className="toolbar"><label className="search-field"><Search size={18} /><span className="sr-only">Buscar equipos</span><input placeholder="Buscar equipo, host o grupo" value={search} onChange={e => setSearch(e.target.value)} /></label><label className="select-field"><span className="sr-only">Filtrar por estado</span><select value={state} onChange={e => setState(e.target.value as typeof state)}><option value="all">Todos los estados</option><option value="online">En línea</option><option value="offline">Sin conexión</option></select><ChevronDown size={15} /></label><button className="icon-button" onClick={() => { void devices.reload(); void health.reload(); }} aria-label="Actualizar inventario"><RefreshCw size={17} /></button></div>
      <div className="table-caption">{devices.loading ? 'Cargando equipos…' : `${list.length} de ${devices.data?.length || 0} equipos`}{devices.updatedAt && <> · Actualizado {hour(devices.updatedAt)}</>}</div><Notice error={devices.error || health.error || actionError} retry={devices.reload} /><DeviceTable data={list} health={health.data} onEdit={canEdit ? d => { setActionError(''); setEditing(d); } : undefined} onDelete={remove} /></Panel>
    {editing && <div className="dialog-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setEditing(null); }}><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="device-dialog-title"><div className="dialog-top"><h2 id="device-dialog-title">{editing === 'new' ? 'Agregar equipo' : `Editar ${editing.id}`}</h2><button className="icon-button" onClick={() => setEditing(null)} aria-label="Cerrar"><X size={19} /></button></div><form onSubmit={e => { e.preventDefault(); void save(e.currentTarget); }} className="field-grid">
      {editing === 'new' && <><label>ID del equipo<input name="id" required placeholder="router-core-01" /></label><label>Usuario SSH/API<input name="username" placeholder="admin" autoComplete="off" /></label></>}
      <label>Host o IP<input name="hostname" required defaultValue={editing === 'new' ? '' : editing.hostname} /></label><label>Puerto<input name="port" type="number" min="1" max="65535" required defaultValue={editing === 'new' ? 22 : editing.port} /></label>
      <label>Driver<select name="driver" defaultValue={editing === 'new' ? 'ios' : editing.driver}><option value="ios">Cisco IOS</option><option value="eos">Arista EOS</option><option value="junos">Juniper JunOS</option><option value="ros">MikroTik ROS</option><option value="nxos">Cisco NX-OS</option><option value="iosxr">Cisco IOS XR</option><option value="fortios">FortiOS</option><option value="snmp">SNMP</option><option value="generic">Genérico</option><option value="linux">Linux</option><option value="procurve">HP ProCurve</option><option value="comware">HP/H3C Comware</option><option value="hpe">HP Enterprise</option><option value="hp_procurve">HP ProCurve (nativo)</option><option value="hp_comware">HP Comware (nativo)</option><option value="huawei">Huawei</option><option value="alcatel_aos">Alcatel AOS</option><option value="alcatel_sros">Alcatel SROS</option><option value="h3c_comware">H3C Comware</option><option value="mikrotik">MikroTik</option></select></label>
      <label>Tipo<select name="type" defaultValue={editing === 'new' ? 'router' : editing.type}><option value="router">Router</option><option value="switch">Switch</option><option value="firewall">Firewall</option><option value="server">Servidor</option></select></label>
      <label>Grupo<input name="group" defaultValue={editing === 'new' ? '' : editing.group || ''} placeholder="Core" /></label><label>Contraseña del equipo <small>{editing === 'new' ? 'si corresponde' : 'dejar en blanco para conservar'}</small><input type="password" name="password" autoComplete="new-password" /></label>
      <label className="wide">Descripción<input name="description" defaultValue={editing === 'new' ? '' : editing.description || ''} /></label><Notice error={actionError} /><div className="dialog-actions wide"><button type="button" className="button subtle" onClick={() => setEditing(null)}>Cancelar</button><button className="button primary" disabled={busy}>{busy ? 'Guardando…' : 'Guardar equipo'}</button></div>
    </form></section></div>}
  </div>;
}

const chartQueries = [
  { label: 'CPU', key: 'host_cpu' as const, query: 'netpulse_host_cpu_percent', color: '#51c9af' },
  { label: 'Memoria', key: 'host_mem' as const, query: 'netpulse_host_memory_percent', color: '#8ba7eb' },
  { label: 'Disco', key: 'host_disk' as const, query: 'max(netpulse_host_disk_percent)', color: '#dfa86e' },
];
function MonitoringPage({ token }: { token: string }) {
  const resource = useResource<Monitoring>('/monitoring/overview', token);
  const mon = resource.data;
  return <div className="page-stack"><div className="intro"><div><h1>Monitoreo</h1><p>Telemetría del host y disponibilidad de servicios, con datos del motor actual.</p></div><button className="button subtle" onClick={resource.reload}><RefreshCw size={16} /> Actualizar</button></div>
    <Notice error={resource.error} retry={resource.reload} />
    <div className="metric-grid">{chartQueries.map(({ label, key, color }) => <Panel className="metric-panel" key={key}><span className="metric-name">Host / {label}</span><strong style={{ color }}>{metric(mon?.metrics?.[key], '%')}</strong><small>Valor actual · últimos 60 minutos</small><Suspense fallback={<p className="muted">Cargando gráfico…</p>}><Trend token={token} query={chartQueries.find(q => q.key === key)!.query} color={color} /></Suspense></Panel>)}<Panel className="metric-panel availability"><span className="metric-name">Equipos disponibles</span><strong>{mon?.metrics?.devices_up == null ? '—' : `${mon.metrics.devices_up} / ${mon.metrics.devices_total ?? '—'}`}</strong><small>Fuente: Prometheus</small><span className="availability-mark"><Network size={68} strokeWidth={1.2} /></span></Panel></div>
    <div className="overview-columns"><Panel><SectionHeading title="Servicios" sub="Comprobación de disponibilidad." /><div className="service-list">{Object.entries(mon?.services || {}).map(([name, s]) => <div className="service-row" key={name}><span className="service-glyph"><Activity size={17} /></span><div><strong>{name}</strong><small>{s.url}</small></div><Status online={s.up} label={s.up ? 'Disponible' : 'No disponible'} /></div>)}{!mon && <p className="muted">{resource.loading ? 'Consultando servicios…' : 'Sin datos de servicios.'}</p>}</div></Panel>
    <Panel><SectionHeading title="Targets de Prometheus" sub="Estado de las fuentes de telemetría." />{mon?.targets.length ? <div className="service-list">{mon.targets.map((target, index) => <div className="service-row" key={`${target.job}-${index}`}><span className="service-glyph"><Monitor size={17} /></span><div><strong>{target.job}</strong><small>{target.url}</small></div><Status online={target.health === 'up'} label={target.health} /></div>)}</div> : <Empty title="Sin targets" detail="Prometheus no ha devuelto fuentes activas." />}</Panel></div>
  </div>;
}
function AlertsPage({ token, go }: { token: string; go: (view: View) => void }) {
  const resource = useResource<AlertResponse>('/alerts/active', token);
  const [filter, setFilter] = useState<'all' | 'firing' | 'pending'>('all');
  const list = (resource.data?.alerts || []).filter(a => filter === 'all' || a.state === filter).sort((a, b) => Number(b.state === 'firing') - Number(a.state === 'firing'));
  return <div className="page-stack"><div className="intro"><div><h1>Alertas activas</h1><p>Eventos activos de Prometheus. Prioriza los que están en firing.</p></div><button className="button subtle" onClick={resource.reload}><RefreshCw size={16} /> Actualizar</button></div>
    <Panel><div className="toolbar"><div className="segmented" role="group" aria-label="Filtrar alertas">{(['all', 'firing', 'pending'] as const).map(s => <button key={s} className={filter === s ? 'selected' : ''} aria-pressed={filter === s} onClick={() => setFilter(s)}>{({ all: 'Todas', firing: 'Activas', pending: 'Pendientes' })[s]}</button>)}</div><span className="table-caption">{resource.data ? `${resource.data.firing} en firing · ${resource.data.total} totales` : 'Sin datos todavía'}</span></div>
      <Notice error={resource.error || (resource.data?.status === 'error' ? 'No se pudo consultar Prometheus. Revisa el servicio y vuelve a intentar.' : '')} retry={resource.reload} />
      {resource.loading && !resource.data ? <p className="muted">Consultando alertas…</p> : list.length ? <div className="alert-list">{list.map((a, index) => <article className="alert-row" key={`${a.name}-${a.device_id}-${index}`}><span className={`alert-mark ${a.state === 'firing' ? 'critical' : ''}`}><Bell size={18} /></span><div><div className="alert-title"><h3>{a.name}</h3><Status online={a.state !== 'firing'} label={a.state === 'firing' ? 'En firing' : a.state} /></div><p>{a.summary || a.description || 'Sin descripción disponible'}</p><div className="alert-meta"><span>{a.device_id || 'Infraestructura'}</span><span>{a.severity || 'warning'}</span>{a.active_at && <span>{new Date(a.active_at).toLocaleString('es-MX')}</span>}</div></div><button className="icon-button" aria-label={`Ver equipos relacionados con ${a.name}`} onClick={() => go('devices')}><ArrowDownRight size={18} /></button></article>)}</div> : <Empty title="No hay alertas con este filtro" detail={resource.error || resource.data?.status === 'error' ? 'Comprueba la fuente antes de asumir que no existen alertas.' : 'Las nuevas alertas aparecerán aquí automáticamente.'} />}</Panel>
  </div>;
}
function UsersPage({ token, self }: { token: string; self: string }) {
  const users = useResource<User[]>('/admin/users', token, 0);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<User | 'new' | null>(null);
  async function save(form: HTMLFormElement) {
    const fields = new FormData(form); const username = String(fields.get('username') || '');
    const body = { name: String(fields.get('name') || ''), role: String(fields.get('role') || 'viewer'), ...(fields.get('password') ? { password: String(fields.get('password')) } : {}) };
    setBusy(true); setError('');
    try {
      if (editing === 'new') await api('/admin/users', token, { method: 'POST', body: JSON.stringify({ ...body, username }) });
      else if (editing) await api(`/admin/users/${encodeURIComponent(editing.username)}`, token, { method: 'PUT', body: JSON.stringify(body) });
      setEditing(null); await users.reload();
    } catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }
  async function remove(user: User) {
    if (user.username === self || !window.confirm(`¿Eliminar al usuario ${user.username}?`)) return;
    setError('');
    try { await api(`/admin/users/${encodeURIComponent(user.username)}`, token, { method: 'DELETE' }); await users.reload(); }
    catch (cause) { setError(errorText(cause)); }
  }
  return <div className="page-stack"><div className="intro"><div><h1>Usuarios y roles</h1><p>Acceso al NOC controlado por los permisos existentes de FastAPI.</p></div><button className="button primary" onClick={() => { setError(''); setEditing('new'); }}>Crear usuario <ArrowRight size={16} /></button></div>
    <Panel><SectionHeading title="Cuentas registradas" sub="Los cambios se guardan en el backend; aquí nunca se muestran contraseñas." /><Notice error={users.error || error} retry={users.reload} />{users.loading && !users.data ? <p className="muted">Cargando usuarios…</p> : <div className="table-scroll"><table><thead><tr><th>Usuario</th><th>Nombre</th><th>Rol</th><th><span className="sr-only">Acciones</span></th></tr></thead><tbody>{(users.data || []).map(user => <tr key={user.username}><td className="strong-cell"><span className="avatar small">{user.username.slice(0, 2).toUpperCase()}</span>{user.username}</td><td>{user.name || '—'}</td><td><span className="role-tag">{user.role}</span></td><td className="actions"><button className="text-button" onClick={() => { setError(''); setEditing(user); }}>Editar</button>{user.username !== self && <button className="text-button danger-text" onClick={() => void remove(user)}>Eliminar</button>}</td></tr>)}</tbody></table></div>}</Panel>
    {editing && <div className="dialog-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setEditing(null); }}><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="user-dialog-title"><div className="dialog-top"><h2 id="user-dialog-title">{editing === 'new' ? 'Crear usuario' : `Editar ${editing.username}`}</h2><button className="icon-button" onClick={() => setEditing(null)} aria-label="Cerrar"><X size={19} /></button></div><form className="field-grid" onSubmit={e => { e.preventDefault(); void save(e.currentTarget); }}>
      {editing === 'new' && <label>Usuario<input name="username" minLength={3} required autoComplete="off" /></label>}
      <label>Nombre<input name="name" required defaultValue={editing === 'new' ? '' : editing.name || editing.username} /></label><label>Rol<select name="role" defaultValue={editing === 'new' ? 'viewer' : editing.role}><option value="viewer">Viewer</option><option value="operator">Operator</option><option value="admin">Admin</option></select></label><label className="wide">{editing === 'new' ? 'Contraseña' : 'Nueva contraseña (opcional)'}<input name="password" type="password" minLength={6} required={editing === 'new'} autoComplete="new-password" /></label><Notice error={error} /><div className="dialog-actions wide"><button type="button" className="button subtle" onClick={() => setEditing(null)}>Cancelar</button><button className="button primary" disabled={busy}>{busy ? 'Guardando…' : 'Guardar usuario'}</button></div>
    </form></section></div>}
  </div>;
}

function Workspace({ token, user, onLogout }: { token: string; user: User; onLogout: () => void }) {
  const [view, setView] = useState<View>(readView);
  const [sidebar, setSidebar] = useState(false);
  useEffect(() => { const handler = () => setView(readView()); window.addEventListener('popstate', handler); return () => window.removeEventListener('popstate', handler); }, []);
  const go = (next: View) => { setView(next); setSidebar(false); history.pushState(null, '', `${location.pathname === '/modern' ? '/modern' : '/'}${next === 'overview' ? '' : `?view=${next}`}`); window.scrollTo(0, 0); };
  const alerts = useResource<AlertResponse>('/alerts/active', token, 45_000);
  const alertsUnverified = Boolean(alerts.error || alerts.data?.status === 'error');
  const activeCount = alerts.data?.firing || 0;
  return <div className="workspace"><div className={`sidebar-shade ${sidebar ? 'visible' : ''}`} onClick={() => setSidebar(false)} />
    <aside className={`sidebar ${sidebar ? 'open' : ''}`}><div className="sidebar-brand"><span className="brand-symbol"><Activity size={22} /></span><span><strong>NETPULSE</strong><small>NETWORK OPERATIONS</small></span><button className="icon-button close-sidebar" aria-label="Cerrar menú" onClick={() => setSidebar(false)}><X size={18} /></button></div>
      <div className="sidebar-status"><span className="pulse" /> SISTEMA EN OBSERVACIÓN</div>
      <nav aria-label="Navegación principal"><div className="nav-label">OPERACIÓN</div>{items.filter(item => !item.admin).map(item => <button className={`nav-item ${view === item.id ? 'active' : ''}`} key={item.id} onClick={() => go(item.id)} aria-current={view === item.id ? 'page' : undefined}><item.icon size={18} />{item.label}{item.id === 'alerts' && activeCount > 0 && <span className="nav-count">{activeCount}</span>}</button>)}
        {user.role === 'admin' && <><div className="nav-label separated">ADMINISTRACIÓN</div><button className={`nav-item ${view === 'users' ? 'active' : ''}`} onClick={() => go('users')} aria-current={view === 'users' ? 'page' : undefined}><Users size={18} />Usuarios</button></>}
        <div className="nav-label separated">HERRAMIENTAS DEL MOTOR</div>{more.map(item => <a className="nav-item secondary" key={item.label} href="/legacy" title="Disponible en el panel clásico; requiere iniciar sesión allí"><item.icon size={18} />{item.label}<ExternalLink size={13} className="external-icon" /></a>)}
      </nav><div className="sidebar-bottom"><a className="legacy-link" href="/legacy"><span><Command size={19} /></span><div><strong>Panel anterior</strong><small>Todas las funciones originales</small></div><ExternalLink size={16} /></a><div className="sidebar-version">INTERFAZ REACT <span>·</span> MOTOR FASTAPI</div></div></aside>
    <div className="main-area"><header className="topbar"><div className="topbar-start"><button className="icon-button menu-trigger" aria-label="Abrir menú" onClick={() => setSidebar(true)}><Menu size={21} /></button><span className="location-prefix">NETPULSE /</span><strong>{titles[view]}</strong></div><div className="topbar-end"><button className="alert-shortcut" onClick={() => go('alerts')} aria-label={alertsUnverified ? 'Alertas no verificadas' : `Ver alertas: ${activeCount} activas`}><Bell size={17} /><span>{alertsUnverified ? '— alertas' : `${activeCount} ${activeCount === 1 ? 'alerta' : 'alertas'}`}</span></button><div className="user-chip"><span className="avatar">{user.username.slice(0, 2).toUpperCase()}</span><span><strong>{user.username}</strong><small>{user.role}</small></span></div><button className="icon-button" title="Cerrar sesión" aria-label="Cerrar sesión" onClick={onLogout}><LogOut size={18} /></button></div></header>
      <main id="content" className="main-content" key={view}>{view === 'overview' ? <Overview token={token} go={go} /> : view === 'devices' ? <Devices token={token} role={user.role} /> : view === 'monitoring' ? <MonitoringPage token={token} /> : view === 'alerts' ? <AlertsPage token={token} go={go} /> : user.role === 'admin' ? <UsersPage token={token} self={user.username} /> : <Notice error="No tienes permisos para esta sección." />}</main>
      <footer className="footer"><span>NetPulse NOC · Motor FastAPI existente</span><a href="/legacy">Panel anterior <ExternalLink size={13} /></a></footer>
    </div></div>;
}
function App() {
  const [session, setSession] = useState<{ token: string; user: User } | null>(null);
  useEffect(() => { const invalid = () => setSession(null); window.addEventListener('netpulse:unauthorized', invalid); return () => window.removeEventListener('netpulse:unauthorized', invalid); }, []);
  const content = useMemo(() => session ? <Workspace token={session.token} user={session.user} onLogout={() => setSession(null)} /> : <Login onLogin={(token, user) => setSession({ token, user })} />, [session]);
  return content;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
