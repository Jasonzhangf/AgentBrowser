/**
 * Evidence-only contract for the M1 network matrix.
 *
 * This module never travels on a Relay or Browser wire.  It validates
 * evidence emitted by the owning transport/client adapters and projects it
 * into the matrix.  A Relay data-plane byte forwarding result therefore
 * cannot become a displayed-frame or Browser-operation result here.
 */

import {
  existsSync,
  lstatSync,
  readdirSync,
  readFileSync,
} from 'node:fs';
import {basename, extname, relative, resolve} from 'node:path';

export const NETWORK_EVIDENCE_SCHEMA = 'agentbrowser.network-path.evidence.v1';

export const CANONICAL_PATHS = Object.freeze([
  Object.freeze({path_id: 'local-direct', network_path: 'local', transports: Object.freeze(['IPC', 'WSS'])}),
  Object.freeze({path_id: 'udp-webrtc', network_path: Object.freeze(['lan', 'public']), transports: Object.freeze(['WebRTC'])}),
  Object.freeze({path_id: 'tailscale-direct', network_path: 'tailscale', transports: Object.freeze(['WebRTC', 'WSS'])}),
  Object.freeze({path_id: 'relay', network_path: 'relay', transports: Object.freeze(['WSS'])}),
]);

const RESULTS = new Set(['PASS', 'UNPROVEN', 'FAIL']);
const SECTION_STATUS = new Set(['proved', 'unknown', 'failed']);
const SUCCESSFUL_OUTCOMES = new Set(['applied', 'succeeded']);
const RECEIPT_OUTCOMES = new Set(['applied', 'succeeded', 'failed', 'rejected', 'unknown']);
const IDENTITY_FIELDS = ['account_id', 'host_id', 'device_id'];

const TOP_LEVEL_KEYS = new Set([
  'schema', 'path_id', 'network_path', 'transport', 'session_id', 'generation',
  'run_id', 'identity', 'peer_identity', 'underlay', 'signaling', 'data_plane',
  'media', 'operation_receipts', 'operation_evidence', 'transition', 'failure',
  'result', 'source', 'cross_account', 'foreign_account',
]);
const IDENTITY_KEYS = new Set(['account_id', 'host_id', 'device_id']);
const UNDERLAY_KEYS = new Set(['status', 'kind', 'reason', 'evidence', 'details']);
const SECTION_KEYS = new Set(['status', 'reason', 'evidence', 'decoded_frames', 'displayed', 'native_surface', 'transport_acknowledgements', 'account_id', 'host_id', 'device_id', 'identity']);
const OPERATION_EVIDENCE_KEYS = new Set(['status', 'reason', 'evidence']);
const TRANSITION_KEYS = new Set([
  'previous_path', 'switched', 'operation_replayed', 'stale_generation_drops',
  'stale_generation_updates', 'stale_generation_applied', 'stale_update_applied',
  'stale_generation_event', 'generation_events', 'previous_generation',
  'current_generation', 'stale_generation', 'account_id', 'host_id', 'device_id',
  'identity',
]);
const RECEIPT_KEYS = new Set([
  'operation_id', 'operation', 'generation', 'session_id', 'outcome', 'evidence',
  'account_id', 'host_id', 'device_id', 'identity',
]);
const SOURCE_KEYS = new Set(['root', 'file', 'files', 'run_id', 'producer']);

export class NetworkEvidenceError extends Error {
  constructor(code, message, path = undefined) {
    super(path ? `${code} at ${path}: ${message}` : `${code}: ${message}`);
    this.name = 'NetworkEvidenceError';
    this.code = code;
    this.path = path;
  }
}

function fail(code, message, path) {
  throw new NetworkEvidenceError(code, message, path);
}

function object(value, name) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('EVIDENCE_OBJECT_INVALID', `${name} must be an object`, name);
  return value;
}

function string(value, name, max = 512) {
  if (typeof value !== 'string' || value.length === 0 || value.length > max) {
    fail('EVIDENCE_FIELD_INVALID', `${name} must be a non-empty string of at most ${max} characters`, name);
  }
  return value;
}

