/**
 * NetPulse NOC — infraestructura compartida del panel (core.js).
 *
 * Extraído del <script> inline de app/static/dashboard.html (Tarjeta 10C,
 * paso 1 del refactor UI) sin cambio de comportamiento:
 *   - Base de la API (A), i18n completo (I18N + setLang + __t), toast(),
 *   - sesión: token t, currentUser, interceptor 401 de window.fetch,
 *     sessionExpired, showLoginError, enterSession, logout, restoreSession,
 *     renovación silenciosa (scheduleRefresh/stopRefresh/refreshSession/_npTick),
 *   - estado recordado por usuario (_npStateKey/_npStateGet/_npStateSet/_npStateClear).
 *
 * Carga (dashboard.html): <script src="/static/js/core.js?v=N"> clásico, SIN
 * defer/async, SIEMPRE antes del JS inline y de cualquier vista futura
 * (static/js/views/*.js). Es el único bloque "core": no re-inlinearlo ni
 * duplicar estas declaraciones en el inline ni en las vistas (un `let t` o
 * `const A` repetido rompe la página entera: SyntaxError de redeclaración).
 *
 * Enlaces cruzados que resuelven en tiempo de llamada (igual que antes):
 * core -> dashboard: setIconText, updateDashboardHero (setLang/toast), init
 * (enterSession), repState/_invGroupsCache (logout). dashboard -> core:
 * login/loginBusy/estado de vista. No adelantar ejecuciones: si un día se
 * mueve algo de esto a core, mantener la invocación en el punto original.
 *
 * Intencionalmente SIN 'use strict' (el inline original es sloppy mode) y sin
 * envolver en IIFE: los `let`/`const` de top-level de un script clásico viven
 * en el entorno léxico global compartido por todos los scripts de la página;
 * las `function` de top-level, además, quedan colgadas de window.
 */

