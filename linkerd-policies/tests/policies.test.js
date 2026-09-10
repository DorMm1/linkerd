'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { mkdtempSync, writeFileSync, rmSync } = require('node:fs');
const path = require('node:path');
const { parsePolicyDocuments: parse } = require('../../../service-mesh/policy-documents');

const chart = path.resolve(__dirname, '..');

function helm(args, input) {
  const result = spawnSync('helm', args, {
    encoding: 'utf8',
    input: input === undefined ? undefined : JSON.stringify(input),
    timeout: 30000,
    windowsHide: true,
  });
  assert.ifError(result.error);
  return result;
}

function render(values, extra = []) {
  const args = ['template', 'policy-test', chart, '--namespace', 'policy-system'];
  if (values !== undefined) args.push('--values', '-');
  const result = helm([...args, ...extra], values);
  assert.equal(result.status, 0, result.stderr);
  return parse(result.stdout);
}

function fixture() {
  return {
    platform: {
      destinations: {
        rabbitmq: {
          namespace: 'messaging',
          port: 5672,
          protocol: 'opaque',
          podSelector: { matchLabels: { 'app.kubernetes.io/name': 'rabbitmq' } },
        },
      },
      projects: {
        payments: {
          namespaces: ['payments-dev', 'payments-prod'],
          destinations: ['rabbitmq'],
        },
      },
    },
    projects: {
      payments: {
        grants: {
          rabbitmq: {
            serviceAccounts: [
              { namespace: 'payments-dev', name: 'worker' },
              { namespace: 'payments-prod', name: 'worker.v2' },
            ],
          },
        },
      },
    },
  };
}