function integer(value, name) {
  if (!Number.isSafeInteger(value) || value < 0) fail('EVIDENCE_INTEGER_INVALID', `${name} must be a non-negative safe integer`, name);
  return value;
}

function boolean(value, name) {
  if (typeof value !== 'boolean') fail('EVIDENCE_BOOLEAN_INVALID', `${name} must be boolean`, name);
  return value;
}

function array(value, name) {
  if (!Array.isArray(value)) fail('EVIDENCE_ARRAY_INVALID', `${name} must be an array`, name);
  return value;
}

function exactKeys(value, allowed, name) {
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) fail('EVIDENCE_UNKNOWN_FIELD', `unsupported field ${key}`, `${name}.${key}`);
  }
}

function evidenceFiles(value, name, required = false) {
  if (value === undefined) {
    if (required) fail('EVIDENCE_REFERENCE_MISSING', `${name} must identify source evidence`, name);
    return [];
  }
  const files = array(value, name);
  if (required && files.length === 0) fail('EVIDENCE_REFERENCE_MISSING', `${name} must contain at least one source evidence path`, name);
  return files.map((item, index) => string(item, `${name}[${index}]`, 1024));
}

function validateIdentity(value, name) {
  if (value === undefined) return {};
  const data = object(value, name);
  exactKeys(data, IDENTITY_KEYS, name);
  const result = {};
  for (const field of IDENTITY_FIELDS) {
    if (data[field] !== undefined) result[field] = string(data[field], `${name}.${field}`, 256);
  }
  if (Object.keys(result).length === 0) fail('IDENTITY_INVALID', `${name} must contain an identity field`, name);
  return result;
}

function mergeIdentity(base, extra, name) {
  const merged = {...base};
  for (const field of IDENTITY_FIELDS) {
    if (extra[field] === undefined) continue;
    if (merged[field] !== undefined && merged[field] !== extra[field]) {
      fail('IDENTITY_MISMATCH', `${field} does not match the path identity`, `${name}.${field}`);
    }
    merged[field] = extra[field];
  }
  return merged;
}

function validateIdentityFields(value, name, base) {
  if (value === undefined) return base;
  const data = object(value, name);
  const direct = {};
  for (const field of IDENTITY_FIELDS) {
    if (data[field] !== undefined) direct[field] = string(data[field], `${name}.${field}`, 256);
  }
  return mergeIdentity(base, direct, name);
}

function validateUnderlay(value, pathId) {
  if (pathId !== 'tailscale-direct' && value === undefined) return undefined;
  if (value === undefined) fail('UNDERLAY_STATUS_MISSING', 'Tailscale evidence must preserve known or unknown underlay status', 'underlay');
  const data = object(value, 'underlay');
  exactKeys(data, UNDERLAY_KEYS, 'underlay');
  if (!SECTION_STATUS.has(data.status)) fail('UNDERLAY_STATUS_INVALID', 'underlay.status must be proved, unknown, or failed', 'underlay.status');
  if (data.status === 'unknown' || data.status === 'failed') string(data.reason, 'underlay.reason', 2048);
  if (data.status === 'proved') {
    evidenceFiles(data.evidence, 'underlay.evidence', true);
    if (data.kind !== undefined) string(data.kind, 'underlay.kind', 128);
  }
  if (data.details !== undefined) object(data.details, 'underlay.details');
  return {...data, evidence: evidenceFiles(data.evidence, 'underlay.evidence')};
}

