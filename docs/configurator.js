/* Local-only Docker Compose label generator. No network or storage. */
'use strict';
const CHECKS = ['http', 'healthcheck', 'both', 'auto'];
const PROJECT_MODES = ['restart', 'force-recreate', 'down-up'];
const MODES = [...PROJECT_MODES, 'container-restart', 'container-recreate'];
const DEFAULTS = Object.freeze({
  check: 'http', mode: 'restart', recovery: '', scheme: 'http', path: '/', host: '',
  interval: 60, failures: 3, healthcheck_failures: 0, cooldown: 120, startup_grace: 60,
  max_restarts: 3, window: 3600, http_timeout: 5, command_timeout: 180,
  project_failure_threshold: 2, project_recovery_mode: 'restart', output: 'labels',
  service: 'web', image: 'nginx:alpine', port: '8080:80', health_command: ''
});
const INTS = {
  interval: [1, 86400], failures: [1, 1000], healthcheck_failures: [0, 1000],
  cooldown: [0, 86400], startup_grace: [0, 86400], max_restarts: [0, 100000],
  window: [1, 604800], http_timeout: [1, 3600], command_timeout: [1, 86400],
  project_failure_threshold: [0, 10000]
};
const PRESETS = {
  nginx: {check: 'http', mode: 'container-restart', service: 'nginx', image: 'nginx:alpine', port: '8080:80', path: '/'},
  wordpress: {check: 'http', mode: 'restart', service: 'web', image: 'wordpress:apache', port: '8080:80', path: '/'},
  backend: {check: 'healthcheck', mode: 'container-restart', service: 'backend', image: 'your-backend:tag', port: '', health_command: 'curl -fsS http://127.0.0.1:8080/health'},
  database: {check: 'healthcheck', mode: 'container-restart', service: 'mysql', image: 'mysql:8', port: '', health_command: 'mysqladmin ping -h 127.0.0.1 --silent', startup_grace: 180}
};
function clean(value, field) {
  const str = String(value ?? '');
  if (/[\u0000-\u001f\u007f]/.test(str)) throw new Error(`${field} must not contain control characters.`);
  return str;
}
function generate(input) {
  const c = {...DEFAULTS, ...input};
  const labels = {'watchdog.enabled': 'true'};
  const choice = (key, values) => {
    if (!values.includes(c[key])) throw new Error(`Invalid ${key}.`);
    return c[key];
  };
  labels['watchdog.check'] = choice('check', CHECKS);
  if (c.check !== 'healthcheck') {
    labels['watchdog.scheme'] = choice('scheme', ['http', 'https']);
    const path = clean(c.path, 'HTTP path');
    if (!path.startsWith('/') || path.startsWith('//') || /\s/.test(path)) throw new Error('HTTP path must start with a single slash and contain no whitespace.');
    labels['watchdog.path'] = path;
    if (c.host) {
      const host = clean(c.host, 'Host header');
      if (/\s/.test(host)) throw new Error('Host header must contain no whitespace.');
      labels['watchdog.host'] = host;
    }
  }
  labels['watchdog.mode'] = choice('mode', MODES);
  if (c.recovery.trim()) {
    const recovery = c.recovery.split(',').map(v => v.trim());
    if (recovery.some(v => !MODES.includes(v))) throw new Error('Recovery sequence must contain valid comma-separated recovery modes.');
    labels['watchdog.recovery'] = recovery.join(',');
  }
  for (const [key, [min, max]] of Object.entries(INTS)) {
    if (key === 'healthcheck_failures' && c.check === 'http') continue;
    if (key === 'http_timeout' && c.check === 'healthcheck') continue;
    const value = String(c[key]);
    if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) < min || Number(value) > max)
      throw new Error(`${key} must be an integer between ${min} and ${max}.`);
    labels[`watchdog.${key}`] = String(Number(value));
  }
  labels['watchdog.project_recovery_mode'] = choice('project_recovery_mode', PROJECT_MODES);
  const labelLines = Object.entries(labels).map(([k, v]) => `  ${k}: ${JSON.stringify(v)}`);
  if (c.output === 'labels') return ['labels:', ...labelLines].join('\n') + '\n';
  if (c.output !== 'service') throw new Error('Unknown output format.');
  const service = clean(c.service, 'Compose service name');
  const image = clean(c.image, 'Image');
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(service)) throw new Error('Invalid Compose service name.');
  if (!image || /\s/.test(image)) throw new Error('Enter a valid image reference or placeholder.');
  const lines = ['# Merge this example into your existing Compose file.', '# Add application-specific environment, volumes and dependencies.', 'services:', `  ${service}:`, `    image: ${JSON.stringify(image)}`];
  if (c.check !== 'healthcheck') {
    const port = clean(c.port, 'Port mapping');
    if (port) {
      if (!/^(?:\d{1,5}:)?\d{1,5}$/.test(port) || port.split(':').some(p => Number(p)<1 || Number(p)>65535)) throw new Error('Port mapping must be e.g. 8080:80 or 80.');
      lines.push('    ports:', `      - ${JSON.stringify(port)}`);
    } else if (c.check !== 'auto') throw new Error('HTTP checks need a published TCP port.');
  }
  if (c.check !== 'http') {
    const command = clean(c.health_command, 'Docker healthcheck command');
    if (command) {
      // Compose interpolates dollar signs; double them for a literal inside the container.
      lines.push('    healthcheck:', `      test: ${JSON.stringify(['CMD-SHELL', command.replace(/\$/g, '$$$$')])}`, '      interval: 30s', '      timeout: 5s', '      retries: 3', '      start_period: 60s');
    } else if (c.check !== 'auto') throw new Error('Provide a healthcheck command for this service example, or generate labels only if the image already defines it.');
  }
  lines.push('    labels:', ...labelLines.map(v => '    '+v));
  return lines.join('\n') + '\n';
}
if (typeof module !== 'undefined' && module.exports) module.exports = {generate, DEFAULTS, PRESETS};
if (typeof document !== 'undefined') {
  const form = document.querySelector('#configurator');
  if (form) {
    const out = document.querySelector('#yaml-output');
    const message = document.querySelector('#generator-message');
    const copy = document.querySelector('#copy-yaml');
    const download = document.querySelector('#download-yaml');
    const value = name => form.elements.namedItem(name).value;
    function setForm(cfg) {
      for (const [key,v] of Object.entries(cfg)) {
        const field = form.elements.namedItem(key);
        if (field) field.value = String(v);
      }
    }
    function render() {
      const cfg = Object.fromEntries(new FormData(form).entries());
      const http = cfg.check !== 'healthcheck';
      const health = cfg.check !== 'http';
      document.querySelectorAll('[data-http]').forEach(e => {e.hidden = !http;});
      document.querySelectorAll('[data-health]').forEach(e => {e.hidden = !health;});
      document.querySelector('#service-fields').hidden = cfg.output !== 'service';
      document.querySelector('#promotion-mode').hidden = cfg.project_failure_threshold === '0';
      try {
        out.value = generate(cfg);
        copy.disabled = false; download.disabled = false;
        message.textContent = cfg.max_restarts === '0' ? 'Unlimited recovery attempts; cooldown still applies. Configure alerts before using this in production.' : 'YAML is ready. Merge into the existing Compose service and review before applying.';
        message.classList.remove('error');
      } catch(e) {
        out.value = ''; copy.disabled = true; download.disabled = true;
        message.textContent = e.message; message.classList.add('error');
      }
    }
    setForm(DEFAULTS);
    form.addEventListener('input', render);
    form.addEventListener('change', render);
    form.addEventListener('submit', e => e.preventDefault());
    document.querySelector('#preset').addEventListener('change', e => {
      setForm({...DEFAULTS,...(PRESETS[e.target.value] || {})}); render();
    });
    document.querySelector('#reset-config').addEventListener('click', () => {document.querySelector('#preset').value = ''; setForm(DEFAULTS);render();});
    document.querySelector('#theme').addEventListener('change', e => {
      if (e.target.value === 'auto') delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = e.target.value;
    });
    copy.addEventListener('click', async () => {
      try {await navigator.clipboard.writeText(out.value);message.textContent='Copied YAML.';}
      catch (_) {out.focus();out.select();message.textContent='Select and copy the YAML (Ctrl/Cmd+C).';}
    });
    download.addEventListener('click', () => {
      const url=URL.createObjectURL(new Blob([out.value],{type:'text/yaml'}));
      const a=document.createElement('a'); a.href=url; a.download=value('output')==='service'?'compose.example.yml':'watchdog-labels.yml';
      a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    render();
  }
}
