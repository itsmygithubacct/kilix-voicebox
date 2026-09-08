#!/usr/bin/env python3
"""A live, single-use packaging capability for one sidecar invocation.

The packaging hook used to accept an inherited ``VOICEBOX_SIDECAR_GATE_PASSED``
string compared against a ``build_id`` published inside the very tree it was
authorizing. That proves nothing about a live build and nothing about the
artifacts: a stale inherited environment -- a nested make, a wrapper script, a
re-entered shell, a CI step -- reached packaging and reported success without
building, and it still did so after the installed binary was replaced with
different bytes.

This module replaces that with a small capability that is hard to hold by
accident:

* it lives in a private ``0700`` directory **outside the checkout**, so it is
  not something the tree publishes about itself;
* it is issued **only after** the consumer has verified this invocation, and is
  removed when packaging finishes, fails or is cancelled;
* it names an **independent unpredictable token**, and the issuing process's pid
  **and start time**, so it cannot outlive the invocation that minted it or be
  revived under a recycled pid;
* it is **consumed exactly once**, by an atomic rename that only one reader can
  win;
* it carries the identities the hook must re-check against the artifacts on
  disk, so an unchanged capability cannot authorize changed bytes.

What this deliberately does **not** claim: it is not protection against an
arbitrary hostile process running as the same user, which can read the file. The
threat model is the declared one -- a private serialized builder plus accidental
inherited state -- and against that the requirement is a fresh live invocation
bound to the actual artifacts. ``RESOURCES.json`` remains an artifact
cross-check, never the authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import stat as stat_module
import time

SCHEMA = 'kilix.voicebox.sidecar-authority/candidate-v1'
VARIABLE = 'VOICEBOX_SIDECAR_AUTHORITY'
MAX_CAPABILITY_BYTES = 256 * 1024
TOKEN_HEX = 64
DEFAULT_LIFETIME_SECONDS = 4 * 60 * 60


class AuthorityError(Exception):
    """A capability was absent, malformed, stale, foreign or already used."""


def _sha256_file(path, limit):
    digest = hashlib.sha256()
    total = 0
    with open(path, 'rb') as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise AuthorityError('a bound artifact exceeds its byte limit: ' + str(path))
            digest.update(chunk)
    return digest.hexdigest(), total


def process_start_ticks(pid):
    """Field 22 of /proc/<pid>/stat: distinguishes a recycled pid."""
    try:
        with open('/proc/%d/stat' % pid, 'rb') as handle:
            data = handle.read()
    except OSError:
        return None
    close = data.rfind(b')')
    if close < 0:
        return None
    fields = data[close + 2:].split()
    if len(fields) < 20 or fields[0] in (b'Z', b'X', b'x'):
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


def _require(condition, message):
    if not condition:
        raise AuthorityError(message)


def _finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _private_regular_file(path):
    """Absolute, non-symlink, regular, ours, and not group/world accessible."""
    _require(path.is_absolute(), 'the capability path is not absolute: ' + str(path))
    try:
        info = os.lstat(path)
    except OSError as error:
        raise AuthorityError('the capability is missing: %s (%s)' % (path, error))
    _require(not stat_module.S_ISLNK(info.st_mode),
             'the capability path is a symbolic link: ' + str(path))
    _require(stat_module.S_ISREG(info.st_mode),
             'the capability path is not a regular file: ' + str(path))
    _require(info.st_uid == os.getuid(),
             'the capability is not owned by this user: ' + str(path))
    _require(not info.st_mode & 0o077,
             'the capability is group- or world-accessible: ' + str(path))
    _require(info.st_nlink == 1,
             'the capability has more than one link: ' + str(path))
    _require(info.st_size <= MAX_CAPABILITY_BYTES,
             'the capability exceeds its byte bound: ' + str(path))
    return info


def _private_directory(path):
    try:
        info = os.lstat(path)
    except OSError as error:
        raise AuthorityError('the capability directory is missing: %s (%s)' % (path, error))
    _require(not stat_module.S_ISLNK(info.st_mode),
             'the capability directory is a symbolic link: ' + str(path))
    _require(stat_module.S_ISDIR(info.st_mode),
             'the capability directory is not a directory: ' + str(path))
    _require(info.st_uid == os.getuid(),
             'the capability directory is not owned by this user: ' + str(path))
    _require(not info.st_mode & 0o077,
             'the capability directory is group- or world-accessible: ' + str(path))


def issue(directory, *, build_id, source_commit, source_tree, platform,
          binary, provenance, receipt, resources, root,
          lifetime_seconds=DEFAULT_LIFETIME_SECONDS, max_artifact_bytes=1 << 30):
    """Mint one capability after a verified build. Returns its path.

    ``resources`` is the receipt's own resource list; each entry is re-hashed
    from the published file here, so the capability records what this invocation
    actually verified rather than what the receipt asserts about itself.
    """
    issued_at = time.time()
    _require(_finite_number(issued_at) and _finite_number(lifetime_seconds),
             'the capability issue time and lifetime must be finite numbers')
    expires_at = issued_at + lifetime_seconds
    _require(_finite_number(expires_at), 'the capability expiry must be finite')
    directory = Path(directory).resolve()
    root = Path(root).resolve()
    _require(not directory.is_relative_to(root),
             'the capability directory must be outside the checkout: ' + str(directory))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    _private_directory(directory)

    binary_sha, binary_bytes = _sha256_file(binary, max_artifact_bytes)
    provenance_sha, provenance_bytes = _sha256_file(provenance, max_artifact_bytes)
    receipt_sha, receipt_bytes = _sha256_file(receipt, MAX_CAPABILITY_BYTES)

    bound_resources = []
    receipt_dir = Path(receipt).resolve().parent
    for entry in resources:
        name = entry['path']
        _require(isinstance(name, str) and name and '..' not in Path(name).parts
                 and not Path(name).is_absolute(),
                 'a bound resource name is not a safe relative path: %r' % (name,))
        published = receipt_dir / name
        _require(published.resolve().is_relative_to(receipt_dir),
                 'a bound resource escapes the resource directory: ' + name)
        digest, size = _sha256_file(published, MAX_CAPABILITY_BYTES)
        bound_resources.append({'path': name, 'sha256': digest, 'bytes': size})

    record = {
        'schema': SCHEMA,
        'token': secrets.token_hex(TOKEN_HEX // 2),
        'build_id': build_id,
        'issuer_pid': os.getpid(),
        'issuer_start_ticks': process_start_ticks(os.getpid()),
        'issued_at': issued_at,
        'expires_at': expires_at,
        'source_commit': source_commit,
        'source_tree': source_tree,
        'platform': platform,
        'root': str(root),
        'binary': {'path': str(Path(binary).resolve()),
                   'sha256': binary_sha, 'bytes': binary_bytes},
        'provenance': {'path': str(Path(provenance).resolve()),
                       'sha256': provenance_sha, 'bytes': provenance_bytes},
        'receipt': {'path': str(Path(receipt).resolve()),
                    'sha256': receipt_sha, 'bytes': receipt_bytes},
        'resources': sorted(bound_resources, key=lambda row: row['path']),
    }
    _require(record['issuer_start_ticks'] is not None,
             'the issuing process start time could not be read')

    path = directory / ('authority-' + build_id + '.json')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write((json.dumps(record, sort_keys=True) + '\n').encode('utf-8'))
        handle.flush()
        os.fsync(handle.fileno())
    return path


def revoke(path):
    """Invalidate on completion, failure or cancellation. Never raises."""
    if not path:
        return False
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def _typed(record, key, kinds, message):
    value = record.get(key)
    _require(isinstance(value, kinds) and not isinstance(value, bool), message)
    return value


def _artifact_block(record, key):
    block = record.get(key)
    _require(isinstance(block, dict), 'the capability %s block is malformed' % key)
    path = block.get('path')
    digest = block.get('sha256')
    size = block.get('bytes')
    _require(isinstance(path, str) and path and Path(path).is_absolute(),
             'the capability %s path is not an absolute string' % key)
    _require(isinstance(digest, str) and len(digest) == 64
             and all(c in '0123456789abcdef' for c in digest),
             'the capability %s digest is not a sha256' % key)
    _require(isinstance(size, int) and not isinstance(size, bool) and size >= 0,
             'the capability %s size is not a non-negative integer' % key)
    return {'path': Path(path), 'sha256': digest, 'bytes': size}


def consume(path, *, root, source_commit, source_tree, platform, now=None,
            max_artifact_bytes=1 << 30):
    """Claim, validate and single-consume a capability. Returns its record.

    Consumption is an atomic rename: only one reader can win it, so a second
    packaging attempt in the same environment finds nothing.
    """
    path = Path(path)
    _private_regular_file(path)
    _private_directory(path.parent)
    root = Path(root).resolve()
    _require(not path.resolve().is_relative_to(root),
             'the capability must live outside the checkout: ' + str(path))

    claimed = path.with_name(path.name + '.claimed-%d-%s'
                             % (os.getpid(), secrets.token_hex(8)))
    try:
        os.rename(path, claimed)
    except OSError as error:
        raise AuthorityError('the capability could not be claimed; it may already have '
                             'been consumed: %s (%s)' % (path, error))
    try:
        _private_regular_file(claimed)
        with open(claimed, 'rb') as handle:
            raw = handle.read(MAX_CAPABILITY_BYTES + 1)
        _require(len(raw) <= MAX_CAPABILITY_BYTES,
                 'the capability exceeds its byte bound after claiming')
        try:
            record = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as error:
            raise AuthorityError('the capability is not readable JSON: %s' % error)
        _require(isinstance(record, dict), 'the capability is not a JSON object')
        _require(record.get('schema') == SCHEMA,
                 'the capability schema is not %s' % SCHEMA)

        token = _typed(record, 'token', str, 'the capability token is not a string')
        _require(len(token) == TOKEN_HEX and all(c in '0123456789abcdef' for c in token),
                 'the capability token is not %d lowercase hex characters' % TOKEN_HEX)
        build_id = _typed(record, 'build_id', str, 'the capability build id is not a string')
        _require(8 <= len(build_id) <= 64 and all(c in '0123456789abcdef' for c in build_id),
                 'the capability build id is not 8..64 lowercase hex characters')

        moment = time.time() if now is None else now
        issued_at = _typed(record, 'issued_at', (int, float),
                           'the capability issue time is not a number')
        expires_at = _typed(record, 'expires_at', (int, float),
                            'the capability expiry is not a number')
        _require(all(_finite_number(value) for value in (moment, issued_at, expires_at)),
                 'the capability validation, issue and expiry times must be finite numbers')
        _require(expires_at > issued_at, 'the capability expiry precedes its issue time')
        _require(moment <= expires_at, 'the capability has expired')
        _require(moment >= issued_at - 60,
                 'the capability was issued in the future; refusing a skewed record')

        pid = _typed(record, 'issuer_pid', int, 'the capability issuer pid is not an integer')
        ticks = _typed(record, 'issuer_start_ticks', int,
                       'the capability issuer start time is not an integer')
        live = process_start_ticks(pid)
        _require(live is not None,
                 'the issuing invocation is no longer running; this capability cannot '
                 'authorize a package build')
        _require(live == ticks,
                 'the issuing pid was recycled by a different process; refusing')

        _require(record.get('root') == str(root),
                 'the capability was issued for a different checkout')
        _require(record.get('source_commit') == source_commit,
                 'the capability names source commit %r, not this build %r'
                 % (record.get('source_commit'), source_commit))
        _require(record.get('source_tree') == source_tree,
                 'the capability names source tree %r, not this build %r'
                 % (record.get('source_tree'), source_tree))
        _require(record.get('platform') == platform,
                 'the capability names platform %r, not this build %r'
                 % (record.get('platform'), platform))

        for key in ('binary', 'provenance', 'receipt'):
            record[key] = _artifact_block(record, key)
        resources = record.get('resources')
        _require(isinstance(resources, list), 'the capability resource list is malformed')
        bound = []
        for entry in resources:
            _require(isinstance(entry, dict), 'a capability resource entry is malformed')
            name = entry.get('path')
            digest = entry.get('sha256')
            size = entry.get('bytes')
            _require(isinstance(name, str) and name and not Path(name).is_absolute()
                     and '..' not in Path(name).parts,
                     'a capability resource name is not a safe relative path')
            _require(isinstance(digest, str) and len(digest) == 64
                     and all(c in '0123456789abcdef' for c in digest),
                     'a capability resource digest is not a sha256')
            _require(isinstance(size, int) and not isinstance(size, bool) and size >= 0,
                     'a capability resource size is not a non-negative integer')
            bound.append({'path': name, 'sha256': digest, 'bytes': size})
        record['resources'] = sorted(bound, key=lambda row: row['path'])
        record['max_artifact_bytes'] = max_artifact_bytes
        return record
    finally:
        # Single consumption, on every path: a validation failure still burns it.
        try:
            os.unlink(claimed)
        except OSError:
            pass


def verify_artifacts(record):
    """Re-check the live artifacts against what this invocation verified.

    Checking the binary alone would leave stale-invocation replay of unchanged
    artifacts, changed provenance and changed notices open, so provenance, the
    resource receipt and **every** selected shipped resource are re-hashed too.
    """
    limit = record.get('max_artifact_bytes', 1 << 30)
    for key, what in (('binary', 'installed sidecar binary'),
                      ('provenance', 'provenance record'),
                      ('receipt', 'resource receipt')):
        block = record[key]
        path = block['path']
        info = _private_regular_file_artifact(path, what)
        _require(info.st_size == block['bytes'],
                 'the %s is %d bytes, not the verified %d'
                 % (what, info.st_size, block['bytes']))
        digest, _size = _sha256_file(path, limit)
        _require(digest == block['sha256'],
                 'the %s no longer matches the identity this invocation verified' % what)

    receipt_dir = record['receipt']['path'].parent
    for entry in record['resources']:
        published = receipt_dir / entry['path']
        _require(published.resolve().is_relative_to(receipt_dir.resolve()),
                 'a shipped resource escapes the resource directory: ' + entry['path'])
        info = _private_regular_file_artifact(published, 'shipped resource ' + entry['path'])
        _require(info.st_size == entry['bytes'],
                 'the shipped resource %s is %d bytes, not the verified %d'
                 % (entry['path'], info.st_size, entry['bytes']))
        digest, _size = _sha256_file(published, limit)
        _require(digest == entry['sha256'],
                 'the shipped resource %s no longer matches the identity this '
                 'invocation verified' % entry['path'])

    # The public receipt is a cross-check, never the authority: it must agree
    # with the capability rather than stand in for it.
    try:
        published_receipt = json.loads(record['receipt']['path'].read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise AuthorityError('the resource receipt is unreadable: %s' % error)
    _require(isinstance(published_receipt, dict), 'the resource receipt is malformed')
    _require(published_receipt.get('build_id') == record['build_id'],
             'the resource receipt names build %r, not this build %r'
             % (published_receipt.get('build_id'), record['build_id']))
    _require(published_receipt.get('sidecar_sha256') == record['binary']['sha256'],
             'the resource receipt describes a different sidecar than the one installed')
    _require(published_receipt.get('sidecar_bytes') == record['binary']['bytes'],
             'the resource receipt describes a different sidecar size than the one installed')
    _require(published_receipt.get('source_commit') == record['source_commit'],
             'the resource receipt names a different source commit')
    receipt_names = sorted(row.get('path') for row in published_receipt.get('resources', [])
                           if isinstance(row, dict))
    _require(receipt_names == [row['path'] for row in record['resources']],
             'the resource receipt lists a different resource set than this invocation '
             'verified')
    return True


def _private_regular_file_artifact(path, what):
    try:
        info = os.lstat(path)
    except OSError as error:
        raise AuthorityError('the %s is missing: %s (%s)' % (what, path, error))
    _require(not stat_module.S_ISLNK(info.st_mode),
             'the %s is a symbolic link' % what)
    _require(stat_module.S_ISREG(info.st_mode),
             'the %s is not a regular file' % what)
    _require(info.st_uid == os.getuid(), 'the %s is not owned by this user' % what)
    return info