function validateSection(value, name, {media = false} = {}) {
  const data = object(value, name);
  exactKeys(data, SECTION_KEYS, name);
  if (!SECTION_STATUS.has(data.status)) fail('EVIDENCE_STATUS_INVALID', `${name}.status must be proved, unknown, or failed`, `${name}.status`);
  const evidence = evidenceFiles(data.evidence, `${name}.evidence`, data.status === 'proved');
  if (data.status === 'unknown' || data.status === 'failed') string(data.reason, `${name}.reason`, 2048);
  if (!media) return {...data, evidence};

  const decodedFrames = data.decoded_frames === undefined ? 0 : integer(data.decoded_frames, `${name}.decoded_frames`);
  const displayed = data.displayed === undefined ? false : boolean(data.displayed, `${name}.displayed`);
  if (data.native_surface !== undefined) boolean(data.native_surface, `${name}.native_surface`);
  const acknowledgements = data.transport_acknowledgements === undefined
    ? 0
    : integer(data.transport_acknowledgements, `${name}.transport_acknowledgements`);
  if (data.status === 'proved' && (decodedFrames < 1 || displayed !== true || evidence.length === 0)) {
    fail('MEDIA_PROOF_INVALID', 'proved media requires a positive decoded frame count, displayed=true, and source evidence', name);
  }
  if (data.status !== 'proved' && displayed === true && decodedFrames < 1) {
    fail('MEDIA_PROOF_INVALID', 'displayed media must have a positive decoded frame count', name);
  }
  return {
    ...data,
    evidence,
    decoded_frames: decodedFrames,
    displayed,
    transport_acknowledgements: acknowledgements,
  };
}

function validateOperationEvidence(value) {
  if (value === undefined) return {status: 'unknown', reason: 'No operation receipt was emitted', evidence: []};
  const data = object(value, 'operation_evidence');
  exactKeys(data, OPERATION_EVIDENCE_KEYS, 'operation_evidence');
  if (!SECTION_STATUS.has(data.status)) fail('EVIDENCE_STATUS_INVALID', 'operation_evidence.status is invalid', 'operation_evidence.status');
  const evidence = evidenceFiles(data.evidence, 'operation_evidence.evidence', data.status === 'proved');
  if (data.status === 'unknown' || data.status === 'failed') string(data.reason, 'operation_evidence.reason', 2048);
  return {...data, evidence};
}

function validateReceipt(value, index, sessionId, generation, identity) {
  const name = `operation_receipts[${index}]`;
  const data = object(value, name);
  exactKeys(data, RECEIPT_KEYS, name);
  const operationId = string(data.operation_id, `${name}.operation_id`, 256);
  const operation = string(data.operation, `${name}.operation`, 128);
  const receiptGeneration = integer(data.generation, `${name}.generation`);
  if (generation === undefined || receiptGeneration !== generation) {
    fail('GENERATION_MISMATCH', 'operation receipt generation must equal the current path generation', `${name}.generation`);
  }
  const receiptSession = string(data.session_id, `${name}.session_id`, 256);
  if (receiptSession !== sessionId) fail('SESSION_MISMATCH', 'operation receipt session differs from path session', `${name}.session_id`);
  const outcome = string(data.outcome, `${name}.outcome`, 64);
  if (!RECEIPT_OUTCOMES.has(outcome)) fail('RECEIPT_OUTCOME_INVALID', `unsupported operation outcome ${outcome}`, `${name}.outcome`);
  const receiptIdentity = validateIdentity(data.identity, `${name}.identity`);
  const withDirectIdentity = validateIdentityFields(data, name, receiptIdentity);
  const mergedIdentity = mergeIdentity(identity, withDirectIdentity, name);
  return {
    ...data,
    operation_id: operationId,
    operation,
    generation: receiptGeneration,
    session_id: receiptSession,
    outcome,
    evidence: evidenceFiles(data.evidence, `${name}.evidence`, outcome === 'applied' || outcome === 'succeeded'),
    identity: Object.keys(withDirectIdentity).length > 0 ? withDirectIdentity : undefined,
    _identity: mergedIdentity,
  };
}

