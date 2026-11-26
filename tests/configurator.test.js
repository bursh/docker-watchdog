'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {generate,DEFAULTS,PRESETS} = require('../docs/configurator.js');
test('default preserves legacy http/project restart',()=>{
  const yaml=generate({}); assert.match(yaml,/watchdog.check: "http"/); assert.match(yaml,/watchdog.mode: "restart"/); assert.match(yaml,/watchdog.project_failure_threshold: "2"/);
});
test('healthcheck only excludes HTTP fields',()=>{
  const yaml=generate({check:'healthcheck',healthcheck_failures:1,max_restarts:0,mode:'container-restart'});
  assert.doesNotMatch(yaml,/watchdog\.(path|scheme|host|http_timeout)/); assert.match(yaml,/watchdog.healthcheck_failures: "1"/); assert.match(yaml,/watchdog.max_restarts: "0"/);
});
test('both includes both checks',()=>{const y=generate({check:'both',path:'/ready',host:'example.org'});assert.match(y,/watchdog.path: "\/ready"/);assert.match(y,/watchdog.healthcheck_failures: "0"/);});
test('escalation normalizes whitespace and supports container modes',()=>{assert.match(generate({recovery:'container-restart, container-recreate, restart'}),/"container-restart,container-recreate,restart"/);assert.throws(()=>generate({recovery:'restart,unknown'}));});
test('zero disables promotion, never emits invalid project mode',()=>{assert.match(generate({project_failure_threshold:0}),/watchdog.project_failure_threshold: "0"/);assert.throws(()=>generate({project_recovery_mode:'container-restart'}));});
test('invalid intervals and HTTP paths rejected',()=>{for(const v of [0,-1,1.5,'','1e5'])assert.throws(()=>generate({interval:v}));for(const path of ['foo','//other','/hello world','/a\nb'])assert.throws(()=>generate({path}));});
test('Compose fragment indentation and labels',()=>{const y=generate({...PRESETS.nginx,output:'service'});assert.match(y,/services:\n  nginx:\n    image: "nginx:alpine"\n    ports:\n      - "8080:80"\n    labels:\n      watchdog.enabled: "true"/);});
test('private healthchecked service and Compose variable escaping',()=>{const y=generate({...PRESETS.backend,output:'service',health_command:'test "$READY" = yes'});assert.doesNotMatch(y,/    ports:/);assert.match(y,/\$\$READY/);assert.match(y,/healthcheck:/);});
test('reject invalid service and missing healthcheck example',()=>{assert.throws(()=>generate({output:'service',service:'../bad'}));assert.throws(()=>generate({check:'healthcheck',output:'service'}));});
test('presets all generate labels',()=>{for(const p of Object.values(PRESETS))assert.match(generate({...DEFAULTS,...p}),/watchdog.enabled: "true"/);});