test('defaults render no resources; chart lints', () => {
  assert.deepEqual(render(), []);
  const result = helm(['lint', chart, '--strict']);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test('each CR has a standalone, readable YAML template', () => {
  const templates = {
    'server.yaml': 'Server',
    'meshtlsauthentication.yaml': 'MeshTLSAuthentication',
    'authorizationpolicy.yaml': 'AuthorizationPolicy',
  };
  for (const [file, kind] of Object.entries(templates)) {
    const result = helm(['template', 'policy-test', chart, '-f', '-', '--show-only', `templates/${file}`], fixture());
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /^apiVersion: policy\.linkerd\.io\//m);
    assert.match(result.stdout, /^metadata:\r?\n  name:/m);
    assert.doesNotMatch(result.stdout, /^\{/m);
    assert.deepEqual(parse(result.stdout).map(resource => resource.kind), [kind]);
  }
});

test('denies the exact destination port and grants cross-namespace ServiceAccounts', () => {
  const resources = render(fixture());
  assert.equal(resources.length, 3);
  const server = resources.find(r => r.kind === 'Server');
  const authn = resources.find(r => r.kind === 'MeshTLSAuthentication');
  const authz = resources.find(r => r.kind === 'AuthorizationPolicy');
  assert.equal(server.apiVersion, 'policy.linkerd.io/v1beta3');
  assert.equal(server.metadata.name, 'rabbitmq');
  assert.deepEqual(server.spec, {
    accessPolicy: 'deny',
    podSelector: { matchLabels: { 'app.kubernetes.io/name': 'rabbitmq' } },
    port: 5672,
    proxyProtocol: 'opaque',
  });
  assert.equal(server.metadata.annotations['helm.sh/resource-policy'], 'keep');
  assert.equal(server.metadata.annotations['argocd.argoproj.io/sync-options'], 'Prune=false,Delete=false');
  assert.equal(server.metadata.annotations['linkerd-policy.gitops/owner'], 'platform');
  assert.deepEqual(authn.spec, {
    identityRefs: [
      { kind: 'ServiceAccount', namespace: 'payments-dev', name: 'worker' },
      { kind: 'ServiceAccount', namespace: 'payments-prod', name: 'worker.v2' },
    ],
  });
  assert.deepEqual(authz.spec, {
    targetRef: { group: 'policy.linkerd.io', kind: 'Server', name: 'rabbitmq' },
    requiredAuthenticationRefs: [{
      group: 'policy.linkerd.io',
      kind: 'MeshTLSAuthentication',
      name: authn.metadata.name,
    }],
  });
  for (const resource of resources) {
    assert.equal(resource.metadata.namespace, 'messaging');
    assert.equal(resource.metadata.labels['app.kubernetes.io/instance'], 'policy-test');
    assert.equal(resource.metadata.labels['app.kubernetes.io/managed-by'], 'Helm');
  }
  for (const resource of [authn, authz]) {
    assert.equal(resource.apiVersion, 'policy.linkerd.io/v1alpha1');
    assert.equal(resource.metadata.labels['linkerd-policy.gitops/project'], 'payments');
    assert.equal(resource.metadata.annotations['linkerd-policy.gitops/owner'], 'payments');
    assert.equal(resource.metadata.annotations['helm.sh/resource-policy'], undefined);
    assert.equal(resource.metadata.annotations['argocd.argoproj.io/sync-options'], undefined);
    assert.equal(resource.metadata.name, authn.metadata.name);
  }
});

test('every protocol is supported, with unknown as the omitted default', () => {
  const values = fixture();
  values.projects = {};
  for (const [index, protocol] of ['unknown', 'HTTP/1', 'HTTP/2', 'gRPC', 'opaque', 'TLS', undefined].entries()) {
    const destination = structuredClone(values.platform.destinations.rabbitmq);
    destination.port = 8000 + index;
    if (protocol === undefined) delete destination.protocol;
    else destination.protocol = protocol;
    values.platform.destinations[`target-${index}`] = destination;
  }
  const servers = render(values);
  for (const [id, destination] of Object.entries(values.platform.destinations)) {
    const server = servers.find(r => r.metadata.name === id);
    assert.equal(server.spec.proxyProtocol, destination.protocol ?? 'unknown');
    assert.equal(server.spec.port, destination.port);
    assert.equal(server.spec.accessPolicy, 'deny');
  }
});

test('zero grants retain deny Servers and no authorization resources', () => {
  for (const projects of [{}, { payments: { grants: {} } }]) {
    const values = fixture();
    values.projects = projects;
    const resources = render(values);
    assert.equal(resources.length, 1);
    assert.equal(resources[0].kind, 'Server');
    assert.equal(resources[0].spec.accessPolicy, 'deny');
    assert.equal(resources[0].metadata.annotations['helm.sh/resource-policy'], 'keep');
    assert.equal(resources[0].metadata.annotations['argocd.argoproj.io/sync-options'], 'Prune=false,Delete=false');
  }
});

test('each grant targets its own destination namespace and revokes independently', () => {
  const values = fixture();
  values.platform.destinations.database = {
    namespace: 'data', port: 5432, protocol: 'TLS',
    podSelector: { matchLabels: { app: 'database' } },
  };
  values.platform.projects.payments.destinations.push('database');
  values.projects.payments.grants.database = structuredClone(values.projects.payments.grants.rabbitmq);
  const before = render(values);
  assert.equal(before.length, 6);
  for (const authz of before.filter(r => r.kind === 'AuthorizationPolicy')) {
    const destination = values.platform.destinations[authz.spec.targetRef.name];
    assert.equal(authz.metadata.namespace, destination.namespace);
    assert.ok(before.some(r => r.kind === 'MeshTLSAuthentication'
      && r.metadata.name === authz.spec.requiredAuthenticationRefs[0].name
      && r.metadata.namespace === destination.namespace));
  }
  delete values.projects.payments.grants.rabbitmq;
  const after = render(values);
  assert.equal(after.length, 4);
  assert.deepEqual(after.filter(r => r.kind === 'Server'), before.filter(r => r.kind === 'Server'));
  assert.deepEqual(after.filter(r => r.kind !== 'Server'), before.filter(r => r.kind !== 'Server' && r.metadata.namespace === 'data'));
});

test('multiple values files merge platform inventory and independent project grants', () => {
  const directory = mkdtempSync(path.join(__dirname, '.values-'));
  try {
    const values = fixture();
    const platformFile = path.join(directory, 'platform.json');
    const firstProjectFile = path.join(directory, 'payments.json');
    const secondProjectFile = path.join(directory, 'analytics.json');
    values.platform.projects.analytics = {
      namespaces: ['analytics'],
      destinations: ['rabbitmq'],
    };
    writeFileSync(platformFile, JSON.stringify({ platform: values.platform }));
    writeFileSync(firstProjectFile, JSON.stringify({ projects: values.projects }));
    writeFileSync(secondProjectFile, JSON.stringify({
      projects: {
        analytics: { grants: { rabbitmq: { serviceAccounts: [{ namespace: 'analytics', name: 'reader' }] } } },
      },
    }));
    const resources = render(undefined, [
      '-f', platformFile, '-f', firstProjectFile, '-f', secondProjectFile,
    ]);
    assert.equal(resources.filter(r => r.kind === 'Server').length, 1);
    assert.equal(resources.filter(r => r.kind === 'AuthorizationPolicy').length, 2);
    assert.deepEqual(
      resources.filter(r => r.kind === 'MeshTLSAuthentication')
        .map(r => r.metadata.labels['linkerd-policy.gitops/project']).sort(),
      ['analytics', 'payments'],
    );
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('grant names avoid ambiguous joins and truncated-prefix collisions and stay stable', () => {
  const values = { platform: { destinations: {}, projects: {} }, projects: {} };
  const pairs = [
    ['a-b', 'c'],
    ['a', 'b-c'],
    ['p'.repeat(62) + 'a', 'd'.repeat(63)],
    ['p'.repeat(62) + 'b', 'd'.repeat(63)],
  ];
  for (const [project, destination] of pairs) {
    values.platform.destinations[destination] = {
      namespace: 'targets', port: 8080,
      podSelector: { matchLabels: { destination } },
    };
    values.platform.projects[project] = { namespaces: ['source'], destinations: [destination] };
    values.projects[project] = {
      grants: { [destination]: { serviceAccounts: [{ namespace: 'source', name: 'client' }] } },
    };
  }
  const resources = render(values);
  assert.deepEqual(resources, render(values));
  const names = resources.filter(r => r.kind === 'MeshTLSAuthentication').map(r => r.metadata.name);
  assert.equal(new Set(names).size, pairs.length);
  for (const name of names) {
    assert.ok(name.length <= 63);
    assert.match(name, /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/);
  }
  const keys = resources.map(r => `${r.kind}/${r.metadata.namespace}/${r.metadata.name}`);
  assert.equal(new Set(keys).size, resources.length);
  for (const authz of resources.filter(r => r.kind === 'AuthorizationPolicy')) {
    assert.ok(names.includes(authz.spec.requiredAuthenticationRefs[0].name));
  }
});

const invalidCases = [
  ['unknown destination', v => { v.projects.payments.grants.missing = v.projects.payments.grants.rabbitmq; }, /unknown destination/],
  ['unregistered project', v => { v.projects.stranger = { grants: {} }; }, /unregistered project/],
  ['unapproved destination', v => { v.platform.projects.payments.destinations = []; }, /not allowed/],
  ['unknown inventory destination', v => { v.platform.projects.payments.destinations.push('missing'); }, /unknown destination/],
  ['unapproved source namespace', v => { v.projects.payments.grants.rabbitmq.serviceAccounts[0].namespace = 'stranger'; }, /not allowed/],
  ['duplicate ServiceAccounts', v => { v.projects.payments.grants.rabbitmq.serviceAccounts.push({ name: 'worker', namespace: 'payments-dev' }); }, /duplicate|unique/],
  ['empty ServiceAccounts', v => { v.projects.payments.grants.rabbitmq.serviceAccounts = []; }, /must not be empty|at least 1/],
  ['unknown root property', v => { v.typo = true; }, /unknown property|Additional property/],
  ['unknown platform property', v => { v.platform.typo = true; }, /unknown property|Additional property/],
  ['unknown destination property', v => { v.platform.destinations.rabbitmq.accessPolicy = 'all-unauthenticated'; }, /unknown property|Additional property/],
  ['unknown platform project property', v => { v.platform.projects.payments.typo = true; }, /unknown property|Additional property/],
  ['unknown project property', v => { v.projects.payments.typo = true; }, /unknown property|Additional property/],
  ['unknown grant property', v => { v.projects.payments.grants.rabbitmq.identities = ['*']; }, /unknown property|Additional property/],
  ['unknown ServiceAccount property', v => { v.projects.payments.grants.rabbitmq.serviceAccounts[0].identity = '*'; }, /unknown property|Additional property/],
  ['unknown selector property', v => { v.platform.destinations.rabbitmq.podSelector.matchExpressions = []; }, /unknown property|Additional property/],
  ['invalid project key', v => { v.projects['Bad_Project'] = { grants: {} }; }, /DNS label|pattern/],
  ['invalid destination key', v => { v.platform.destinations['bad.destination'] = v.platform.destinations.rabbitmq; }, /DNS label|pattern/],
  ['invalid grant key', v => { v.projects.payments.grants['Bad_Grant'] = v.projects.payments.grants.rabbitmq; }, /DNS label|pattern/],
  ['invalid namespace', v => { v.platform.destinations.rabbitmq.namespace = 'bad.namespace'; }, /DNS label|pattern/],
  ['invalid ServiceAccount name', v => { v.projects.payments.grants.rabbitmq.serviceAccounts[0].name = 'Bad_Name'; }, /DNS subdomain|pattern/],
  ['empty selector', v => { v.platform.destinations.rabbitmq.podSelector.matchLabels = {}; }, /must not be empty|at least 1/],
  ['invalid label key', v => { v.platform.destinations.rabbitmq.podSelector.matchLabels = { 'bad/key/extra': 'value' }; }, /invalid label key|pattern/],
  ['overlong label prefix', v => { v.platform.destinations.rabbitmq.podSelector.matchLabels = { [`${'a'.repeat(254)}/app`]: 'value' }; }, /DNS subdomain|pattern/],
  ['non-string label value', v => { v.platform.destinations.rabbitmq.podSelector.matchLabels = { app: 123 }; }, /nonempty strings|Invalid type/],
  ['empty label value', v => { v.platform.destinations.rabbitmq.podSelector.matchLabels = { app: '' }; }, /invalid label value|length|pattern/],
  ['string port', v => { v.platform.destinations.rabbitmq.port = '5672'; }, /integer/],
  ['fractional port', v => { v.platform.destinations.rabbitmq.port = 5672.5; }, /integer/],
  ['out-of-range port', v => { v.platform.destinations.rabbitmq.port = 65536; }, /65535/],
  ['zero port', v => { v.platform.destinations.rabbitmq.port = 0; }, /between 1|equal to 1/],
  ['invalid protocol', v => { v.platform.destinations.rabbitmq.protocol = 'tcp'; }, /protocol/],
  ['duplicate namespace allowlist', v => { v.platform.projects.payments.namespaces.push('payments-dev'); }, /duplicate|unique/],
  ['duplicate destination allowlist', v => { v.platform.projects.payments.destinations.push('rabbitmq'); }, /duplicate|unique/],
  ['missing grants', v => { delete v.projects.payments.grants; }, /requires grants|grants is required/],
  ['missing ServiceAccounts', v => { delete v.projects.payments.grants.rabbitmq.serviceAccounts; }, /requires serviceAccounts|serviceAccounts is required/],
  ['non-array ServiceAccounts', v => { v.projects.payments.grants.rabbitmq.serviceAccounts = {}; }, /array/],
];

for (const [name, mutate, error] of invalidCases) {
  test(`rejects ${name}`, () => {
    const values = fixture();
    mutate(values);
    const result = helm(['template', 'policy-test', chart, '-f', '-'], values);
    assert.notEqual(result.status, 0, `unexpectedly accepted ${name}`);
    assert.match(result.stderr, error);
    assert.equal(result.stdout.trim(), '');
  });
}