function validateTransition(value, generation, identity) {
  if (value === undefined) return {identity};
  const data = object(value, 'transition');
  exactKeys(data, TRANSITION_KEYS, 'transition');
  if (data.previous_path !== undefined) string(data.previous_path, 'transition.previous_path', 128);
  if (data.switched !== undefined) boolean(data.switched, 'transition.switched');
  if (data.operation_replayed !== undefined && boolean(data.operation_replayed, 'transition.operation_replayed')) {
    fail('OPERATION_REPLAYED', 'a path transition cannot claim that an operation was replayed', 'transition.operation_replayed');
  }
  if (data.stale_generation_drops !== undefined) integer(data.stale_generation_drops, 'transition.stale_generation_drops');
  if (data.stale_generation_updates !== undefined && integer(data.stale_generation_updates, 'transition.stale_generation_updates') > 0) {
    fail('STALE_GENERATION_UPDATE', 'stale generation updates must be rejected, never applied', 'transition.stale_generation_updates');
  }
  for (const field of ['stale_generation_applied', 'stale_update_applied']) {
    if (data[field] !== undefined && boolean(data[field], `transition.${field}`)) {
      fail('STALE_GENERATION_UPDATE', 'stale generation updates must be rejected, never applied', `transition.${field}`);
    }
  }
  if (data.stale_generation_event !== undefined) {
    const event = object(data.stale_generation_event, 'transition.stale_generation_event');
    exactKeys(event, new Set(['generation', 'applied', 'stale', 'reason']), 'transition.stale_generation_event');
    integer(event.generation, 'transition.stale_generation_event.generation');
    if (event.applied !== undefined) boolean(event.applied, 'transition.stale_generation_event.applied');
    if (event.stale !== undefined) boolean(event.stale, 'transition.stale_generation_event.stale');
    if (event.stale === true && event.applied === true) fail('STALE_GENERATION_UPDATE', 'stale event was marked applied', 'transition.stale_generation_event');
    if (event.reason !== undefined) string(event.reason, 'transition.stale_generation_event.reason', 2048);
  }
  if (data.generation_events !== undefined) {
    const events = array(data.generation_events, 'transition.generation_events');
    for (const [index, eventValue] of events.entries()) {
      const name = `transition.generation_events[${index}]`;
      const event = object(eventValue, name);
      exactKeys(event, new Set(['generation', 'applied', 'stale', 'event', 'reason']), name);
      integer(event.generation, `${name}.generation`);
      if (event.applied !== undefined) boolean(event.applied, `${name}.applied`);
      if (event.stale !== undefined) boolean(event.stale, `${name}.stale`);
      if (event.event !== undefined) string(event.event, `${name}.event`, 128);
      if (event.reason !== undefined) string(event.reason, `${name}.reason`, 2048);
      if ((event.stale === true || event.event === 'stale_update') && event.applied === true) {
        fail('STALE_GENERATION_UPDATE', 'stale event was marked applied', name);
      }
    }
  }
  for (const field of ['previous_generation', 'current_generation', 'stale_generation']) {
    if (data[field] !== undefined) integer(data[field], `transition.${field}`);
  }
  if (data.current_generation !== undefined && generation !== undefined && data.current_generation !== generation) {
    fail('GENERATION_MISMATCH', 'transition current_generation differs from path generation', 'transition.current_generation');
  }
  if (data.previous_generation !== undefined && data.current_generation !== undefined && data.current_generation <= data.previous_generation && data.switched === true) {
    fail('GENERATION_ORDER_INVALID', 'a switched path must advance generation', 'transition.current_generation');
  }
  if (data.stale_generation !== undefined && data.current_generation !== undefined && data.stale_generation >= data.current_generation) {
    fail('STALE_GENERATION_ORDER_INVALID', 'stale generation must be older than current generation', 'transition.stale_generation');
  }
  const transitionIdentity = validateIdentity(data.identity, 'transition.identity');
  const withDirectIdentity = validateIdentityFields(data, 'transition', transitionIdentity);
  return {...data, identity: withDirectIdentity, _identity: mergeIdentity(identity, withDirectIdentity, 'transition')};
}

function validateSource(value) {
  if (value === undefined) return undefined;
  const data = object(value, 'source');
  exactKeys(data, SOURCE_KEYS, 'source');
  if (data.root === undefined && data.file === undefined && data.files === undefined) {
    fail('SOURCE_REFERENCE_MISSING', 'source must identify at least one evidence file', 'source');
  }
  for (const field of ['root', 'file', 'run_id', 'producer']) {
    if (data[field] !== undefined) string(data[field], `source.${field}`, 2048);
  }
  if (data.files !== undefined) evidenceFiles(data.files, 'source.files', true);
  return data;
}