// ── BASE DE LA API ── (antes en dashboard.html, línea 3004)
    const A = (window.location.origin && window.location.origin !== 'null')
      ? window.location.origin + '/api'
      : 'http://localhost:8082/api';

    // Estado de sesión compartido (partido del let combinado del inline original)
    let t = '', currentUser = null;

    // Sesión persistente: el JWT vive en localStorage ('np_token') y se renueva en silencio.
    let _refreshTimer = null; // renovación silenciosa (~25 min; exp por defecto 30 min)
    let _np401Done = false;   // el aviso de sesión caducada solo se muestra UNA vez
    let currentLang = localStorage.getItem('np_lang') || 'es';

    // ── i18n SYSTEM ──
    const I18N = {
      en: {
        nav_overview: 'OVERVIEW', nav_dashboard: 'Dashboard', nav_infrastructure: 'INFRASTRUCTURE',
        nav_devices: 'Devices', nav_connectivity: 'Connectivity', nav_observability: 'OBSERVABILITY',
        nav_performance: 'Equipment', nav_alerts: 'Alerts', nav_logs: 'Logs', nav_monitoring: 'Monitoring',
        nav_operations: 'OPERATIONS', nav_commands: 'Commands', nav_backups: 'Backups',
        nav_compare: 'Compare', nav_topology: 'Topology',
        nav_maintenance: 'Maintenance', nav_security: 'SECURITY',
        nav_compliance: 'Compliance',
        nav_apikeys: 'API Keys', nav_2fa: '2FA / TOTP', nav_administration: 'ADMINISTRATION',
        nav_inventory: 'Device Inventory', nav_users: 'Users & Roles', nav_webhooks: 'Webhooks',
        nav_settings: 'Settings', nav_system: 'SYSTEM',
        sys_devices: 'Devices', btn_signout: 'Sign Out',

        view_devices: 'Devices', view_cmd_center: 'Network Command Center',
        view_register: 'Add / Register Equipment',
        cmd_desc: 'Batch execution across infrastructure', cmd_targets: 'TARGET DEVICES',
        cmd_commands: 'COMMANDS (One per line)', cmd_output: 'EXECUTION OUTPUT',

        btn_multi: 'Multiple PUSH', btn_add_device: 'Add Device', btn_exec: 'Execute', btn_clear: 'Clear',
        btn_register: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Register Device to Database', card_latency: 'Latency', btn_refresh: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg> Refresh', btn_test: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg> Test Connectivity', btn_close: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Close',

        view_conn: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> Connectivity', view_theme: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/><circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/><path d="M12 22a10 10 0 1 1 10-10 4 4 0 0 1-4 4h-2a2 2 0 0 0-2 2 2 2 0 0 1-2 4Z"/></svg> Theme & Appearance',
        view_perf: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg> Device Resources', view_alerts: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M7 18v-6a5 5 0 0 1 10 0v6M5 18h14v3H5zM12 2v2M3 7l2 1M21 7l-2 1M5 2l1 2M19 2l-1 2"/></svg> Active Alerts', view_logs: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg> Audit & System Logs', view_monitoring: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 3v18h18M7 14v3M12 9v8M17 5v12"/></svg> Monitoring',
        view_bk: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> Backup Management', view_sched: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> Scheduled Backups',
        view_comp: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Compliance Validation', view_wh: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg> Webhooks / Notifications', view_admin: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg> Administration',
        view_cmp: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 8h18m-4-4 4 4-4 4M21 16H3m4-4-4 4 4 4"/></svg> Compare Configurations', view_audit_tbl: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> Audit Trail',
        view_topo: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15M15 6v15"/></svg> Network Topology', view_health: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/></svg> Health Scores', view_maint: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg> Maintenance Windows',
        view_keys: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="8" cy="15" r="5"/><path d="m11.5 11.5 9-9M16 7l3 3M19 4l3 3"/></svg> API Keys',

        lbl_id: 'ID (Internal Name)', lbl_host: 'IP / Hostname', lbl_vendor: 'Manufacturer/OS', lbl_protocol: 'Protocol',

        tfa_title: 'Two-Factor Authentication (2FA)', tfa_step1: 'Generate TOTP Secret',
        tfa_desc: 'Generate a unique secret key and scan the QR code with your authenticator app (Google Authenticator, Authy, Microsoft Authenticator).',
        tfa_generate: 'Generate Secret Key', tfa_warning: 'Keep this secret safe. Do not share it.',
        tfa_step2: 'Verify Code', tfa_verify_desc: 'Enter the 6-digit code from your authenticator app to verify the setup is working correctly.',
        tfa_code_label: 'Verification Code', tfa_verify_btn: 'Verify & Activate',
        tfa_status_title: '2FA Status', tfa_status_inactive: 'Not configured — Generate a secret key to begin setup.'
      },
      es: {
        nav_overview: 'GENERAL', nav_dashboard: 'Panel Principal', nav_infrastructure: 'INFRAESTRUCTURA',
        nav_devices: 'Dispositivos', nav_connectivity: 'Conectividad', nav_observability: 'OBSERVABILIDAD',
        nav_performance: 'Equipos', nav_alerts: 'Alertas', nav_logs: 'Registros', nav_monitoring: 'Monitoreo',
        nav_operations: 'OPERACIONES', nav_commands: 'Comandos', nav_backups: 'Respaldos',
        nav_compare: 'Comparar', nav_topology: 'Topología',
        nav_maintenance: 'Mantenimiento', nav_security: 'SEGURIDAD',
        nav_compliance: 'Cumplimiento',
        nav_apikeys: 'Llaves API', nav_2fa: '2FA / TOTP', nav_administration: 'ADMINISTRACIÓN',
        nav_inventory: 'Inventario de Equipos', nav_users: 'Usuarios y Roles', nav_webhooks: 'Webhooks',
        nav_settings: 'Configuración', nav_system: 'SISTEMA',
        sys_devices: 'Dispositivos', btn_signout: 'Cerrar Sesión',

        view_devices: 'Dispositivos', view_cmd_center: 'Centro de Comandos de Red',
        view_register: 'Registrar Nuevo Equipo',
        cmd_desc: 'Ejecución en lote multiplataforma', cmd_targets: 'DISPOSITIVOS (Ctrl+Click)',
        cmd_commands: 'COMANDOS (Uno por línea)', cmd_output: 'SALIDA DE TERMINAL',

        btn_multi: 'Multiselección', btn_add_device: 'Agregar dispositivo', btn_exec: 'Ejecutar', btn_clear: 'Limpiar',
        btn_register: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Registrar Dispositivo en DB', card_latency: 'Latencia', btn_refresh: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg> Actualizar', btn_test: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg> Probar', btn_close: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Cerrar',

        view_conn: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> Conectividad', view_theme: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/><circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/><path d="M12 22a10 10 0 1 1 10-10 4 4 0 0 1-4 4h-2a2 2 0 0 0-2 2 2 2 0 0 1-2 4Z"/></svg> Apariencia',
        view_perf: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg> Recursos por Equipo', view_alerts: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M7 18v-6a5 5 0 0 1 10 0v6M5 18h14v3H5zM12 2v2M3 7l2 1M21 7l-2 1M5 2l1 2M19 2l-1 2"/></svg> Alertas Activas', view_logs: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg> Logs de Sistema y Auditoría', view_monitoring: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 3v18h18M7 14v3M12 9v8M17 5v12"/></svg> Monitoreo',
        view_bk: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> Gestión de Backups', view_sched: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> Backups Programados',
        view_comp: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Validación de Cumplimiento', view_wh: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg> Webhooks / Notificaciones', view_admin: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg> Administración',
        view_cmp: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 8h18m-4-4 4 4-4 4M21 16H3m4-4-4 4 4 4"/></svg> Comparar Configuraciones', view_audit_tbl: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> Auditoría del Sistema',
        view_topo: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15M15 6v15"/></svg> Topología de Red', view_health: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/></svg> Estado de Salud', view_maint: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg> Ventanas de Mantenimiento',
        view_keys: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="8" cy="15" r="5"/><path d="m11.5 11.5 9-9M16 7l3 3M19 4l3 3"/></svg> API Keys',

        lbl_id: 'ID (Nombre Interno)', lbl_host: 'IP / Hostname', lbl_vendor: 'Fabricante / OS', lbl_protocol: 'Protocolo',

        tfa_title: 'Autenticación de Dos Factores (2FA)', tfa_step1: 'Generar Secreto TOTP',
        tfa_desc: 'Genera una clave secreta única y escanea el código QR con tu app de autenticación (Google Authenticator, Authy, Microsoft Authenticator).',
        tfa_generate: 'Generar Clave Secreta', tfa_warning: 'Mantén este secreto seguro. No lo compartas.',
        tfa_step2: 'Verificar Código', tfa_verify_desc: 'Ingresa el código de 6 dígitos de tu app de autenticación para verificar que la configuración funcione correctamente.',
        tfa_code_label: 'Código de Verificación', tfa_verify_btn: 'Verificar y Activar',
        tfa_status_title: 'Estado 2FA', tfa_status_inactive: 'No configurado — Genera una clave secreta para comenzar.'
      }
    };

    // Dynamic extensions for deeply rendered DOM elements
    Object.assign(I18N.en, { stat_devices: 'DEVICES', stat_online: 'ONLINE', stat_offline: 'OFFLINE', stat_avail: 'AVAILABILITY', stat_alerts: 'ACTIVE ALERTS', stat_sys: 'NETWORK STATUS', state_ok: 'OPERATIONAL', state_down: 'OFFLINE', state_degraded: 'DEGRADED', state_unknown: 'UNVERIFIED', state_empty: 'NO DATA', tab_ov: 'Overview', tab_if: 'Interfaces', tab_cfg: 'Config (RAM)', tab_cmd: 'Commands', btn_cmd: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 17l6-6-6-6"/><path d="M12 19h8"/></svg> Command', btn_ping: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> Ping', btn_facts: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> Facts', bulk_none: 'No selection', qa_bk: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> Backup', qa_sv: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> Save RAM', qa_ping: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> Ping Test', qa_exp: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M12 3v12m-5-5 5 5 5-5"/></svg> Export', qa_cmp: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 8h18m-4-4 4 4-4 4M21 16H3m4-4-4 4 4 4"/></svg> Compare', m_loading: 'Loading metrics...' });
    Object.assign(I18N.es, { stat_devices: 'DISPOSITIVOS', stat_online: 'EN LÍNEA', stat_offline: 'FUERA DE LÍNEA', stat_avail: 'DISPONIBILIDAD', stat_alerts: 'ALERTAS ACTIVAS', stat_sys: 'ESTADO DE LA RED', state_ok: 'OPERATIVA', state_down: 'CAÍDA', state_degraded: 'DEGRADADA', state_unknown: 'SIN VERIFICAR', state_empty: 'SIN DATOS', tab_ov: 'General', tab_if: 'Interfaces', tab_cfg: 'Configuración (RAM)', tab_cmd: 'Comandos', btn_cmd: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 17l6-6-6-6"/><path d="M12 19h8"/></svg> Comando', btn_ping: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> Ping', btn_facts: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> Informes', bulk_none: 'Sin selección', qa_bk: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> Backup', qa_sv: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> Guardar RAM', qa_ping: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> Ping Test', qa_exp: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M12 3v12m-5-5 5 5 5-5"/></svg> Exportar', qa_cmp: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 8h18m-4-4 4 4-4 4M21 16H3m4-4-4 4 4 4"/></svg> Comparar', m_loading: 'Cargando métricas...' });
    // Admin de alertas: silencios (Alertmanager) + reglas (Prometheus) — contrato docs/handoffs/alerts_ui.md
    Object.assign(I18N.en, {
      al_sil_title: 'Active silences (Alertmanager)', al_sil_new: 'Silence…',
      al_sil_device: 'device_id (optional)', al_sil_group: 'group (optional)', al_sil_matchers: 'Free-form matchers (optional)',
      al_sil_matcher_add: '+ matcher', al_sil_matcher_name_ph: 'name', al_sil_matcher_value_ph: 'value', al_sil_regex: 'regex',
      al_sil_duration: 'Duration', al_sil_comment: 'Comment', al_sil_create: 'Create silence', al_sil_cancel: 'Cancel',
      al_sil_none: 'No active silences', al_sil_unavail: 'Alertmanager unavailable — not verifiable',
      al_sil_create_unavail: 'Alertmanager unavailable — silence not created', al_sil_created: 'Silence created', al_sil_deleted: 'Silence cancelled',
      al_sil_delete_confirm: 'Cancel this silence?', al_sil_yes: 'Yes', al_sil_no: 'No', al_sil_404: 'The silence no longer exists (it may have expired)',
      al_sil_err_generic: 'Could not complete the operation', al_sil_invalid_scope: 'Indicate what to silence (device, group or matchers)',
      al_th_scope: 'Scope', al_th_owner: 'Created by', al_th_expires: 'Expires', al_th_comment: 'Comment',
      al_rules_title: 'Active rules (Prometheus)', al_rules_hint: 'Read-only — writing rules is not available in this release',
      al_rules_none: 'No rules loaded', al_rules_unavail: 'Prometheus unavailable — rules not verifiable',
      al_th_name: 'Rule', al_th_expr: 'Expression', al_th_for: 'for', al_th_sev: 'Severity', al_th_labels: 'Labels', al_expand: 'expand'
    });
    Object.assign(I18N.es, {
      al_sil_title: 'Silencios activos (Alertmanager)', al_sil_new: 'Silenciar…',
      al_sil_device: 'device_id (opcional)', al_sil_group: 'group (opcional)', al_sil_matchers: 'Matchers libres (opcional)',
      al_sil_matcher_add: '+ matcher', al_sil_matcher_name_ph: 'nombre', al_sil_matcher_value_ph: 'valor', al_sil_regex: 'regex',
      al_sil_duration: 'Duración', al_sil_comment: 'Comentario', al_sil_create: 'Crear silencio', al_sil_cancel: 'Cancelar',
      al_sil_none: 'Sin silencios activos', al_sil_unavail: 'Alertmanager no disponible — no verificable',
      al_sil_create_unavail: 'Alertmanager no disponible — no se pudo crear', al_sil_created: 'Silencio creado', al_sil_deleted: 'Silencio cancelado',
      al_sil_delete_confirm: '¿Cancelar este silencio?', al_sil_yes: 'Sí', al_sil_no: 'No', al_sil_404: 'El silencio ya no existe (quizá venció)',
      al_sil_err_generic: 'No se pudo completar la operación', al_sil_invalid_scope: 'Indica qué silenciar (dispositivo, grupo o matchers)',
      al_th_scope: 'Alcance', al_th_owner: 'Creado por', al_th_expires: 'Vence', al_th_comment: 'Comentario',
      al_rules_title: 'Reglas activas (Prometheus)', al_rules_hint: 'Solo lectura — la escritura de reglas no está disponible en esta versión',
      al_rules_none: 'Sin reglas cargadas', al_rules_unavail: 'Prometheus no disponible — reglas no verificables',
      al_th_name: 'Regla', al_th_expr: 'Expresión', al_th_for: 'for', al_th_sev: 'Severidad', al_th_labels: 'Etiquetas', al_expand: 'expandir'
    });

    Object.assign(I18N.en, {
      topo_tab_graph: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="9" y="2" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="16" y="16" width="6" height="6" rx="1"/><path d="M12 8v4M5 16v-4h14v4"/></svg> Graph', topo_tab_map: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a17 17 0 0 1 0 20 17 17 0 0 1 0-20"/></svg> Map', topo_tab_table: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> Table',
      topo_tr_title: 'TRACEROUTE ON THE MAP', topo_tr_origin: 'Origin', topo_tr_dest: 'Destination (IP / FQDN)',
      topo_tr_run: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><polygon points="6 4 20 12 6 20"/></svg> Run Traceroute', topo_tr_running: 'Running traceroute… (up to 90s)',
      topo_tr_empty: 'Run a traceroute to display the hops on the map.',
      topo_tr_err_pre: 'Traceroute failed: ', topo_th_hop: 'Hop', topo_th_host: 'Host',
      topo_th_ip: 'IP', topo_th_rtt: 'RTT', topo_th_geo: 'Location', topo_th_dev: 'NetPulse device',
      topo_tr_private: 'Private address — no public geolocation (drawn as unknown hop).',
      topo_geo_off: 'Geolocation off: place GeoLite2-City.mmdb in config/ (docs: MaxMind).',
      topo_tr_clear: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Clear', topo_nodes_no_geo: 'Devices without geolocation (local IPs):',
      topo_tr_from: 'from'
    });
    Object.assign(I18N.es, {
      topo_tab_graph: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="9" y="2" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="16" y="16" width="6" height="6" rx="1"/><path d="M12 8v4M5 16v-4h14v4"/></svg> Grafo', topo_tab_map: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a17 17 0 0 1 0 20 17 17 0 0 1 0-20"/></svg> Mapa', topo_tab_table: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> Tabla',
      topo_tr_title: 'TRACEROUTE SOBRE EL MAPA', topo_tr_origin: 'Origen', topo_tr_dest: 'Destino (IP / FQDN)',
      topo_tr_run: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><polygon points="6 4 20 12 6 20"/></svg> Ejecutar Traceroute', topo_tr_running: 'Ejecutando traceroute… (hasta 90s)',
      topo_tr_empty: 'Ejecuta un traceroute para mostrar los saltos sobre el mapa.',
      topo_tr_err_pre: 'Falló el traceroute: ', topo_th_hop: 'Salto', topo_th_host: 'Host',
      topo_th_ip: 'IP', topo_th_rtt: 'RTT', topo_th_geo: 'Ubicación', topo_th_dev: 'Equipo NetPulse',
      topo_tr_private: 'Dirección privada — sin geolocalización pública (se dibuja como salto desconocido).',
      topo_geo_off: 'Geolocalización desactivada: coloca GeoLite2-City.mmdb en config/ (docs: MaxMind).',
      topo_tr_clear: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Limpiar', topo_nodes_no_geo: 'Equipos sin geolocalización (IPs locales):',
      topo_tr_from: 'desde'
    });
    Object.assign(I18N.en, { search_placeholder: 'Search devices or IPs (Ctrl+K)', ov_focus: 'Offline devices', ov_loading: 'Checking devices…', ov_loading_inventory: 'Loading inventory…', ov_hint: 'Select a device to inspect its details.', ov_topology: 'Open topology', ov_refresh: 'Refresh', ov_add: 'Add device', ov_offline: 'Offline', ov_online: 'Online', ov_devices: 'Devices', ov_availability: 'Online share', ov_alerts: 'Active alerts', ov_detail: 'View details', ov_unverified: 'Unverified', ov_disk: 'Disk', login_submit: 'Sign in', login_busy: 'Signing in…', login_err_401: 'Incorrect username or password. Check your credentials and try again.', login_err_429: 'Too many attempts. Wait a minute before signing in again.', login_err_generic: 'Could not sign in. Check that NetPulse is available and try again.', login_err_session: 'The session could not be validated. Please try again.', login_err_conn: 'Could not connect to NetPulse. Make sure the API is running and try again.', sess_expired: 'Session expired — please sign in again.' });
    Object.assign(I18N.es, { search_placeholder: 'Buscar equipos o IP (Ctrl+K)', ov_focus: 'Equipos fuera de línea', ov_loading: 'Verificando equipos…', ov_loading_inventory: 'Consultando inventario…', ov_hint: 'Selecciona un equipo para ver su detalle.', ov_topology: 'Abrir topología', ov_refresh: 'Actualizar', ov_add: 'Agregar equipo', ov_offline: 'Fuera de línea', ov_online: 'En línea', ov_devices: 'Dispositivos', ov_availability: 'Porcentaje en línea', ov_alerts: 'Alertas activas', ov_detail: 'Ver detalle', ov_unverified: 'Sin verificar', ov_disk: 'Disco', login_submit: 'Ingresar', login_busy: 'Ingresando…', login_err_401: 'Usuario o contraseña incorrectos. Revisa los datos e inténtalo de nuevo.', login_err_429: 'Demasiados intentos. Espera un minuto antes de volver a ingresar.', login_err_generic: 'No se pudo iniciar sesión. Comprueba que NetPulse esté disponible e inténtalo de nuevo.', login_err_session: 'No se pudo validar la sesión. Vuelve a intentarlo.', login_err_conn: 'No se pudo conectar con NetPulse. Verifica que la API esté encendida e inténtalo de nuevo.', sess_expired: 'Sesión expirada — vuelve a entrar.' });

    // Claves nuevas: Grupos (módulo dormido), NetBox (solo admin) y guard de Compliance.
    Object.assign(I18N.en, {
      nav_groups: 'Groups', nav_netbox: 'NetBox',
      view_groups: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 17a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3.9a2 2 0 0 1-1.69-.9l-.81-1.2a2 2 0 0 0-1.67-.9H8a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2Z"/><path d="M2 8v11a2 2 0 0 0 2 2h14"/></svg> Device Groups', view_netbox: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg> NetBox',
      np_unverified: 'Not verifiable',
      grp_col_group: 'Group', grp_col_desc: 'Description', grp_col_devices: 'Devices',
      grp_add_title: 'Create group', grp_name_ph: 'Name (e.g. DMZ)', grp_desc_ph: 'Optional description', grp_add_btn: 'Create',
      grp_admin_required: 'Creating groups requires the admin role', grp_empty: 'No groups defined',
      grp_list_unverified: 'The group list could not be verified',
      grp_view_devices: 'View devices', grp_devices_none: 'no devices in this group',
      grp_name_missing: 'Type a name for the group', grp_exists: 'That group already exists',
      grp_created: 'Group {g} created', grp_create_fail: 'Could not create the group',
      nb_admin_required: 'Requires the admin role', nb_unavailable: 'NetBox not available',
      nb_state_title: 'Availability', nb_online: 'NetBox reachable',
      nb_sites_title: 'Sites', nb_sites_empty: 'NetBox reachable, but no sites defined',
      nb_col_site: 'Site', nb_col_status: 'Status', nb_col_region: 'Region', nb_col_devices: 'Devices',
      nb_sync_title: 'Sync from NetBox', nb_sync: 'Synchronize',
      nb_sync_desc: 'Pulls devices from NetBox into the local inventory (devices.yaml). NetBox is the source of truth; existing devices are updated and nothing is deleted.',
      nb_sync_confirm_title: 'Confirm NetBox → inventory sync',
      nb_sync_run: 'Run sync', nb_cancel: 'Cancel', nb_sync_running: 'Synchronizing… this can take a while',
      nb_count_created: 'created', nb_count_updated: 'updated', nb_count_skipped: 'skipped',
      nb_what_title: 'What is NetBox?',
      nb_what_text: 'NetBox is the external source-of-truth inventory catalog of the network: which devices exist, where they are and which management IP they use. The sync imports those devices into NetPulse and updates existing ones; it NEVER deletes local devices and does not overwrite your credentials or the local id of an existing device.',
      nb_what_detail: 'Scope and guarantees (reported by the server)',
      nb_scope: 'What gets imported',
      nb_no_hace: 'What it never does',
      nb_checklist_title: 'Configuration checklist',
      nb_check_cfg: 'Configured — URL and token set',
      nb_check_token: 'Token available (NETBOX_TOKEN or config/.netbox_token)',
      nb_check_url: 'NetBox URL (masked)',
      nb_not_configured_title: 'NetBox is not configured',
      nb_not_configured_vars: 'Variables on the server: NETBOX_URL · NETBOX_TOKEN (or the file config/.netbox_token)',
      nb_sync_disabled_hint: 'Sync stays disabled until NETBOX_URL and NETBOX_TOKEN are set on the server',
      nb_status_fail: 'Could not verify the NetBox configuration',
      nb_sync_fail: 'Sync failed — nothing was verified',
      nb_res_ok: 'Sync completed',
      nb_res_partial: 'Partial sync: {n} devices were not imported',
      nb_res_partial_hint: 'Fix those devices in NetBox and sync again — everything else is already in the local inventory.',
      nb_count_unchanged: 'unchanged', nb_count_errors: 'errors',
      nb_list_created: 'Created ({n})', nb_list_updated: 'Updated ({n})',
      nb_list_skipped: 'Skipped ({n})', nb_list_errors: 'Errors ({n})',
      nb_totals: 'Local inventory: {a} devices · NetBox: {b} devices',
      nb_sites_checking: 'Checking whether NetBox answers…',
      comp_admin_required: 'Compliance requires the admin role',
      comp_baselines: 'Defined baselines', comp_no_baselines: 'No baselines defined yet: create one in step 1',
      comp_step1: 'Pick group', comp_step1_d: 'Type or choose the group this baseline applies to',
      comp_step2: 'Write baseline', comp_step2_d: 'Standard configuration the devices must comply with',
      comp_step3: 'Verify devices', comp_step3_d: 'Choose which devices to check against the baseline',
      comp_no_devices: 'No devices loaded from the inventory yet', comp_save_baseline: 'Save baseline',
      comp_check_btn: 'Check compliance', comp_report_btn: 'Full report',
      comp_report_running: 'Generating report…', comp_need_device: 'Select at least one device',
      comp_checking: 'Verifying…', comp_ok_summary: 'devices comply', comp_mal_summary: 'devices do NOT comply',
      comp_errors: 'with read errors', comp_check_fail: 'Compliance check failed',
      comp_baseline_fail: 'Could not save the baseline', comp_baseline_saved: 'Baseline for {g} saved',
      comp_th_device: 'Device', comp_th_result: 'Result', comp_th_violations: 'Violations'
    });
    Object.assign(I18N.es, {
      nav_groups: 'Grupos', nav_netbox: 'NetBox',
      view_groups: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 17a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3.9a2 2 0 0 1-1.69-.9l-.81-1.2a2 2 0 0 0-1.67-.9H8a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2Z"/><path d="M2 8v11a2 2 0 0 0 2 2h14"/></svg> Grupos de dispositivos', view_netbox: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg> NetBox',
      np_unverified: 'No verificable',
      grp_col_group: 'Grupo', grp_col_desc: 'Descripción', grp_col_devices: 'Equipos',
      grp_add_title: 'Crear grupo', grp_name_ph: 'Nombre (ej. DMZ)', grp_desc_ph: 'Descripción opcional', grp_add_btn: 'Crear',
      grp_admin_required: 'Crear grupos requiere rol admin', grp_empty: 'Sin grupos definidos',
      grp_list_unverified: 'No se pudo verificar la lista de grupos',
      grp_view_devices: 'Ver equipos', grp_devices_none: 'sin equipos asociados',
      grp_name_missing: 'Escribe un nombre para el grupo', grp_exists: 'Ese grupo ya existe',
      grp_created: 'Grupo {g} creado', grp_create_fail: 'No se pudo crear el grupo',
      nb_admin_required: 'Requiere rol admin', nb_unavailable: 'NetBox no disponible',
      nb_state_title: 'Disponibilidad', nb_online: 'NetBox responde',
      nb_sites_title: 'Sites', nb_sites_empty: 'NetBox responde, pero sin sites definidos',
      nb_col_site: 'Sitio', nb_col_status: 'Estado', nb_col_region: 'Región', nb_col_devices: 'Equipos',
      nb_sync_title: 'Sincronizar desde NetBox', nb_sync: 'Sincronizar',
      nb_sync_desc: 'Vuelca los equipos de NetBox al inventario local (devices.yaml). NetBox es la fuente de verdad; los equipos existentes se actualizan y no se borra nada.',
      nb_sync_confirm_title: 'Confirmar sincronización NetBox → inventario',
      nb_sync_run: 'Ejecutar sincronización', nb_cancel: 'Cancelar', nb_sync_running: 'Sincronizando… puede tardar un rato',
      nb_count_created: 'creados', nb_count_updated: 'actualizados', nb_count_skipped: 'omitidos',
      nb_what_title: '¿Qué es NetBox?',
      nb_what_text: 'NetBox es el catálogo externo de inventario (source of truth) de la red: qué equipos existen, dónde están y con qué IP de gestión. La sincronización importa esos equipos a NetPulse y actualiza los existentes; NUNCA borra equipos locales y no sobreescribe tus credenciales ni el id local de un equipo existente.',
      nb_what_detail: 'Alcance y garantías (informadas por el servidor)',
      nb_scope: 'Qué se importa',
      nb_no_hace: 'Lo que nunca hace',
      nb_checklist_title: 'Checklist de configuración',
      nb_check_cfg: 'Configurado — URL y token definidos',
      nb_check_token: 'Token disponible (NETBOX_TOKEN o config/.netbox_token)',
      nb_check_url: 'URL de NetBox (enmascarada)',
      nb_not_configured_title: 'NetBox no está configurado',
      nb_not_configured_vars: 'Variables en el servidor: NETBOX_URL · NETBOX_TOKEN (o el archivo config/.netbox_token)',
      nb_sync_disabled_hint: 'La sincronización queda deshabilitada hasta definir NETBOX_URL y NETBOX_TOKEN en el servidor',
      nb_status_fail: 'No se pudo verificar la configuración de NetBox',
      nb_sync_fail: 'La sincronización falló — nada quedó verificado',
      nb_res_ok: 'Sincronización completada',
      nb_res_partial: 'Sincronización parcial: {n} equipos no se importaron',
      nb_res_partial_hint: 'Corrige esos equipos en NetBox y vuelve a sincronizar; el resto ya está en el inventario local.',
      nb_count_unchanged: 'sin cambios', nb_count_errors: 'errores',
      nb_list_created: 'Creados ({n})', nb_list_updated: 'Actualizados ({n})',
      nb_list_skipped: 'Saltados ({n})', nb_list_errors: 'Errores ({n})',
      nb_totals: 'Inventario local: {a} equipos · NetBox: {b} equipos',
      nb_sites_checking: 'Verificando si NetBox responde…',
      comp_admin_required: 'Cumplimiento requiere rol admin',
      comp_baselines: 'Baselines definidos', comp_no_baselines: 'Sin baselines definidos aún: se crean en el paso 1',
      comp_step1: 'Elegir grupo', comp_step1_d: 'Escribe o elige el grupo al que aplica el baseline',
      comp_step2: 'Escribir baseline', comp_step2_d: 'Configuración estándar que deben cumplir los dispositivos',
      comp_step3: 'Verificar dispositivos', comp_step3_d: 'Elegí cuáles revisar contra el baseline',
      comp_no_devices: 'Sin equipos cargados del inventario todavía', comp_save_baseline: 'Guardar baseline',
      comp_check_btn: 'Verificar cumplimiento', comp_report_btn: 'Reporte completo',
      comp_report_running: 'Generando reporte…', comp_need_device: 'Seleccioná al menos un dispositivo',
      comp_checking: 'Verificando…', comp_ok_summary: 'equipos cumplen', comp_mal_summary: 'equipos NO cumplen',
      comp_errors: 'con errores de lectura', comp_check_fail: 'Fallo la verificación de cumplimiento',
      comp_baseline_fail: 'No se pudo guardar el baseline', comp_baseline_saved: 'Baseline para {g} guardado',
      comp_th_device: 'Dispositivo', comp_th_result: 'Resultado', comp_th_violations: 'Violaciones'
    });

    // Reportes SLA + Wizard de auto-discovery — contratos docs/handoffs/reportes-sla.md y discovery.md
    Object.assign(I18N.en, {
      nav_reports: 'SLA Reports', view_reports: 'SLA Reports',
      rep_lbl_desde: 'From (day)', rep_lbl_hasta: 'To (day)', rep_presets: 'Quick windows',
      rep_lbl_group: 'Device group (optional)', rep_lbl_devices: 'Devices (optional)',
      rep_btn_cached: 'Get last report', rep_btn_regen: 'Recalculate', rep_recalc: 'Recalculate',
      rep_viewer_only: 'Recalculating requires the operator or admin role (viewer can only read the last report).',
      rep_window_note: 'SLA is only computed on observed samples; gaps are never interpolated. The history in this process covers up to 168 h.',
      rep_any_group: '— Any group —', rep_no_devices_yet: 'Inventory not loaded yet',
      rep_cache_miss: 'No previous report exists for these filters.',
      rep_unavailable: 'Reports module not available — not verifiable', rep_timeout: 'timed out',
      rep_partial: 'partial coverage', rep_of_samples: 'of {n} observed samples',
      rep_first_sample: 'First sample', rep_last_sample: 'Last sample',
      rep_hours_req: 'requested {g} h · observed span {o} h',
      rep_inc_from: 'Down start', rep_inc_last: 'Last down observation', rep_inc_dur: 'Observed duration',
      rep_inc_single: 'observed 0 s = a single down sample; the real outage length cannot be inferred',
      rep_no_incidents: 'No down samples in this window',
      rep_excl_n: 'No data in your window: {n} devices were excluded', rep_col_reason: 'Reason',
      rep_collector_off: 'Active collection is off', rep_from_cache: 'last report kept by this process',
      rep_need_dates: 'Pick both window dates first', rep_bad_window: 'Invalid window: the start must be before the end',
      rep_future_clamped: 'Future dates are not allowed: the window end was trimmed to now',
      rep_window_max: 'The window may span at most 31 days', rep_groups_unverified: 'Group list not verifiable',
      rep_state_nodata: 'No data',
      help_sites: 'Shows what the remote site collector measures (freshness, buffers and per-device state) — it is NOT comparable with the central polling of the Overview.',
      help_retention: 'Shows what is kept: audit trail and local backups. Purging audit records requires a dry-run simulation first — the preview deletes nothing.',
      help_reports: 'SLA is computed only on observed samples within your window: partial coverage and gaps are shown as-is, never interpolated.',
      help_scopes: 'The scope limits which groups/sites a user can see. It applies on the user\'s NEXT login, not to an active session.',
      dw_help: 'Queries LLDP/CDP neighbors from the origin devices you pick and proposes candidates — nothing is imported into the inventory without your explicit confirmation in step 4.'
    });
    Object.assign(I18N.es, {
      nav_reports: 'Reportes SLA', view_reports: 'Reportes SLA',
      rep_lbl_desde: 'Desde (día)', rep_lbl_hasta: 'Hasta (día)', rep_presets: 'Ventanas rápidas',
      rep_lbl_group: 'Grupo de equipos (opcional)', rep_lbl_devices: 'Equipos (opcional)',
      rep_btn_cached: 'Consultar último', rep_btn_regen: 'Regenerar', rep_recalc: 'Recalcular',
      rep_viewer_only: 'Regenerar requiere rol operator o admin (viewer solo puede leer el último reporte).',
      rep_window_note: 'El SLA solo se calcula sobre muestras observadas; los huecos nunca se interpolan. El histórico de este proceso cubre hasta 168 h.',
      rep_any_group: '— Cualquier grupo —', rep_no_devices_yet: 'Inventario sin cargar todavía',
      rep_cache_miss: 'No existe reporte previo para estos filtros.',
      rep_unavailable: 'Módulo de reportes no disponible — no verificable', rep_timeout: 'venció el tiempo',
      rep_partial: 'cobertura parcial', rep_of_samples: 'de {n} muestras observadas',
      rep_first_sample: 'Primera muestra', rep_last_sample: 'Última muestra',
      rep_hours_req: 'solicitaste {g} h · separación observada {o} h',
      rep_inc_from: 'Inicio down', rep_inc_last: 'Última observación down', rep_inc_dur: 'Duración observada',
      rep_inc_single: '0 s observados = una sola muestra down; no se puede inferir cuánto duró de verdad',
      rep_no_incidents: 'Sin muestras down en esta ventana',
      rep_excl_n: 'Sin datos en tu ventana: se excluyeron {n} equipos', rep_col_reason: 'Motivo',
      rep_collector_off: 'La recolección activa está apagada', rep_from_cache: 'último reporte que conserva este proceso',
      rep_need_dates: 'Primero elige las dos fechas de la ventana', rep_bad_window: 'Ventana inválida: el inicio debe ser anterior al fin',
      rep_future_clamped: 'No se permiten fechas futuras: se recortó el final de la ventana a ahora',
      rep_window_max: 'La ventana puede cubrir 31 días como máximo', rep_groups_unverified: 'Lista de grupos no verificable',
      rep_state_nodata: 'Sin datos',
      help_sites: 'Muestra lo que mide el colector del sitio remoto (frescura, buffers y estado por equipo) — NO es comparable con el sondeo central del Overview.',
      help_retention: 'Muestra qué se guarda: auditoría y backups locales. La purga de auditoría requiere antes una simulación (dry-run) — la previsualización no borra nada.',
      help_reports: 'El SLA se calcula solo con las muestras observadas en tu ventana: la cobertura parcial y los huecos se muestran tal cual, nunca se interpolan.',
      help_scopes: 'El scope limita los grupos/sitios que puede ver un usuario. Aplica en el PRÓXIMO login del usuario, no sobre una sesión activa.',
      dw_help: 'Consulta los vecinos LLDP/CDP de los equipos origen que elijas y propone candidatos — nada se importa al inventario sin tu confirmación explícita en el paso 4.'
    });
    Object.assign(I18N.en, {
      dw_open: 'Discover devices', dw_title: 'Auto-discovery wizard',
      dw_close: 'Close', dw_step_n: 'Step {n}',
      dw_step1_desc: 'Pick the origin devices to query for LLDP/CDP neighbors. Nothing is imported without explicit confirmation.',
      dw_metodo: 'Discovery method', dw_cdp_note: 'CDP only on ios/nxos/iosxr/eos drivers',
      dw_origins: 'Origin devices', dw_pick_all: 'Select all / none',
      dw_scan_btn: 'Scan now', dw_scanning: 'Scanning…', dw_scanning_note: 'Up to ~25 s. Do not close or leave this view. A fallen device is not a global error: it shows per-origin below.',
      dw_no_origins: 'No origins available — register devices in the inventory first.',
      dw_pick_origins: 'Select at least one origin device (or use Select all)',
      dw_scan_done: 'Scan finished', dw_res_origins: 'origins', dw_res_failed: 'with error',
      dw_next_candidates: 'View candidates', dw_no_candidates: 'No candidates for this scan',
      dw_back_step1: 'Back to origins', dw_rescan: 'Scan again (last scan stays as listed)',
      dw_reused: 'Reusing the last scan kept by this process (it is lost on API restart)',
      dw_in_inventory: 'Already in inventory', dw_proposed: 'Proposed', dw_no_platform: 'Platform unidentified',
      dw_next_confirm: 'Confirm selection', dw_confirm_title: 'Import {n} candidate(s) into the inventory',
      dw_col_name: 'Suggested name', dw_col_platform: 'Suggested platform', dw_col_origen: 'Seen by',
      dw_col_if: 'Interface', dw_col_state: 'State',
      dw_lbl_user: 'SSH user', dw_lbl_pass: 'SSH password', dw_lbl_group: 'Group (optional)',
      dw_lbl_tipo: 'Type', dw_lbl_driver: 'Driver (optional)', dw_driver_auto: 'auto (from suggestion)',
      dw_cred_note: 'Honest warning: without credentials the device is registered but cannot be polled until you edit it. The backend never copies them silently.',
      dw_driver_needed: 'A selected candidate has no platform suggestion: pick a manual driver or the import will fail with "Platform unidentified".',
      dw_import_btn: 'Import', dw_importing: 'Importing…',
      dw_import_done: '{n} of {m} imported', dw_created: 'Created (green)', dw_existed: 'Already existed', dw_failed: 'Failed (red)',
      dw_after_import: 'Candidates remain "Proposed" in the last scan; the inventory is never modified by the scan and a second import answers "already existed".',
      dw_cancel_back: 'Back to candidates',
      dw_st_ok: 'Scanned', dw_st_sin_vecinos: 'No neighbors', dw_st_sin_soporte: 'No support for LLDP/CDP',
      dw_st_sin_cred: 'No credentials', dw_st_error: 'Not verifiable'
    });
    Object.assign(I18N.es, {
      dw_open: 'Descubrir equipos', dw_title: 'Wizard de auto-discovery',
      dw_close: 'Cerrar', dw_step_n: 'Paso {n}',
      dw_step1_desc: 'Elige los equipos origen a los que preguntar vecinos LLDP/CDP. No se importa nada sin confirmación explícita.',
      dw_metodo: 'Método de descubrimiento', dw_cdp_note: 'CDP solo en drivers ios/nxos/iosxr/eos',
      dw_origins: 'Equipos origen', dw_pick_all: 'Marcar todo / nada',
      dw_scan_btn: 'Escanear ahora', dw_scanning: 'Escaneando…', dw_scanning_note: 'Hasta ~25 s. No cierres ni cambies de vista. Un equipo caído no es un error global: se detalla por origen abajo.',
      dw_no_origins: 'Sin orígenes disponibles — registra equipos primero en el inventario.',
      dw_pick_origins: 'Selecciona al menos un equipo origen (o usa Marcar todo)',
      dw_scan_done: 'Escaneo terminado', dw_res_origins: 'orígenes', dw_res_failed: 'con error',
      dw_next_candidates: 'Ver candidatos', dw_no_candidates: 'Sin candidatos en este escaneo',
      dw_back_step1: 'Volver a orígenes', dw_rescan: 'Escanear de nuevo (el último escaneo queda como está)',
      dw_reused: 'Reutilizando el último escaneo que conserva este proceso (se pierde al reiniciar la API)',
      dw_in_inventory: 'Ya en inventario', dw_proposed: 'Propuesto', dw_no_platform: 'Plataforma sin identificar',
      dw_next_confirm: 'Confirmar selección', dw_confirm_title: 'Importar {n} candidato(s) al inventario',
      dw_col_name: 'Nombre sugerido', dw_col_platform: 'Plataforma sugerida', dw_col_origen: 'Visto por',
      dw_col_if: 'Interfaces', dw_col_state: 'Estado',
      dw_lbl_user: 'Usuario SSH', dw_lbl_pass: 'Contraseña SSH', dw_lbl_group: 'Grupo (opcional)',
      dw_lbl_tipo: 'Tipo', dw_lbl_driver: 'Driver (opcional)', dw_driver_auto: 'auto (desde la sugerencia)',
      dw_cred_note: 'Aviso honesto: sin credenciales el equipo queda registrado pero no podrá ser sondeado hasta editarlo. El backend nunca las copia por su cuenta.',
      dw_driver_needed: 'Algún candidato seleccionado no tiene plataforma sugerida: elige un driver manual o la importación fallará con "Plataforma no identificada".',
      dw_import_btn: 'Importar', dw_importing: 'Importando…',
      dw_import_done: '{n} de {m} importados', dw_created: 'Creados (verde)', dw_existed: 'Ya existían', dw_failed: 'Fallidos (rojo)',
      dw_after_import: 'Los candidatos siguen en estado "Propuesto" en el último escaneo; el escaneo nunca modifica el inventario y un segundo import responde "ya existían".',
      dw_cancel_back: 'Volver a candidatos',
      dw_st_ok: 'Escaneado', dw_st_sin_vecinos: 'Sin vecinos', dw_st_sin_soporte: 'Sin soporte para LLDP/CDP',
      dw_st_sin_cred: 'Sin credenciales', dw_st_error: 'No verificable'
    });

    Object.assign(I18N.en, {
      nav_sites: 'Sites', view_sites: 'Remote Sites',
      sites_empty: 'No site has contacted yet', sites_help: 'Data measured from the remote site node; not directly comparable to central polling.',
      sites_view_devices: 'Devices', sites_last_seen: 'last contact', sites_stale: 'without contact',
      sites_st_ok: 'Responds', sites_st_noresp: 'No probe reply', sites_st_nodata: 'No current data',
      sites_last_known: 'last known state', sites_unmatched: 'Not in inventory',
      sites_buf_pending: '{n} samples pending since {ts}', sites_buf_dropped: '{n} samples discarded; gap since {ts}',
      sites_buf_disklow: 'Probe paused: low disk', sites_skew: 'Collector clock off by {s} s', sites_epoch: 'Buffer restarted on the site',
      sites_no_devs: 'No devices reported by this site', sites_col_dev: 'Device', sites_col_source: 'Source',
      sites_col_state: 'State', sites_col_lat: 'Probe latency (ms)', sites_col_last: 'Last sample', sites_col_inv: 'Inventory',
      sites_inv_ok: 'In inventory', sites_inv_no: 'Not in inventory', sites_inv_unv: 'Inventory unverified',
      sites_hist_btn: 'History', sites_hist_title: 'History (6 h, raw)',
      sites_hist_nodata: 'No history yet', sites_hist_less: 'reduce hours', sites_hist_empty: 'Window contains no samples',
      sites_hist_caption: 'Raw points: a failure is its own failing sample; gaps are left as gaps (no interpolation).',
      nav_retention: 'Retention', view_retention: 'Retention & housekeeping',
      ret_unmeasured: 'unmeasured', ret_audit: 'Audit trail', ret_backups: 'Local backups',
      ret_records: 'Records', ret_oldest: 'Oldest', ret_files: 'Files', ret_usage: 'Usage',
      ret_name: 'Name', ret_size: 'Size (bytes)', ret_mtime: 'Last modified UTC',
      ret_prune_title: 'Purge audit trail', ret_days: 'Days',
      ret_dry_btn: 'Simulate', ret_dry_result: '{n} records would be purged ({d} days).',
      ret_col_ts: 'Date', ret_col_action: 'Action', ret_col_user: 'User',
      ret_still_skip: 'Nothing deleted yet — preview only.',
      ret_exec_btn: 'Purge', ret_cancel_btn: 'Cancel', ret_confirm_msg: 'Delete {n} audit records older than {d} days? This cannot be undone.',
      ret_exec_now: 'Delete now', ret_done: '{n} records purged', ret_kept: '{n} records kept',
      ret_pick_days: 'Choose a range first', ret_viewer_note: 'Purge requires the admin role — inventory is visible to you.',
      scp_all: 'All sites', scp_none: 'No groups allowed',
      scp_title: 'Operating scope', scp_no_main: 'no main group', scp_groups_unverified: 'Group list could not be verified — you may still type/select another source.',
      scp_note: 'Scope applies on next login (existing tokens keep permissions until expiry).',
      scp_save: 'Save scope', scp_clear: 'Remove scope (full access)',
      scp_clear_confirm: 'A user without scope sees EVERYTHING — remove the restriction?', scp_saved: 'Scope saved',
      scp_relogin: 'The user must log in again to receive the new scope in their token.', scp_cleared: 'Scope removed'
    });
    Object.assign(I18N.es, {
      nav_sites: 'Sitios', view_sites: 'Sitios remotos',
      sites_empty: 'Ningún site ha contactado todavía', sites_help: 'Datos medidos desde el nodo del site; no comparables directamente con el sondeo del centro.',
      sites_view_devices: 'Dispositivos', sites_last_seen: 'último contacto', sites_stale: 'sin contacto',
      sites_st_ok: 'Responde', sites_st_noresp: 'No responde al sondeo', sites_st_nodata: 'Sin dato actual',
      sites_last_known: 'último estado conocido', sites_unmatched: 'No inventariado',
      sites_buf_pending: '{n} muestras pendientes desde {ts}', sites_buf_dropped: 'Se descartaron {n} muestras; hueco desde {ts}',
      sites_buf_disklow: 'Sonda pausada: disco bajo', sites_skew: 'Reloj del colector desviado {s} s', sites_epoch: 'Buffer reiniciado en el site',
      sites_no_devs: 'Ningún dispositivo reportado por este site', sites_col_dev: 'Equipo', sites_col_source: 'Fuente',
      sites_col_state: 'Estado', sites_col_lat: 'Latencia de sonda (ms)', sites_col_last: 'Última muestra', sites_col_inv: 'Inventario',
      sites_inv_ok: 'En inventario', sites_inv_no: 'No inventariado', sites_inv_unv: 'Inventario no verificable',
      sites_hist_btn: 'Histórico', sites_hist_title: 'Histórico (6 h, crudo)',
      sites_hist_nodata: 'Sin histórico aún', sites_hist_less: 'reduce las horas', sites_hist_empty: 'La ventana no contiene muestras',
      sites_hist_caption: 'Puntos crudos: un fallo es su propia muestra fallida; los huecos se dejan como huecos (sin interpolación).',
      nav_retention: 'Retención', view_retention: 'Retención y housekeeping',
      ret_unmeasured: 'sin medir', ret_audit: 'Auditoría', ret_backups: 'Backups locales',
      ret_records: 'Registros', ret_oldest: 'Más viejo', ret_files: 'Archivos', ret_usage: 'Uso',
      ret_name: 'Nombre', ret_size: 'Tamaño (bytes)', ret_mtime: 'Última modificación UTC',
      ret_prune_title: 'Purgar auditoría', ret_days: 'Días',
      ret_dry_btn: 'Simular', ret_dry_result: 'Se borrarían {n} registros ({d} días).',
      ret_col_ts: 'Fecha', ret_col_action: 'Acción', ret_col_user: 'Usuario',
      ret_still_skip: 'Aún no se borró nada — solo previsualización.',
      ret_exec_btn: 'Purgar', ret_cancel_btn: 'Cancelar', ret_confirm_msg: '¿Borrar {n} registros de auditoría con más de {d} días? Esto NO se puede deshacer.',
      ret_exec_now: 'Borrar ahora', ret_done: '{n} registros purgados', ret_kept: '{n} registros conservados',
      ret_pick_days: 'Elige una ventana primero', ret_viewer_note: 'La purga requiere rol admin — el inventario sí es visible para ti.',
      scp_all: 'Todos los sitios', scp_none: 'Ningún grupo autorizado',
      scp_title: 'Alcance operativo', scp_no_main: 'sin grupo principal', scp_groups_unverified: 'No se pudo verificar la lista de grupos — puedes seguir guardando el valor actual.',
      scp_note: 'El alcance aplica en el próximo inicio de sesión (los tokens existentes conservan permisos hasta vencer).',
      scp_save: 'Guardar scope', scp_clear: 'Quitar scope (acceso total)',
      scp_clear_confirm: 'Un usuario sin scope ve TODO — ¿quitar la restricción?', scp_saved: 'Scope guardado',
      scp_relogin: 'El usuario debe iniciar sesión de nuevo para recibir el nuevo scope en su token.', scp_cleared: 'Scope quitado'
    });

    // i18n — SFTP (estados honestos por causa, contrato docs/handoffs/sftp-ui.md)
    Object.assign(I18N.en, {
      sftp_close: 'Dismiss', sftp_route_label: 'REMOTE PATH:', sftp_up_level: 'Up one level',
      sftp_go: 'Go / Reload', sftp_refresh: 'Refresh', sftp_upload: 'Upload file',
      sftp_crumb_label: 'PATH:', sftp_blocked_hint: 'SFTP is not available on this device',
      sftp_up_hint: 'Go up one directory level', sftp_refresh_hint: 'Reload the current folder',
      sftp_path_aria: 'Remote path',
      sftp_summary: '{n} items ({d} folders, {f} files) in {p}',
      sftp_summary_empty: 'Empty folder at {p}',
      sftp_summary_none: 'Not verified — no listing',
      sftp_summary_loading: 'Reading folder…',
      sftp_loading: 'Connecting over SFTP and reading the directory…',
      sftp_list_fail: 'Could not read this folder',
      sftp_list_fail_d: 'No empty listing is shown: the contents of this folder are not verified.',
      sftp_empty_title: 'Empty folder',
      sftp_empty_detail: 'The device answered: this folder has no files.',
      sftp_no_dev_title: 'No device selected',
      sftp_no_dev_text: 'Pick a device in the header to browse its files over SFTP.',
      sftp_select_dev: 'Select a device first', sftp_busy: 'Another transfer is still running…',
      sftp_open: 'Open', sftp_download: 'Download',
      sftp_up_start: 'Uploading {f} ({s})…',
      sftp_up_ok: 'File uploaded',
      sftp_up_fail: 'Upload failed',
      sftp_up_unconfirmed: 'The server did not confirm the upload',
      sftp_up_unconfirmed_title: 'Upload not confirmed',
      sftp_dl_start: 'Downloading {f}…',
      sftp_dl_ok: 'Downloaded: {f}',
      sftp_dl_fail: 'Download failed',
      sftp_err_generic: 'SFTP error',
      sftp_err_auth_title: 'SSH authentication failed',
      sftp_err_auth_text: 'The device rejected the username/password from the inventory. Check the credentials — retrying with the same ones will fail again.',
      sftp_err_501_title: 'SFTP is not supported by this device',
      sftp_err_501_text: 'This device does not offer SFTP — use Terminal or Commands for CLI.',
      sftp_open_terminal: 'Open Terminal',
      sftp_err_502_title: 'Could not connect',
      sftp_err_kex_title: 'Incomplete SSH negotiation',
      sftp_err_kex_text: 'The device did not finish the SSH key exchange before the timeout — it is not a credentials problem. Do not retry in a loop: try once more in a few seconds or raise NETPULSE_SFTP_CONNECT_TIMEOUT_S on the server.',
      sftp_err_504_title: 'SSH not responding',
      sftp_err_504_text: 'Timed out connecting to {d} — unverified.',
      sftp_err_403_read: 'Permission denied while reading',
      sftp_err_403_up: 'Permission denied while writing',
      sftp_err_404_dev: 'Device not found',
      sftp_err_404_file: 'File not found',
      sftp_reload_dir: 'Reload folder',
      sftp_err_400_title: 'Path not accessible',
      sftp_back_last: 'Back to last folder',
      sftp_err_500_title: 'Internal SFTP failure',
      sftp_err_500_text: 'The server could not complete the operation. Report this to support.',
      sftp_err_generic_title: 'SFTP error',
      sftp_err_api_title: 'No connection to NetPulse',
      sftp_err_api_text: 'The request did not reach the API. No listing is shown because the result is unknown.',
      sftp_err_badres_title: 'Unreadable SFTP response',
      sftp_err_badres_text: 'The server answered 200 but without a valid listing, so the folder is not verified.'
    });
    Object.assign(I18N.es, {
      sftp_close: 'Cerrar', sftp_route_label: 'RUTA REMOTA:', sftp_up_level: 'Subir nivel',
      sftp_go: 'Ir / Recargar', sftp_refresh: 'Refrescar', sftp_upload: 'Subir Archivo',
      sftp_crumb_label: 'RUTA:', sftp_blocked_hint: 'SFTP no disponible en este equipo',
      sftp_up_hint: 'Subir un nivel de directorio', sftp_refresh_hint: 'Recargar la carpeta actual',
      sftp_path_aria: 'Ruta remota',
      sftp_summary: '{n} elementos ({d} carpetas, {f} archivos) en {p}',
      sftp_summary_empty: 'Carpeta vacía en {p}',
      sftp_summary_none: 'Sin verificar — sin listado',
      sftp_summary_loading: 'Leyendo carpeta…',
      sftp_loading: 'Conectando por SFTP y leyendo directorio…',
      sftp_list_fail: 'No se pudo leer esta carpeta',
      sftp_list_fail_d: 'No se muestra un listado vacío: el contenido de esta carpeta no está verificado.',
      sftp_empty_title: 'Carpeta vacía',
      sftp_empty_detail: 'El equipo respondió: la carpeta no tiene archivos.',
      sftp_no_dev_title: 'Sin equipo seleccionado',
      sftp_no_dev_text: 'Elige un equipo en la cabecera para explorar sus archivos por SFTP.',
      sftp_select_dev: 'Selecciona un dispositivo primero', sftp_busy: 'Aún hay una transferencia en curso…',
      sftp_open: 'Abrir', sftp_download: 'Descargar',
      sftp_up_start: 'Subiendo {f} ({s})…',
      sftp_up_ok: 'Archivo subido',
      sftp_up_fail: 'Falló la subida',
      sftp_up_unconfirmed: 'El servidor no confirmó la subida',
      sftp_up_unconfirmed_title: 'Subida no confirmada',
      sftp_dl_start: 'Descargando {f}…',
      sftp_dl_ok: 'Descargado: {f}',
      sftp_dl_fail: 'Falló la descarga',
      sftp_err_generic: 'Error SFTP',
      sftp_err_auth_title: 'Autenticación SSH fallida',
      sftp_err_auth_text: 'Revisa las credenciales del equipo: rechazó el usuario/contraseña del inventario. Reintentar con las mismas volverá a fallar.',
      sftp_err_501_title: 'SFTP no soportado por este equipo',
      sftp_err_501_text: 'Este equipo no ofrece SFTP — usa Terminal o Comandos para CLI.',
      sftp_open_terminal: 'Abrir Terminal',
      sftp_err_502_title: 'No se pudo conectar',
      sftp_err_kex_title: 'Negociación SSH incompleta',
      sftp_err_kex_text: 'El equipo no terminó el intercambio de claves SSH antes del timeout — no es un problema de credenciales. No reintentar en bucle: prueba una vez más en unos segundos o sube NETPULSE_SFTP_CONNECT_TIMEOUT_S en el servidor.',
      sftp_err_504_title: 'Sin respuesta SSH',
      sftp_err_504_text: 'Tiempo de espera agotado conectando a {d} — sin verificar.',
      sftp_err_403_read: 'Permiso denegado al leer',
      sftp_err_403_up: 'Permiso denegado al escribir',
      sftp_err_404_dev: 'Equipo no encontrado',
      sftp_err_404_file: 'Archivo no encontrado',
      sftp_reload_dir: 'Recargar carpeta',
      sftp_err_400_title: 'Ruta no accesible',
      sftp_back_last: 'Volver a la última carpeta',
      sftp_err_500_title: 'Fallo interno de SFTP',
      sftp_err_500_text: 'El servidor no pudo completar la operación. Regístralo para soporte.',
      sftp_err_generic_title: 'Error SFTP',
      sftp_err_api_title: 'Sin conexión con NetPulse',
      sftp_err_api_text: 'La petición no llegó a la API. No se muestra listado porque el resultado es desconocido.',
      sftp_err_badres_title: 'Respuesta SFTP ilegible',
      sftp_err_badres_text: 'El servidor respondió 200 pero sin un listado válido; la carpeta no está verificada.'
    });

    // i18n — Terminal SSH (contrato docs/handoffs/terminal.md: eventos JSON, heartbeat, cierres honestos)
    Object.assign(I18N.en, {
      term_connect: 'Connect', term_disconnect: 'Disconnect',
      term_copy: 'Copy', term_copy_hint: 'Copy the selected terminal text to the clipboard',
      term_clear: 'Clear', term_clear_hint: 'Clear this tab’s terminal buffer',
      term_copy_none: 'Nothing selected to copy', term_copied: 'Copied to clipboard', term_copy_fail: 'Could not copy',
      term_search_ph: 'Search scrollback…', term_search_aria: 'Search the terminal scrollback',
      term_search_title: '{n} match(es) for “{q}”', term_search_none: 'No matches for “{q}”',
      term_search_no_session: 'No console tab open — nothing to search yet.',
      term_search_hint: 'Run a command in this tab to fill the scrollback.',
      term_banner_close: 'Close', term_close_tab: 'Close tab', term_tabs_aria: 'Open console tabs',
      term_connected_status: 'Connected to {d} ({h}:{p})', term_connecting: 'Connecting to {d}…',
      term_disconnected: 'Disconnected', term_need_device: 'Select a device first',
      term_no_xterm: 'xterm.js failed to load correctly',
      term_max_tabs: 'Only {n} consoles can be open at once — close a tab first.',
      term_err_ssh_auth: 'SSH credentials rejected', term_err_ssh_connect: 'Could not connect',
      term_err_devnf: 'Device not found', term_err_unsupported: 'Terminal unavailable on the server',
      term_err_internal: 'Internal failure', term_err_api_hint: 'Note: the inventory points to the management API, not to SSH.',
      term_expired_title: 'Session expired', term_expired_text: 'The token is invalid or expired — sign in again. No automatic retry.',
      term_closed_eof: 'Session ended by the device', term_closed_idle: 'Session closed by inactivity — reconnect to continue',
      term_closed_operator: 'Session closed — closed by you', term_closed_unreachable: 'Device unreachable',
      term_closed_devnf: 'Device not found', term_closed_internal: 'Internal server failure',
      term_closed_generic: 'Session closed', term_reason: 'Server reason: “{r}”',
      term_last_error: 'Detail of the previous error: {d}',
      term_reconnecting: 'Reconnecting… (attempt {n}/6)', term_retry_stop: 'Stop retries', term_retry_manual: 'Retry',
      term_no_connection: 'No connection to {d} — not verifiable',
      cmd_open_console: 'Open in console'
    });
    Object.assign(I18N.es, {
      term_connect: 'Conectar', term_disconnect: 'Desconectar',
      term_copy: 'Copiar', term_copy_hint: 'Copiar el texto seleccionado de la terminal al portapapeles',
      term_clear: 'Limpiar', term_clear_hint: 'Limpiar el buffer de esta pestaña',
      term_copy_none: 'No hay selección para copiar', term_copied: 'Copiado al portapapeles', term_copy_fail: 'No se pudo copiar',
      term_search_ph: 'Buscar en scrollback…', term_search_aria: 'Buscar en el scrollback de la terminal',
      term_search_title: '{n} coincidencia(s) por “{q}”', term_search_none: 'Sin coincidencias por “{q}”',
      term_search_no_session: 'Sin pestaña de consola abierta — nada que buscar todavía.',
      term_search_hint: 'Ejecuta un comando en esta pestaña para llenar el scrollback.',
      term_banner_close: 'Cerrar', term_close_tab: 'Cerrar pestaña', term_tabs_aria: 'Pestañas de consola abiertas',
      term_connected_status: 'Conectado a {d} ({h}:{p})', term_connecting: 'Conectando a {d}…',
      term_disconnected: 'Desconectado', term_need_device: 'Selecciona un dispositivo primero',
      term_no_xterm: 'xterm.js no cargó correctamente',
      term_max_tabs: 'Solo se pueden abrir {n} consolas a la vez — cierra una pestaña.',
      term_err_ssh_auth: 'Credenciales SSH rechazadas', term_err_ssh_connect: 'No se pudo conectar',
      term_err_devnf: 'Equipo no encontrado', term_err_unsupported: 'Terminal no disponible en el servidor',
      term_err_internal: 'Fallo interno', term_err_api_hint: 'Aviso: el inventario apunta a la API de gestión, no a SSH.',
      term_expired_title: 'Sesión caducada', term_expired_text: 'El token es inválido o venció — vuelve a iniciar sesión. Sin reintentos automáticos.',
      term_closed_eof: 'Sesión finalizada por el equipo', term_closed_idle: 'Sesión cerrada por inactividad — reconéctate',
      term_closed_operator: 'Sesión cerrada — cierre del operador', term_closed_unreachable: 'Equipo inalcanzable',
      term_closed_devnf: 'Equipo no encontrado', term_closed_internal: 'Fallo interno del servidor',
      term_closed_generic: 'Sesión cerrada', term_reason: 'Motivo del servidor: “{r}”',
      term_last_error: 'Detalle del error previo: {d}',
      term_reconnecting: 'Reconectando… (intento {n}/6)', term_retry_stop: 'Detener reintentos', term_retry_manual: 'Reintentar',
      term_no_connection: 'Sin conexión con {d} — no verificable',
      cmd_open_console: 'Abrir en consola'
    });

    // Alta de equipo con grupo + estado recordado de vistas (Tarjeta 9B/9C)
    Object.assign(I18N.en, {
      inv_group: 'Group',
      inv_group_none: '— No group —',
      inv_group_manual_opt: 'Type group manually…',
      inv_group_unverified: 'Group list not verifiable — type the group manually',
      inv_group_empty_warn: 'Device without a group: users with scope will not be able to operate it',
      inv_403_nocreate: '403 — device NOT created. Backend detail:',
      rep_filters_restored: 'Filters restored from your last visit (per user). No report was run automatically — use “Consult last” or “Regenerate”.'
    });
    Object.assign(I18N.es, {
      inv_group: 'Grupo',
      inv_group_none: '— Sin grupo —',
      inv_group_manual_opt: 'Escribir grupo a mano…',
      inv_group_unverified: 'Lista de grupos no verificable — escribe el grupo a mano',
      inv_group_empty_warn: 'Equipo sin grupo: los usuarios con scope no podrán operarlo',
      inv_403_nocreate: '403 — el equipo NO se creó. Detail del backend:',
      rep_filters_restored: 'Filtros restaurados de tu última visita (por usuario). No se lanzó ningún reporte automáticamente — usa «Consultar último» o «Regenerar».'
    });

    function setLang(lang) {
      currentLang = lang;
      localStorage.setItem('np_lang', lang);
      document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (I18N[lang] && I18N[lang][key]) {
          const decoration = el.querySelector(':scope > svg');
          const label = I18N[lang][key];
          setIconText(el, label);
          if (decoration && !label.includes('<svg')) el.prepend(decoration, ' ');
        }
      });
      // Tooltips y etiquetas accesibles (atributos) también se traducen.
      document.querySelectorAll('[data-i18n-title]').forEach(el => {
        const key = el.getAttribute('data-i18n-title');
        if (I18N[lang] && I18N[lang][key]) el.title = I18N[lang][key];
      });
      document.querySelectorAll('[data-i18n-aria]').forEach(el => {
        const key = el.getAttribute('data-i18n-aria');
        if (I18N[lang] && I18N[lang][key]) el.setAttribute('aria-label', I18N[lang][key]);
      });
      const sel = document.getElementById('lang-select');
      if (sel) sel.value = lang;
      const search = document.getElementById('sb');
      if (search) search.placeholder = I18N[lang]?.search_placeholder || I18N.en.search_placeholder;
      const termSearch = document.getElementById('term-search');
      if (termSearch) termSearch.placeholder = I18N[lang]?.term_search_ph || I18N.en.term_search_ph;
      if (overviewState) updateDashboardHero();
    }

    function __t(k) { return (I18N[currentLang] && I18N[currentLang][k]) || (I18N['en'] && I18N['en'][k]) || k; }

    function toast(m, ty = 'info', d = 3000) { const e = document.createElement('div'); e.className = `toast ${ty}`; setIconText(e, m); document.body.appendChild(e); setTimeout(() => e.remove(), d) }
    function h() { return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' } }

    // ── INTERCEPTOR 401 GLOBAL ──
    // Un solo punto de manejo: cualquier petición de la app con sesión activa que
    // reciba 401 de la API devuelve al login UNA sola vez, conservando location.hash
    // (init() restaura la vista del hash tras re-autenticar). No se reescribieron los
    // fetch existentes: el wrapper pasa por todos los fetch de window.fetch.
    const _npOrigFetch = window.fetch.bind(window);
    window.fetch = async function (input, opts) {
      const resp = await _npOrigFetch(input, opts);
      try {
        const url = typeof input === 'string' ? input : (input && input.url) || '';
        if (resp && resp.status === 401 && t && !_np401Done && url.startsWith(A)
          && !url.includes('/auth/login') && !url.includes('/auth/refresh') && !url.includes('/auth/me')) {
          // Excepción deliberada: /api/sftp/ usa 401 para «autenticación SSH fallida del equipo»
          // (contrato docs/handoffs/sftp-ui.md), no para sesión caducada. Se comprueba el `detail`
          // para no confundir ambos casos.
          if (!url.includes('/sftp/')) {
            sessionExpired();
          } else {
            let deviceAuth = false;
            try {
              const body = await resp.clone().json();
              deviceAuth = /autenticaci[oó]n ssh/i.test(String((body && body.detail) || ''));
            } catch (e) { deviceAuth = false; }
            if (!deviceAuth) sessionExpired();
          }
        }
      } catch (e) { }
      return resp;
    };

    function sessionExpired(msg) {
      if (_np401Done) return;
      logout(); // limpia token/intervalos/currentUser sin tocar location.hash
      _np401Done = true;
      showLoginError(msg || __t('sess_expired'));
    }

    // Latidos periódicos de la app: solo hacen trabajo con sesión activa.
    function _npTick(fn, ms) { return setInterval(() => { if (t) fn() }, ms) }

    // ── RENOVACIÓN SILENCIOSA DE SESIÓN ──
    // POST /api/auth/refresh con el JWT vigente a los ~25 min (exp por defecto 30 min,
    // margen de 5 min como pide el contrato en docs/handoffs/auth-session.md).
    // 200 → se reprograma con el token nuevo. 401 → sesión no renovable: login UNA vez.
    // Fallo de red u otro error → NO desloguea: reintenta en el próximo ciclo.
    const NP_REFRESH_MS = 25 * 60 * 1000;
    function scheduleRefresh() {
      if (_refreshTimer) { clearTimeout(_refreshTimer); _refreshTimer = null; }
      if (!t || t.startsWith('np_')) return; // las API keys np_ no son sesiones: el refresh las rechaza
      _refreshTimer = setTimeout(refreshSession, NP_REFRESH_MS);
    }
    function stopRefresh() {
      if (_refreshTimer) { clearTimeout(_refreshTimer); _refreshTimer = null; }
    }
    async function refreshSession() {
      if (!t || t.startsWith('np_')) return;
      try {
        const r = await _npOrigFetch(`${A}/auth/refresh`, { method: 'POST', headers: { Authorization: `Bearer ${t}` } });
        if (r.ok) {
          const d = await r.json();
          t = d.access_token;
          localStorage.setItem('np_token', t);
          scheduleRefresh();
        } else if (r.status === 401) {
          sessionExpired();
        }
        // Semáforo honesto: un fallo transitorio no invalida la sesión vigente.
      } catch (e) { /* sin red: reintento en el próximo ciclo, sin cerrar sesión */ }
    }

    function showLoginError(message) {
      const error = document.getElementById('login-error');
      error.textContent = message;
      error.style.display = 'block';
    }

    function enterSession(me, fallbackName) {
      // Punto común de entrada a la app: lo usan el login manual y la restauración
      // de sesión desde localStorage tras cargar la página.
      currentUser = me;
      const role = me.role || 'viewer';
      _np401Done = false; // nueva sesión: el aviso global de 401 vuelve a estar disponible
      document.getElementById('pass').value = '';
      document.getElementById('login-error').style.display = 'none';
      document.getElementById('login-screen').style.display = 'none'; document.getElementById('main').style.display = 'block';
      const uname = me.username || fallbackName || 'user';
      document.getElementById('user-avatar').textContent = uname.slice(0, 2).toUpperCase();
      document.getElementById('user-name').textContent = uname;
      document.getElementById('user-role').textContent = role;
      document.getElementById('dd-name').textContent = uname;
      document.getElementById('dd-role').textContent = 'Rol: ' + role;
      document.getElementById('info-api').textContent = 'Conectada';
      // Todos los grupos visibles para cualquier usuario autenticado.
      // El RBAC del backend sigue validando cada acción.
      document.querySelectorAll('.sidebar-s').forEach(el => { el.style.display = 'block'; });
      scheduleRefresh();
      init()
    }

    // ── ESTADO RECORDADO DE VISTAS (por usuario) ──
    // Filtros e IDs guardados en localStorage con prefijo por username, para no
    // mezclar usuarios en un navegador compartido. JAMÁS se guardan datos sensibles
    // (solo fechas, nombre de grupo, device_ids y site_id). Se limpian en logout()
    // junto con np_token (logout y sessionExpired pasan por aquí).
    function _npStateKey(key) {
      const u = (currentUser && currentUser.username) || '';
      return `np_u:${u}:${key}`;
    }
    function _npStateGet(key) {
      try {
        const raw = localStorage.getItem(_npStateKey(key));
        return raw ? JSON.parse(raw) : null;
      } catch (e) { return null; }
    }
    function _npStateSet(key, value) {
      try { localStorage.setItem(_npStateKey(key), JSON.stringify(value)); } catch (e) { }
    }
    function _npStateClear() {
      const prefix = `np_u:${(currentUser && currentUser.username) || ''}:`;
      try {
        const borran = [];
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          if (k && k.startsWith(prefix)) borran.push(k);
        }
        borran.forEach(k => localStorage.removeItem(k));
      } catch (e) { }
    }

    function logout() {
      stopRefresh();
      _npStateClear(); // filtros recordados del usuario, junto con np_token
      repState = null; // el estado en memoria no debe colarse al siguiente usuario
      _invGroupsCache = null;
      t = ''; currentUser = null; localStorage.removeItem('np_token');
      if (window._rInt) { clearInterval(window._rInt); window._rInt = null }
      if (window._mInt) { clearInterval(window._mInt); window._mInt = null }
      if (window.dLiveT) { clearInterval(window.dLiveT); window.dLiveT = null }
      document.getElementById('main').style.display = 'none'; document.getElementById('login-screen').style.display = 'flex';
      const dd = document.getElementById('user-dd'); if (dd) dd.classList.remove('open');
      document.getElementById('login-error').style.display = 'none'
    }

    // La invocación queda en dashboard.html, en el punto original del inline,
    // para no adelantar el fetch de /auth/me (el cuerpo vive aquí).
    // ── RESTAURAR SESIÓN AL CARGAR LA PÁGINA ──
    // Con 'np_token' vigente se entra directo (mismo flujo que login(): currentUser,
    // avatar, sidebar, init()); sin sesión válida se muestra el login con el aviso.
    async function restoreSession() {
      const saved = localStorage.getItem('np_token');
      if (!saved) return;
      t = saved;
      try {
        const r = await fetch(`${A}/auth/me`, { headers: { Authorization: `Bearer ${t}` } });
        if (r.ok) {
          enterSession(await r.json());
        } else {
          // El token venció o fue invalidado con la pestaña cerrada: limpiar y pedir credenciales.
          t = ''; currentUser = null; localStorage.removeItem('np_token');
          showLoginError(__t('sess_expired'));
        }
      } catch (e) {
        // Sin red al arrancar NO es sesión expirada: se conserva 'np_token' y la
        // próxima recarga (o el login manual) la reutiliza.
        t = '';
      }
    }

// ── EXPOSICIÓN GLOBAL EXPLÍCITA ──
// Los onclick/onchange del HTML (onclick="logout()", onchange="setLang(this.value)")
// y los futuros archivos de vista resuelven estos nombres por window. Como
// funciones de top-level de un script clásico ya estarían colgadas de window,
// la asignación explícita es el contrato del core (no conectar window.t: 't'
// es el token de sesión, no el traductor — el traductor en esta base es __t).
window.logout = logout;
window.setLang = setLang;
window.sessionExpired = sessionExpired;
window.showLoginError = showLoginError;
window.enterSession = enterSession;
window.restoreSession = restoreSession;
window.scheduleRefresh = scheduleRefresh;
window.stopRefresh = stopRefresh;
window.refreshSession = refreshSession;
window._npTick = _npTick;
window.toast = toast;
window.h = h;
window.__t = __t;
window._npStateKey = _npStateKey;
window._npStateGet = _npStateGet;
window._npStateSet = _npStateSet;
window._npStateClear = _npStateClear;
