#!/usr/bin/env node
// Offline harness for Claude Code workflow scripts (.claude/workflows/*.js). No Claude calls.
//
//   node tests/workflow_harness.mjs --check .claude/workflows/sed-analyze.js [...]
//
// For every workflow it (1) checks the script shape: `export const meta = {...}` as a pure literal with name,
// description and phases, no clock/randomness/filesystem/module access, plain JavaScript that compiles as an async
// function body (the way the workflow runtime runs it; `node --check` alone does not catch errors in ESM-looking
// files); and (2) runs every case of tests/workflows/<name>.scenario.json with stubbed agent/pipeline/parallel/
// phase/log/args/budget, canned agent responses keyed by agent label, and the case's expectations.
// Exit 0 when everything passes, 1 otherwise (a JSON report is printed either way).

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SCENARIOS = path.join(HERE, 'workflows')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const FORBIDDEN = [
  [/\bDate\.now\s*\(/, 'Date.now() is not available in workflows'],
  [/\bMath\.random\s*\(/, 'Math.random() is not available in workflows'],
  [/\bnew\s+Date\s*\(\s*\)/, 'new Date() without arguments is not available in workflows'],
  [/\brequire\s*\(/, 'require() is not available in workflows'],
  [/^\s*import\s/m, 'import statements are not available in workflows'],
  [/\bimport\s*\(/, 'dynamic import() is not available in workflows'],
  [/\bprocess\./, 'process is not available in workflows'],
  [/\bworkflow\s*\(/, 'nested workflow() calls are not used by SED workflows'],
  [/isolation\s*:\s*['"]worktree['"]/, "isolation: 'worktree' is forbidden"],
]

function extractMeta(source) {
  const match = /^export const meta\s*=\s*\{/.exec(source)
  if (!match) throw new Error('the script must begin with `export const meta = {`')
  const start = match[0].length - 1
  let depth = 0
  let quote = null
  for (let i = start; i < source.length; i++) {
    const ch = source[i]
    if (quote) {
      if (ch === '\\') i++
      else if (ch === quote) quote = null
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') quote = ch
    else if (ch === '{') depth++
    else if (ch === '}') {
      depth--
      if (depth === 0) return { text: source.slice(start, i + 1), end: i + 1 }
    }
  }
  throw new Error('unterminated meta literal')
}

function checkMetaLiteral(text) {
  const problems = []
  const outsideStrings = text.replace(/'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"/g, "''")
  if (/`/.test(outsideStrings)) problems.push('meta must not use template literals')
  if (/\.\.\./.test(outsideStrings)) problems.push('meta must not use spreads')
  if (/[A-Za-z_$][\w$]*\s*\(/.test(outsideStrings)) problems.push('meta must not call functions')
  const idents = outsideStrings.match(/(?<![\w$.])[A-Za-z_$][\w$]*(?=\s*[,}\]])/g) || []
  const values = idents.filter((id) => !['true', 'false', 'null'].includes(id))
  if (values.length) problems.push(`meta must not reference variables: ${values.join(', ')}`)
  let meta
  try {
    meta = new Function(`"use strict"; return (${text})`)()
  } catch (err) {
    problems.push(`meta is not a literal: ${err.message}`)
    return { meta: null, problems }
  }
  if (!meta || typeof meta.name !== 'string' || !meta.name) problems.push('meta.name is required')
  if (typeof meta?.description !== 'string' || !meta.description) problems.push('meta.description is required')
  if (!Array.isArray(meta?.phases) || !meta.phases.every((p) => p && typeof p.title === 'string')) {
    problems.push('meta.phases must list {title} entries')
  }
  return { meta, problems }
}

function typeOk(type, value) {
  switch (type) {
    case 'integer':
      return Number.isInteger(value)
    case 'number':
      return typeof value === 'number' && Number.isFinite(value)
    case 'string':
      return typeof value === 'string'
    case 'boolean':
      return typeof value === 'boolean'
    case 'null':
      return value === null
    case 'array':
      return Array.isArray(value)
    case 'object':
      return value !== null && typeof value === 'object' && !Array.isArray(value)
    default:
      return true
  }
}

// Small JSON-schema check (type, enum, required, properties, additionalProperties, items, anyOf, min/max).
function validate(schema, value, where = '$') {
  if (!schema || typeof schema !== 'object') return []
  if (Array.isArray(schema.anyOf)) {
    return schema.anyOf.some((s) => validate(s, value, where).length === 0) ? [] : [`${where}: matches no anyOf branch`]
  }
  const errors = []
  if (schema.type !== undefined) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type]
    if (!types.some((t) => typeOk(t, value))) return [`${where}: expected ${types.join('|')}`]
  }
  if (Array.isArray(schema.enum) && !schema.enum.includes(value)) errors.push(`${where}: not one of ${schema.enum}`)
  if (typeof value === 'number') {
    if (schema.minimum !== undefined && value < schema.minimum) errors.push(`${where}: below minimum`)
    if (schema.maximum !== undefined && value > schema.maximum) errors.push(`${where}: above maximum`)
  }
  if (typeOk('object', value)) {
    for (const key of schema.required || []) if (!(key in value)) errors.push(`${where}.${key}: required`)
    for (const [key, v] of Object.entries(value)) {
      if (schema.properties && key in schema.properties) errors.push(...validate(schema.properties[key], v, `${where}.${key}`))
      else if (schema.additionalProperties === false) errors.push(`${where}.${key}: not allowed`)
      else if (typeof schema.additionalProperties === 'object') {
        errors.push(...validate(schema.additionalProperties, v, `${where}.${key}`))
      }
    }
  }
  if (Array.isArray(value) && schema.items) value.forEach((v, i) => errors.push(...validate(schema.items, v, `${where}[${i}]`)))
  return errors
}

function subsetMismatch(expected, actual, where = '$') {
  if (expected === null || typeof expected !== 'object') {
    return JSON.stringify(expected) === JSON.stringify(actual) ? [] : [`${where}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`]
  }
  if (Array.isArray(expected)) {
    return JSON.stringify(expected) === JSON.stringify(actual) ? [] : [`${where}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`]
  }
  if (!actual || typeof actual !== 'object') return [`${where}: expected an object, got ${JSON.stringify(actual)}`]
  return Object.entries(expected).flatMap(([k, v]) => subsetMismatch(v, actual[k], `${where}.${k}`))
}

function sandboxDate() {
  const RealDate = Date
  function FakeDate(...a) {
    if (!new.target) throw new Error('Date() is not available in workflows')
    if (a.length === 0) throw new Error('new Date() without arguments is not available in workflows')
    return new RealDate(...a)
  }
  FakeDate.now = () => {
    throw new Error('Date.now() is not available in workflows')
  }
  FakeDate.UTC = RealDate.UTC
  FakeDate.parse = RealDate.parse
  return FakeDate
}

function sandboxMath() {
  const m = Object.create(Math)
  m.random = () => {
    throw new Error('Math.random() is not available in workflows')
  }
  return m
}

async function runCase(body, meta, testCase) {
  const calls = { agents: [], phases: [], logs: [], pipelines: [] }
  const responses = testCase.responses || {}
  const agent = async (prompt, opts = {}) => {
    if (typeof prompt !== 'string' || !prompt) throw new Error('agent() needs a prompt string')
    const label = opts.label || `agent-${calls.agents.length + 1}`
    calls.agents.push({ label, prompt, opts })
    if (!(label in responses)) throw new Error(`no canned response for agent '${label}'`)
    const response = responses[label]
    if (response === null) return null
    if (!opts.schema) throw new Error(`agent '${label}' has no schema`)
    const problems = validate(opts.schema, response)
    if (problems.length) throw new Error(`canned response for '${label}' does not match its schema: ${problems.join('; ')}`)
    return JSON.parse(JSON.stringify(response))
  }
  const pipeline = async (items, ...stages) => {
    if (!Array.isArray(items)) throw new Error('pipeline() needs an array')
    calls.pipelines.push(items.length)
    const out = []
    for (let i = 0; i < items.length; i++) {
      let value = items[i]
      try {
        for (const stage of stages) value = await stage(value, items[i], i)
      } catch (err) {
        value = null
      }
      out.push(value)
    }
    return out
  }
  const parallel = async (thunks) => {
    const out = []
    for (const thunk of thunks) {
      try {
        out.push(await thunk())
      } catch (err) {
        out.push(null)
      }
    }
    return out
  }
  const phase = (title) => calls.phases.push(title)
  const log = (message) => calls.logs.push(String(message))
  const budget = { total: null, spent: () => 0, remaining: () => Infinity }
  const workflow = () => {
    throw new Error('nested workflow() is not allowed')
  }
  const fn = new AsyncFunction('agent', 'pipeline', 'parallel', 'phase', 'log', 'args', 'budget', 'workflow', 'Date', 'Math', body)
  let result
  let thrown = null
  try {
    result = await fn(agent, pipeline, parallel, phase, log, testCase.args, budget, workflow, sandboxDate(), sandboxMath())
  } catch (err) {
    thrown = err
  }
  return { calls, result, thrown }
}

function checkExpectations(meta, testCase, run) {
  const e = testCase.expect || {}
  const failures = []
  const { calls, result, thrown } = run
  if (e.throws) {
    if (!thrown) failures.push(`expected an error containing '${e.throws}'`)
    else if (!String(thrown.message).includes(e.throws)) failures.push(`error '${thrown.message}' lacks '${e.throws}'`)
  } else if (thrown) {
    failures.push(`workflow threw: ${thrown.message}`)
  }
  const titles = new Set((meta.phases || []).map((p) => p.title))
  for (const title of calls.phases) if (!titles.has(title)) failures.push(`phase('${title}') is not declared in meta.phases`)
  for (const a of calls.agents) {
    if (a.opts.phase && !titles.has(a.opts.phase)) failures.push(`agent '${a.label}' uses undeclared phase '${a.opts.phase}'`)
    if (a.opts.isolation) failures.push(`agent '${a.label}' uses isolation (forbidden)`)
  }
  const order = calls.phases.filter((p, i) => i === 0 || calls.phases[i - 1] !== p)
  if (e.phases && JSON.stringify(order) !== JSON.stringify(e.phases)) {
    failures.push(`phase order ${JSON.stringify(order)} != ${JSON.stringify(e.phases)}`)
  }
  const labels = calls.agents.map((a) => a.label)
  if (e.agent_labels && JSON.stringify(labels) !== JSON.stringify(e.agent_labels)) {
    failures.push(`agent labels ${JSON.stringify(labels)} != ${JSON.stringify(e.agent_labels)}`)
  }
  if (e.batch_agents !== undefined) {
    const n = labels.filter((l) => l.startsWith('batch ')).length
    if (n !== e.batch_agents) failures.push(`batch agents ${n} != ${e.batch_agents}`)
    const unique = new Set(labels.filter((l) => l.startsWith('batch '))).size
    if (unique !== n) failures.push('more than one agent for the same batch')
  }
  const byLabel = (label) => calls.agents.filter((a) => a.label === label)
  for (const [label, needles] of Object.entries(e.prompt_contains || {})) {
    const agents = byLabel(label)
    if (!agents.length) failures.push(`no agent '${label}' was called`)
    for (const needle of needles) {
      if (!agents.some((a) => a.prompt.includes(needle))) failures.push(`prompt of '${label}' lacks ${JSON.stringify(needle)}`)
    }
  }
  for (const [label, needles] of Object.entries(e.prompt_excludes || {})) {
    for (const needle of needles) {
      if (byLabel(label).some((a) => a.prompt.includes(needle))) failures.push(`prompt of '${label}' contains ${JSON.stringify(needle)}`)
    }
  }
  for (const [label, expected] of Object.entries(e.opts || {})) {
    const agents = byLabel(label)
    if (!agents.length) failures.push(`no agent '${label}' was called`)
    for (const a of agents) failures.push(...subsetMismatch(expected, a.opts, `opts[${label}]`))
  }
  for (const needle of e.logs_contain || []) {
    if (!calls.logs.some((l) => l.includes(needle))) failures.push(`no log line contains ${JSON.stringify(needle)}`)
  }
  for (const needle of e.logs_exclude || []) {
    if (calls.logs.some((l) => l.includes(needle))) failures.push(`a log line contains ${JSON.stringify(needle)}`)
  }
  if (e.result) failures.push(...subsetMismatch(e.result, result, 'result'))
  return { failures, labels, phases: order, logs: calls.logs }
}

async function checkWorkflow(file) {
  const report = { workflow: file, problems: [], cases: [] }
  const source = fs.readFileSync(file, 'utf8')
  if (source.includes('\r')) report.problems.push('use LF line endings')
  let metaInfo
  try {
    const { text, end } = extractMeta(source)
    metaInfo = { ...checkMetaLiteral(text), end }
  } catch (err) {
    report.problems.push(err.message)
    return report
  }
  report.problems.push(...metaInfo.problems)
  const body = source.replace(/^export const meta\s*=/, 'const meta =')
  for (const [pattern, message] of FORBIDDEN) if (pattern.test(source.slice(metaInfo.end))) report.problems.push(message)
  try {
    new AsyncFunction('agent', 'pipeline', 'parallel', 'phase', 'log', 'args', 'budget', 'workflow', 'Date', 'Math', body)
  } catch (err) {
    report.problems.push(`does not compile as a workflow body: ${err.message}`)
    return report
  }
  if (!metaInfo.meta) return report
  const name = path.basename(file).replace(/\.js$/, '')
  const scenarioFile = path.join(SCENARIOS, `${name}.scenario.json`)
  if (!fs.existsSync(scenarioFile)) {
    report.problems.push(`missing scenario file tests/workflows/${name}.scenario.json`)
    return report
  }
  const scenario = JSON.parse(fs.readFileSync(scenarioFile, 'utf8'))
  if (!Array.isArray(scenario.cases) || !scenario.cases.length) report.problems.push('scenario has no cases')
  for (const testCase of scenario.cases || []) {
    const run = await runCase(body, metaInfo.meta, testCase)
    const outcome = checkExpectations(metaInfo.meta, testCase, run)
    report.cases.push({ name: testCase.name, ok: outcome.failures.length === 0, ...outcome })
  }
  return report
}

async function main(argv) {
  const files = argv.filter((a) => !a.startsWith('--'))
  if (!argv.includes('--check') || !files.length) {
    console.error('usage: node tests/workflow_harness.mjs --check <workflow.js> [...]')
    return 2
  }
  const reports = []
  for (const file of files) reports.push(await checkWorkflow(file))
  const ok = reports.every((r) => r.problems.length === 0 && r.cases.every((c) => c.ok))
  const summary = reports.map((r) => ({
    workflow: r.workflow,
    problems: r.problems,
    cases: r.cases.map((c) => ({ name: c.name, ok: c.ok, failures: c.failures })),
  }))
  console.log(JSON.stringify({ ok, workflows: summary }, null, 2))
  return ok ? 0 : 1
}

process.exitCode = await main(process.argv.slice(2))