function pathSpec(pathId) {
  const spec = CANONICAL_PATHS.find(item => item.path_id === pathId);
  if (!spec) fail('PATH_ID_INVALID', `unsupported path_id ${pathId}`, 'path_id');
  return spec;
}

function validatePathTransport(pathId, networkPath, transport) {
  const spec = pathSpec(pathId);
  string(networkPath, 'network_path', 64);
  string(transport, 'transport', 64);
  const networkAllowed = Array.isArray(spec.network_path) ? spec.network_path.includes(networkPath) : spec.network_path === networkPath;
  if (!networkAllowed || !spec.transports.includes(transport)) {
    fail('PATH_TRANSPORT_MISMATCH', `${pathId} cannot be represented as ${networkPath}/${transport}`, 'network_path');
  }
  return spec;
}

function resultReasons({signaling, media, operationEvidence, receipts, transition}) {
  const reasons = [];
  if (signaling.status !== 'proved') reasons.push(`signaling ${signaling.status}: ${signaling.reason ?? 'source evidence is missing'}`);
  const mediaProved = media.status === 'proved' && media.decoded_frames > 0 && media.displayed === true;
  if (!mediaProved) {
    if (media.transport_acknowledgements > 0 && media.decoded_frames === 0) reasons.push('transport acknowledgements do not prove a decoded or displayed frame');
    else reasons.push(`media ${media.status}: ${media.reason ?? 'decoded/displayed frame evidence is missing'}`);
  }
  const operationsProved = receipts.length > 0 && receipts.every(item => SUCCESSFUL_OUTCOMES.has(item.outcome));
  if (!operationsProved) reasons.push(operationEvidence.reason ?? 'successful operation receipt evidence is missing');
  if (transition?.operation_replayed === true) reasons.push('operation replay was reported');
  return {reasons, mediaProved, operationsProved};
}

/**
 * Validate one evidence record.  Invalid structure or an over-claimed PASS
 * throws; a structurally valid but incomplete record returns UNPROVEN.
 */
export function validateNetworkPathEvidence(value) {
  const record = object(value, 'record');
  exactKeys(record, TOP_LEVEL_KEYS, 'record');
  if (record.schema !== NETWORK_EVIDENCE_SCHEMA) fail('SCHEMA_INVALID', `expected ${NETWORK_EVIDENCE_SCHEMA}`, 'schema');
  const pathId = string(record.path_id, 'path_id', 128);
  const networkPath = string(record.network_path, 'network_path', 64);
  const transport = string(record.transport, 'transport', 64);
  const spec = validatePathTransport(pathId, networkPath, transport);
  const sessionId = string(record.session_id, 'session_id', 256);
  const generation = record.generation === undefined ? undefined : integer(record.generation, 'generation');
  const identityFromObject = validateIdentity(record.identity, 'identity');
  const identity = validateIdentityFields(record, 'record', identityFromObject);
  const peerIdentity = validateIdentity(record.peer_identity, 'peer_identity');
  if (peerIdentity.account_id !== undefined && identity.account_id !== undefined && peerIdentity.account_id !== identity.account_id) {
    fail('CROSS_ACCOUNT_EVIDENCE', 'peer account differs from path account', 'peer_identity.account_id');
  }
  if (record.cross_account === true || record.foreign_account === true) {
    fail('CROSS_ACCOUNT_EVIDENCE', 'cross-account or foreign-account evidence cannot be accepted as this path', 'record');
  }
  if (record.cross_account !== undefined) boolean(record.cross_account, 'cross_account');
  if (record.foreign_account !== undefined) boolean(record.foreign_account, 'foreign_account');
  const underlay = validateUnderlay(record.underlay, pathId);
  const signaling = validateSection(record.signaling, 'signaling');
  const dataPlane = record.data_plane === undefined ? undefined : validateSection(record.data_plane, 'data_plane');
  const media = validateSection(record.media, 'media', {media: true});
  const operationEvidence = validateOperationEvidence(record.operation_evidence);
  const receiptsRaw = array(record.operation_receipts ?? [], 'operation_receipts');
  const operationIds = new Set();
  let receiptIdentity = identity;
  const receipts = receiptsRaw.map((item, index) => {
    const receipt = validateReceipt(item, index, sessionId, generation, receiptIdentity);
    if (operationIds.has(receipt.operation_id)) fail('OPERATION_ID_DUPLICATE', `duplicate operation id ${receipt.operation_id}`, `operation_receipts[${index}].operation_id`);
    operationIds.add(receipt.operation_id);
    receiptIdentity = receipt._identity;
    return receipt;
  });
  const transition = validateTransition(record.transition, generation, receiptIdentity);
  const sectionIdentity = mergeIdentity(receiptIdentity, transition._identity ?? {}, 'transition');
  for (const section of [record.signaling, record.data_plane, record.media, record.operation_evidence]) {
    if (section === undefined) continue;
    const sectionIdentityObject = validateIdentity(section.identity, 'section.identity');
    const sectionWithDirectIdentity = validateIdentityFields(section, 'section', sectionIdentityObject);
    mergeIdentity(sectionIdentity, sectionWithDirectIdentity, 'section');
  }
  const source = validateSource(record.source);
  const proof = resultReasons({signaling, media, operationEvidence, receipts, transition});
  const complete = proof.mediaProved && proof.operationsProved && signaling.status === 'proved';
  const derivedResult = complete ? 'PASS' : 'UNPROVEN';
  const claimedResult = record.result === undefined ? derivedResult : record.result;
  if (!RESULTS.has(claimedResult)) fail('RESULT_INVALID', `unsupported result ${claimedResult}`, 'result');
  if (claimedResult === 'PASS' && !complete) {
    fail('PASS_CLAIM_UNPROVEN', proof.reasons.join('; '), 'result');
  }
  if (claimedResult === 'FAIL') {
    const failure = object(record.failure, 'failure');
    exactKeys(failure, new Set(['code', 'reason', 'evidence']), 'failure');
    string(failure.code, 'failure.code', 128);
    string(failure.reason, 'failure.reason', 2048);
    evidenceFiles(failure.evidence, 'failure.evidence', true);
  }
  return {
    valid: true,
    path_id: pathId,
    network_path: networkPath,
    transport,
    result: claimedResult,
    record: {
      ...record,
      path_id: pathId,
      network_path: networkPath,
      transport,
      session_id: sessionId,
      generation,
      identity: Object.keys(sectionIdentity).length > 0 ? sectionIdentity : undefined,
      peer_identity: Object.keys(peerIdentity).length > 0 ? peerIdentity : undefined,
      underlay,
      signaling,
      data_plane: dataPlane,
      media,
      operation_evidence: operationEvidence,
      operation_receipts: receipts.map(({_identity, ...receipt}) => receipt),
      transition,
      source,
      result: claimedResult,
    },
    proof: {
      signaling: signaling.status === 'proved',
      media: proof.mediaProved,
      operations: proof.operationsProved,
      complete,
      reasons: proof.reasons,
    },
    spec,
  };
}

/** Validate a set and reject duplicate paths, operation IDs, and session accounts. */
export function validateNetworkPathEvidenceSet(values) {
  const records = array(values, 'records');
  const paths = new Set();
  const operationIds = new Set();
  const sessionAccounts = new Map();
  const validated = records.map((value, index) => {
    const item = validateNetworkPathEvidence(value);
    if (paths.has(item.path_id)) fail('PATH_DUPLICATE', `duplicate path evidence for ${item.path_id}`, `records[${index}].path_id`);
    paths.add(item.path_id);
    const session = item.record.session_id;
    const account = item.record.identity?.account_id;
    if (sessionAccounts.has(session) && account !== undefined && sessionAccounts.get(session) !== account) {
      fail('CROSS_ACCOUNT_EVIDENCE', `session ${session} is associated with multiple accounts`, `records[${index}].identity.account_id`);
    }
    if (account !== undefined) sessionAccounts.set(session, account);
    for (const receipt of item.record.operation_receipts) {
      if (operationIds.has(receipt.operation_id)) fail('OPERATION_ID_DUPLICATE', `duplicate operation id ${receipt.operation_id} across path evidence`, `records[${index}].operation_receipts`);
      operationIds.add(receipt.operation_id);
    }
    return item;
  });
  return validated;
}

function missingRow(spec) {
  return {
    path_id: spec.path_id,
    network_path: Array.isArray(spec.network_path) ? spec.network_path[0] : spec.network_path,
    transport: spec.transports.length === 1 ? spec.transports[0] : spec.transports.join('|'),
    result: 'UNPROVEN',
    evidence: [],
    reason: 'No owner-produced path evidence was supplied for this canonical path',
  };
}

/** Project validated evidence into stable rows without upgrading unknown claims. */
export function projectNetworkMatrix(values) {
  const validated = validateNetworkPathEvidenceSet(values);
  const byPath = new Map(validated.map(item => [item.path_id, item]));
  return CANONICAL_PATHS.map(spec => {
    const item = byPath.get(spec.path_id);
    if (!item) return missingRow(spec);
    const record = item.record;
    return {
      path_id: item.path_id,
      network_path: item.network_path,
      transport: item.transport,
      session_id: record.session_id,
      generation: record.generation,
      underlay: record.underlay,
      result: item.result,
      evidence: record.source?.files ?? (record.source?.file ? [record.source.file] : []),
      proof: item.proof,
      reason: item.result === 'UNPROVEN' ? item.proof.reasons.join('; ') : undefined,
      failure: record.failure,
    };
  });
}

function jsonFiles(root) {
  if (!existsSync(root)) fail('EVIDENCE_ROOT_UNAVAILABLE', `evidence root is unavailable: ${root}`, 'evidence_root');
  if (lstatSync(root).isFile()) return extname(root) === '.json' ? [root] : [];
  const result = [];
  for (const entry of readdirSync(root, {withFileTypes: true})) {
    const path = resolve(root, entry.name);
    if (entry.isDirectory()) result.push(...jsonFiles(path));
    else if (entry.isFile() && extname(entry.name) === '.json') result.push(path);
  }
  return result;
}

function evidenceReferences(record) {
  const references = new Set(record.source?.files ?? []);
  if (record.source?.file !== undefined) references.add(record.source.file);
  for (const section of [record.signaling, record.data_plane, record.media, record.operation_evidence]) {
    for (const reference of section?.evidence ?? []) references.add(reference);
  }
  for (const reference of record.failure?.evidence ?? []) references.add(reference);
  for (const receipt of record.operation_receipts ?? []) {
    for (const reference of receipt.evidence ?? []) references.add(reference);
  }
  return [...references];
}

function verifyEvidenceReferences(record, loaderRoot, file) {
  if (record.source === undefined) return;
  const sourceRoot = resolve(record.source.root ?? loaderRoot);
  if (!existsSync(sourceRoot)) fail('EVIDENCE_ROOT_UNAVAILABLE', `source root is unavailable: ${sourceRoot}`, file);
  for (const reference of evidenceReferences(record)) {
    if (!existsSync(resolve(sourceRoot, reference))) {
      fail('EVIDENCE_REFERENCE_UNAVAILABLE', `source evidence is unavailable: ${reference}`, file);
    }
  }
}

/** Read only files carrying this evidence schema; unrelated replay JSON is ignored. */
export function loadNetworkPathEvidence(root) {
  const evidenceRoot = resolve(root);
  const files = jsonFiles(evidenceRoot);
  const records = [];
  const sources = [];
  for (const file of files) {
    let value;
    try {
      value = JSON.parse(readFileSync(file, 'utf8'));
    } catch (error) {
      if (basename(file) === 'network-path-evidence.json' || files.length === 1) {
        fail('EVIDENCE_JSON_INVALID', error instanceof Error ? error.message : String(error), file);
      }
      continue;
    }
    if (value?.schema !== NETWORK_EVIDENCE_SCHEMA) continue;
    const checked = validateNetworkPathEvidence(value);
    verifyEvidenceReferences(checked.record, evidenceRoot, file);
    records.push(checked.record);
    sources.push({file: relative(evidenceRoot, file), path_id: checked.path_id, result: checked.result});
  }
  return {root: evidenceRoot, records, sources};
}
