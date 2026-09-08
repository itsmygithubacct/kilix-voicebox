#!/usr/bin/env python3
"""Strictly validate a candidate-v3 sidecar provenance record.

This is a consumer. It never builds, stages, downloads or executes anything: it
reads an actual binary, an actual provenance record, the checked-in input
manifest and the actual product lock, and either refuses or reports exactly
which facts it checked against real bytes and which remain producer-bound
claims that only the separate real-build and archive review can settle.

The identity of the build being consumed is supplied by the caller, from the
collector's handoff. It is never inferred from a provenance file that happens
to be lying next to a binary: that is precisely how a stale pair gets shipped.

Exit status: 0 valid, 2 refused, 3 missing authority (see BRIDGE below).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


SCHEMA = 'kilix.voicebox.sidecar-provenance/candidate-v3'
STAGE_SCHEMA = 'kilix.voicebox.sidecar-stage/v2'
HANDOFF_SCHEMA = 'kilix.voicebox.sidecar-output/candidate-v1'
MANIFEST_SCHEMA = 'kilix.voicebox.sidecar-inputs/v2'
MANIFEST_SHA256 = 'fa26ea1c27217be6f9eb4509bd84e3c0b8f4c02a95913d5cd2cadd0c352b59d2'
VOICE_COMMIT = '228ac7ae5b1ceb6b8065f3bc1342f90bbc740345'
TRANSCRIBE_COMMIT = '0c61eb4edcdc75e1aca993ef7c8673b3b9aed6a9'
WHEEL_FILENAME = 'kilix_transcribe-0.1.0-py3-none-any.whl'
WHEEL_BYTES = 46454
WHEEL_SHA256 = '5d6ffab4c5237a45107eff9a23a177ae23759018e3cea60c19f7509bc592f2fb'
PROVIDER_ENDPOINTS = ['kilix-qwen-tts.sock', 'kilix-transcribe.sock']
PYTHON_VERSION = '3.12.8'
PYTHON_EXECUTABLE = 'python/bin/python3.12'
PYINSTALLER_VERSION = '6.15.0'
# Exact Linux x86_64 CPython 3.12.8 runtime + build selection. This table is
# valid only for these raw lock bytes, not for arbitrary future uv lockfiles.
# Its complete marker/extra traversal is reproduced in test_sidecar_lock_selection.
UV_LOCK_SHA256 = 'c32006e700bde9801f6e427d2c1370f6ff3a1bb6b7259e2468c414345de89aa6'
LOCKED_DISTRIBUTIONS = (
    ('alembic', '1.19.1'),
    ('altgraph', '0.17.5'),
    ('annotated-doc', '0.0.5'),
    ('annotated-types', '0.8.0'),
    ('anyio', '4.14.2'),
    ('cffi', '2.1.1'),
    ('click', '8.5.0'),
    ('fastapi', '0.141.1'),
    ('greenlet', '3.5.5'),
    ('h11', '0.16.0'),
    ('httptools', '0.8.0'),
    ('idna', '3.19'),
    ('mako', '1.4.1'),
    ('markupsafe', '3.0.3'),
    ('numpy', '2.5.2'),
    ('packaging', '26.3'),
    ('pillow', '12.3.0'),
    ('pycparser', '3.0'),
    ('pydantic', '2.13.5'),
    ('pydantic-core', '2.46.5'),
    ('pyinstaller', '6.15.0'),
    ('pyinstaller-hooks-contrib', '2026.7'),
    ('python-dotenv', '1.2.3'),
    ('python-multipart', '0.0.32'),
    ('pyyaml', '6.0.3'),
    ('setuptools', '84.0.0'),
    ('soundfile', '0.14.0'),
    ('sqlalchemy', '2.0.52'),
    ('starlette', '1.6.0'),
    ('typing-extensions', '4.16.0'),
    ('typing-inspection', '0.4.4'),
    ('uvicorn', '0.52.4'),
    ('uvloop', '0.22.1'),
    ('watchfiles', '1.2.0'),
    ('websockets', '17.1'),
)

PLAINTEXT_PATHS = ('VERSION', 'licenses/kilix-transcribe/LICENSE',
                   'licenses/kilix-voice/LICENSE', 'voicelib/microphone_supervisor.py')
SNAPSHOT_PREFIXES = ('source/', 'projection/', 'python/', 'env/', 'host/')
SELECTED_MODULE_COUNT = 16
COMPLETION_FILE_COUNT = 20

MAX_PROVENANCE_BYTES = 16 * 1024 ** 2
MAX_DEPTH = 32
MAX_ARRAY = 40000
MAX_PATH_BYTES = 1024
MAX_SNAPSHOT_FILE = 128 * 1024 ** 2
MAX_SNAPSHOT_TOTAL = 512 * 1024 ** 2
MAX_ENTRY_BYTES = 128 * 1024 ** 2
MAX_OPTIONS = 128
MAX_OPTION_BYTES = 1024
MAX_INTEGER = 2 ** 53 - 1

HEX = re.compile(r'\A[0-9a-f]+\Z')
DISTRIBUTION_NAME = re.compile(r'\A[a-z0-9]+(-[a-z0-9]+)*\Z')
MODE = ('0600', '0700')
INPUT_MODE = ('0600', '0644')

BRIDGE = (
    'The collector at scripts/build-server.sh removes its staging directory on '
    'exit, so its kilix.voicebox.sidecar-output/candidate-v1 handoff -- and the '
    'build_id in it -- does not survive to Tauri build time. Until the owned '
    'collector publishes that handoff beside the installed artifacts (root to '
    'integrate; the consumer needs only a path to the handoff file, or the '
    'build_id, source_commit and source_tree passed through), this consumer '
    'refuses rather than adopting whatever provenance sits next to the binary.'
)


class Refused(Exception):
    """The record, the binary or the caller's authority did not validate."""


class MissingAuthority(Exception):
    """No explicit current-invocation identity was supplied."""


def require(condition, message):
    if not condition:
        raise Refused(message)


# ---------------------------------------------------------------- strict JSON


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused('duplicate object key: ' + key[:120])
        result[key] = value
    return result


def _reject_float(value):
    raise Refused('a floating point or non-finite number is not accepted')


def parse_strict(raw, *, limit=MAX_PROVENANCE_BYTES, label='provenance'):
    """Parse UTF-8 JSON with no duplicate keys, floats, NaN or trailing data."""
    require(len(raw) <= limit, label + ' exceeds its byte bound')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as error:
        raise Refused(label + ' is not valid UTF-8') from error
    decoder = json.JSONDecoder(object_pairs_hook=_no_duplicates,
                               parse_float=_reject_float, parse_constant=_reject_float)
    try:
        value, index = decoder.raw_decode(text)
    except json.JSONDecodeError as error:
        raise Refused(label + ' is not valid JSON: ' + error.msg) from error
    require(text[index:].strip() == '', label + ' carries trailing data')
    _check_depth(value, 1, label)
    return value


def _check_depth(value, depth, label):
    require(depth <= MAX_DEPTH, label + ' nesting exceeds its depth bound')
    if isinstance(value, dict):
        for item in value.values():
            _check_depth(item, depth + 1, label)
    elif isinstance(value, list):
        for item in value:
            _check_depth(item, depth + 1, label)


# ------------------------------------------------------------- typed checkers


def obj(value, keys, where):
    require(isinstance(value, dict), where + ' is not an object')
    found = set(value)
    expected = set(keys)
    require(not expected - found, where + ' is missing keys: '
            + ','.join(sorted(expected - found)))
    require(not found - expected, where + ' has unknown keys: '
            + ','.join(sorted(found - expected)))
    return value


def text(value, where, *, maximum=MAX_PATH_BYTES):
    require(isinstance(value, str), where + ' is not a string')
    require(len(value.encode('utf-8')) <= maximum, where + ' exceeds its byte bound')
    return value


def digest(value, where):
    text(value, where, maximum=64)
    require(len(value) == 64 and HEX.fullmatch(value), where + ' is not a lowercase sha256')
    return value


def hexadecimal(value, where, low, high):
    text(value, where, maximum=high)
    require(low <= len(value) <= high and HEX.fullmatch(value),
            where + ' is not %d-%d lowercase hex characters' % (low, high))
    return value


def integer(value, where, *, low=0, high=MAX_INTEGER):
    require(isinstance(value, int) and not isinstance(value, bool),
            where + ' is not a strict integer')
    require(low <= value <= high, where + ' is out of range')
    return value


def relative_path(value, where):
    text(value, where)
    require(value, where + ' is empty')
    require('\\' not in value and '\0' not in value, where + ' has an unsupported character')
    require(not value.startswith('/'), where + ' is not relative')
    parts = value.split('/')
    require(all(part and part not in ('.', '..') for part in parts),
            where + ' has an empty or traversing component')
    return value


def array(value, where, *, maximum=MAX_ARRAY):
    require(isinstance(value, list), where + ' is not an array')
    require(len(value) <= maximum, where + ' exceeds its member bound')
    return value


def sorted_unique(rows, key, where):
    values = [row[key] for row in rows]
    require(len(set(values)) == len(values), where + ' has duplicate ' + key + ' values')
    require(values == sorted(values), where + ' is not sorted by ' + key)
    return values


def canonical_outer(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
            + '\n').encode('utf-8')


def canonical_completion(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode('utf-8')


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def read_regular(path, where, *, limit):
    path = Path(path)
    require(not path.is_symlink(), where + ' is a symbolic link')
    for parent in path.parents:
        require(not parent.is_symlink(), where + ' has a symbolic link ancestor')
    require(path.is_file(), where + ' is not a regular file')
    size = path.stat().st_size
    require(size <= limit, where + ' exceeds its byte bound')
    return path.read_bytes()


# --------------------------------------------------------------- the manifest


def manifest_rows(manifest_bytes):
    """The selected input population, taken from the checked-in manifest only."""
    require(sha256_bytes(manifest_bytes) == MANIFEST_SHA256,
            'the checked-in input manifest is not the selected one')
    manifest = parse_strict(manifest_bytes, label='input manifest')
    obj(manifest, ('schema', 'sources', 'wheel'), 'input manifest')
    require(manifest['schema'] == MANIFEST_SCHEMA, 'the input manifest has the wrong schema')
    rows = {}
    modes = {}
    for key in ('voice', 'transcribe'):
        require(key in manifest['sources'], 'input manifest has no ' + key + ' source')
        for member in manifest['sources'][key]['members']:
            rows[member['output']] = {'bytes': member['bytes'], 'sha256': member['sha256'],
                                      'mode': member['output_mode'], 'role': member['role']}
            modes[key + '/' + member['path']] = member
    require(len(rows) == COMPLETION_FILE_COUNT,
            'the selected input population is not %d rows' % COMPLETION_FILE_COUNT)
    return manifest, rows, modes


def module_name(output_path):
    """python/<pkg>/<mod>.py -> pkg.mod, and .../__init__.py -> pkg.

    The contract names the projection by module rather than by path and does not
    spell out this mapping; it is derived here from the manifest rather than
    hardcoded, and is reported as an interpretation for root.
    """
    relative = output_path[len('python/'):]
    require(relative.endswith('.py'), 'a projection module is not a Python source path')
    relative = relative[:-len('.py')]
    if relative.endswith('/__init__'):
        relative = relative[:-len('/__init__')]
    return relative.replace('/', '.')


# ------------------------------------------------------------------ validation


def validate_completion(completion, rows, modes):
    obj(completion, ('schema', 'input_manifest_sha256', 'sources', 'wheel',
                     'observed_source_modes', 'observed_wheel_mode', 'files'),
        'inputs.completion')
    require(completion['schema'] == STAGE_SCHEMA, 'inputs.completion has the wrong schema')
    require(completion['input_manifest_sha256'] == MANIFEST_SHA256,
            'inputs.completion does not bind the selected input manifest')

    sources = obj(completion['sources'], ('voice', 'transcribe'), 'inputs.completion.sources')
    for key, commit in (('voice', VOICE_COMMIT), ('transcribe', TRANSCRIBE_COMMIT)):
        entry = obj(sources[key], ('repository', 'commit', 'tree'),
                    'inputs.completion.sources.' + key)
        hexadecimal(entry['commit'], 'sources.' + key + '.commit', 40, 40)
        hexadecimal(entry['tree'], 'sources.' + key + '.tree', 40, 40)
        require(entry['commit'] == commit, 'sources.' + key + ' is not the selected commit')

    wheel = obj(completion['wheel'], ('filename', 'bytes', 'sha256'), 'inputs.completion.wheel')
    require(wheel['filename'] == WHEEL_FILENAME, 'the wheel filename is not the selected one')
    require(integer(wheel['bytes'], 'wheel.bytes', low=1) == WHEEL_BYTES,
            'the wheel length is not the selected one')
    require(digest(wheel['sha256'], 'wheel.sha256') == WHEEL_SHA256,
            'the wheel digest is not the selected one')

    observed = completion['observed_source_modes']
    obj(observed, tuple(modes), 'inputs.completion.observed_source_modes')
    for key, value in observed.items():
        require(value in INPUT_MODE, 'observed source mode ' + key + ' is unsupported')
    require(completion['observed_wheel_mode'] in INPUT_MODE,
            'the observed wheel mode is unsupported')

    files = array(completion['files'], 'inputs.completion.files', maximum=COMPLETION_FILE_COUNT)
    require(len(files) == COMPLETION_FILE_COUNT,
            'inputs.completion.files is not %d rows' % COMPLETION_FILE_COUNT)
    for row in files:
        obj(row, ('path', 'bytes', 'sha256', 'mode', 'role'), 'a completion file row')
        relative_path(row['path'], 'completion file path')
        require(row['path'] in rows, 'completion names an input outside the manifest: '
                + row['path'])
        selected = rows[row['path']]
        require(row['mode'] == '0600', 'completion file ' + row['path'] + ' has an unsupported mode')
        require(integer(row['bytes'], 'completion bytes', low=0) == selected['bytes']
                and digest(row['sha256'], 'completion sha256') == selected['sha256']
                and row['role'] == selected['role'],
                'completion row differs from the manifest: ' + row['path'])
    sorted_unique(files, 'path', 'inputs.completion.files')
    return {row['path']: row for row in files}


def validate_plaintext(plaintext, completion_rows, where):
    rows = array(plaintext, where, maximum=len(PLAINTEXT_PATHS))
    require(len(rows) == len(PLAINTEXT_PATHS), where + ' is not %d rows' % len(PLAINTEXT_PATHS))
    for row in rows:
        obj(row, ('path', 'bytes', 'sha256'), 'a ' + where + ' row')
        relative_path(row['path'], where + ' path')
    sorted_unique(rows, 'path', where)
    require([row['path'] for row in rows] == sorted(PLAINTEXT_PATHS),
            where + ' does not name the four selected plaintext files')
    for row in rows:
        source = 'python/' + row['path'] if row['path'] in (
            'VERSION', 'voicelib/microphone_supervisor.py') else row['path']
        require(source in completion_rows, where + ' names an unstaged file: ' + row['path'])
        selected = completion_rows[source]
        require(integer(row['bytes'], where + ' bytes', low=0) == selected['bytes']
                and digest(row['sha256'], where + ' sha256') == selected['sha256'],
                where + ' identity differs from the staged input: ' + row['path'])
    return rows


def validate_snapshot(snapshot):
    obj(snapshot, ('sha256', 'files'), 'build.snapshot')
    digest(snapshot['sha256'], 'build.snapshot.sha256')
    files = array(snapshot['files'], 'build.snapshot.files')
    require(files, 'build.snapshot.files is empty')
    total = 0
    population = {}
    for row in files:
        obj(row, ('path', 'bytes', 'sha256', 'mode'), 'a snapshot file row')
        path = relative_path(row['path'], 'snapshot file path')
        require(path.startswith(SNAPSHOT_PREFIXES),
                'snapshot file is outside the declared prefixes: ' + path)
        require(row['mode'] in MODE, 'snapshot file ' + path + ' has an unsupported mode')
        total += integer(row['bytes'], 'snapshot bytes', low=0, high=MAX_SNAPSHOT_FILE)
        digest(row['sha256'], 'snapshot sha256')
        population[path] = row
    sorted_unique(files, 'path', 'build.snapshot.files')
    require(total <= MAX_SNAPSHOT_TOTAL, 'the sealed build bundle exceeds its byte bound')
    return population, total


def validate_python(python, population):
    obj(python, ('implementation', 'version', 'executable', 'members'), 'build.python')
    require(python['implementation'] == 'CPython', 'build.python is not CPython')
    require(python['version'] == PYTHON_VERSION, 'build.python is not the selected version')
    require(python['executable'] == PYTHON_EXECUTABLE, 'build.python names another executable')
    members = array(python['members'], 'build.python.members')
    for member in members:
        relative_path(member, 'a python member')
        require(member.startswith('python/'), 'a python member is outside python/: ' + member)
        require(member in population, 'a python member has no snapshot row: ' + member)
    require(len(set(members)) == len(members), 'build.python.members has duplicates')
    require(members == sorted(members), 'build.python.members is not sorted')
    require(PYTHON_EXECUTABLE in members, 'build.python.members omits the interpreter')


def validate_distributions(distributions, population):
    rows = array(distributions, 'build.distributions')
    require(rows, 'build.distributions is empty')
    claimed = {}
    for row in rows:
        obj(row, ('name', 'version', 'record_sha256', 'members'), 'a distribution row')
        name = text(row['name'], 'distribution name', maximum=128)
        require(DISTRIBUTION_NAME.fullmatch(name),
                'distribution name is not a normalized lowercase name: ' + name)
        text(row['version'], 'distribution version', maximum=128)
        digest(row['record_sha256'], 'distribution record_sha256')
        members = array(row['members'], 'distribution members')
        require(members, 'distribution ' + name + ' has no members')
        require(len(set(members)) == len(members),
                'distribution ' + name + ' has duplicate members')
        for member in members:
            relative_path(member, 'a distribution member')
            require(member.startswith('env/'),
                    'distribution member is outside env/: ' + member)
            require(member in population, 'distribution member has no snapshot row: ' + member)
            require(member not in claimed,
                    'distribution member is shared with ' + str(claimed.get(member)) + ': ' + member)
            claimed[member] = name
        require(members == sorted(members), 'distribution ' + name + ' members are not sorted')
    sorted_unique(rows, 'name', 'build.distributions')
    env_population = {path for path in population if path.startswith('env/')}
    missing = env_population - set(claimed)
    require(not missing, 'env/ snapshot files are claimed by no distribution: '
            + ','.join(sorted(missing)[:5]))
    require({row['name']: row['version'] for row in rows} == dict(LOCKED_DISTRIBUTIONS),
            'build.distributions does not match the selected uv.lock name/version population')
    return {row['name']: row for row in rows}


def validate_tools(tools, population):
    rows = array(tools, 'build.tools')
    require(rows, 'build.tools is empty')
    claimed = set()
    for row in rows:
        obj(row, ('name', 'member'), 'a tool row')
        text(row['name'], 'tool name', maximum=256)
        member = relative_path(row['member'], 'tool member')
        require(member.startswith('host/'), 'tool member is outside host/: ' + member)
        require(member in population, 'tool member has no snapshot row: ' + member)
        require(member not in claimed, 'tool member is claimed twice: ' + member)
        claimed.add(member)
    sorted_unique(rows, 'name', 'build.tools')
    host_population = {path for path in population if path.startswith('host/')}
    missing = host_population - claimed
    require(not missing, 'host/ snapshot files have no tool row: '
            + ','.join(sorted(missing)[:5]))


def validate_archive(archive, completion_rows, plaintext_rows):
    obj(archive, ('selected_modules', 'plaintext', 'entries', 'options'), 'build.archive')
    modules = array(archive['selected_modules'], 'build.archive.selected_modules',
                    maximum=SELECTED_MODULE_COUNT)
    require(len(modules) == SELECTED_MODULE_COUNT,
            'the projection is not %d modules' % SELECTED_MODULE_COUNT)
    expected = {module_name(path): row for path, row in completion_rows.items()
                if row['role'] == 'module'}
    require(len(expected) == SELECTED_MODULE_COUNT,
            'the manifest does not select %d modules' % SELECTED_MODULE_COUNT)
    for row in modules:
        obj(row, ('name', 'source_sha256', 'code_sha256'), 'a selected module row')
        name = text(row['name'], 'module name', maximum=256)
        require(name in expected, 'the projection names an unselected module: ' + name)
        require(digest(row['source_sha256'], 'module source_sha256') == expected[name]['sha256'],
                'module source digest differs from the staged input: ' + name)
        digest(row['code_sha256'], 'module code_sha256')
    sorted_unique(modules, 'name', 'build.archive.selected_modules')

    archive_plaintext = validate_plaintext(archive['plaintext'], completion_rows,
                                           'build.archive.plaintext')
    require(archive_plaintext == plaintext_rows,
            'build.archive.plaintext differs from inputs.plaintext')

    entries = array(archive['entries'], 'build.archive.entries')
    require(entries, 'build.archive.entries is empty')
    for row in entries:
        obj(row, ('path', 'kind', 'bytes', 'sha256'), 'an archive entry row')
        relative_path(row['path'], 'archive entry path')
        kind = text(row['kind'], 'archive entry kind', maximum=8)
        require(len(kind) == 1 and 32 <= ord(kind) < 127,
                'archive entry kind is not one printable ASCII character')
        integer(row['bytes'], 'archive entry bytes', low=0, high=MAX_ENTRY_BYTES)
        digest(row['sha256'], 'archive entry sha256')
    sorted_unique(entries, 'path', 'build.archive.entries')

    options = array(archive['options'], 'build.archive.options', maximum=MAX_OPTIONS)
    for option in options:
        text(option, 'an archive option', maximum=MAX_OPTION_BYTES)
    require(len(set(options)) == len(options), 'build.archive.options has duplicates')
    require(options == sorted(options), 'build.archive.options is not sorted')
    return [row['name'] for row in modules]


def validate(*, provenance_bytes, binary_bytes_length, binary_sha256, build_id,
             source_commit, source_tree, manifest_bytes, uv_lock_sha256):
    """Refuse or return a report of what was checked against actual bytes."""
    record = parse_strict(provenance_bytes)
    obj(record, ('schema', 'build_id', 'source_commit', 'source_tree', 'uv_lock_sha256',
                 'sidecar_sha256', 'sidecar_bytes', 'contains_model_runtime',
                 'provider_endpoints', 'inputs', 'build'), 'provenance')
    require(record['schema'] == SCHEMA,
            'provenance schema is not ' + SCHEMA + ': ' + str(record['schema'])[:120]
            + ' (there is no downgrade to an earlier candidate)')
    require(canonical_outer(record) == provenance_bytes,
            'provenance is not its own canonical sorted compact encoding with one final LF')

    hexadecimal(record['build_id'], 'build_id', 8, 64)
    require(record['build_id'] == build_id,
            'provenance build_id is not this invocation: the record belongs to another build')
    hexadecimal(record['source_commit'], 'source_commit', 40, 40)
    hexadecimal(record['source_tree'], 'source_tree', 40, 40)
    require(record['source_commit'] == source_commit,
            'provenance source_commit is not the expected clean product commit')
    require(record['source_tree'] == source_tree,
            'provenance source_tree is not that commit tree')
    require(digest(record['uv_lock_sha256'], 'uv_lock_sha256') == uv_lock_sha256,
            'provenance does not bind the actual product uv.lock bytes')
    require(uv_lock_sha256 == UV_LOCK_SHA256,
            'the actual product uv.lock has no supported distribution selection')
    require(digest(record['sidecar_sha256'], 'sidecar_sha256') == binary_sha256,
            'provenance does not bind the actual sidecar bytes')
    require(integer(record['sidecar_bytes'], 'sidecar_bytes', low=1) == binary_bytes_length,
            'provenance does not bind the actual sidecar length')
    require(record['contains_model_runtime'] is False,
            'contains_model_runtime is not the JSON boolean false')
    require(record['provider_endpoints'] == PROVIDER_ENDPOINTS,
            'provider_endpoints is not the exact selected ordered pair')

    manifest, rows, modes = manifest_rows(manifest_bytes)
    inputs = obj(record['inputs'], ('completion_sha256', 'completion', 'plaintext'), 'inputs')
    completion_rows = validate_completion(inputs['completion'], rows, modes)
    for key, source in manifest['sources'].items():
        require(inputs['completion']['sources'][key]
                == {field: source[field] for field in ('repository', 'commit', 'tree')},
                'completion source identity differs from the selected manifest: ' + key)
    require(digest(inputs['completion_sha256'], 'inputs.completion_sha256')
            == sha256_bytes(canonical_completion(inputs['completion'])),
            'inputs.completion_sha256 does not hash the C19 canonical completion encoding')
    plaintext_rows = validate_plaintext(inputs['plaintext'], completion_rows, 'inputs.plaintext')

    build = obj(record['build'], ('python', 'pyinstaller', 'distributions', 'tools',
                                  'snapshot', 'archive'), 'build')
    population, total = validate_snapshot(build['snapshot'])
    validate_python(build['python'], population)
    pyinstaller = obj(build['pyinstaller'], ('distribution', 'version'), 'build.pyinstaller')
    require(pyinstaller['distribution'] == 'pyinstaller',
            'build.pyinstaller names another distribution')
    require(pyinstaller['version'] == PYINSTALLER_VERSION,
            'build.pyinstaller is not the selected version')
    distributions = validate_distributions(build['distributions'], population)
    require('pyinstaller' in distributions,
            'the build tool has no distribution row binding its actual code population')
    require(distributions['pyinstaller']['version'] == PYINSTALLER_VERSION,
            'the pyinstaller distribution row is not the selected version')
    validate_tools(build['tools'], population)
    module_names = validate_archive(build['archive'], completion_rows, plaintext_rows)

    return {
        'schema': SCHEMA,
        'build_id': record['build_id'],
        'source_commit': record['source_commit'],
        'sidecar_bytes': record['sidecar_bytes'],
        'sidecar_sha256': record['sidecar_sha256'],
        'plaintext': plaintext_rows,
        'selected_modules': module_names,
        'snapshot_files': len(population),
        'snapshot_bytes': total,
        'distributions': len(distributions),
        'verified_from_actual_files': [
            'the sidecar binary length and sha256 read from the installed file',
            'the provenance bytes, their canonical encoding and their sha256',
            'the product uv.lock sha256 read from the checked-in file',
            'the exact Linux x86_64 CPython3.12 runtime plus build distribution '
            'names/versions selected by those fixed uv.lock bytes, including standard extras',
            'the input manifest sha256 and its complete selected 20-row population',
            'the collector handoff build_id, binary and provenance identities',
            'the expected clean source commit and tree supplied by the caller',
        ],
        'producer_bound_claims': [
            'snapshot.sha256 and every snapshot file row: no sealed bundle is read here',
            'distribution record_sha256 and member populations, and the tool/host rows',
            'archive entries, options and every module code_sha256: no archive is opened here',
            'that the compiled code matches the verified source for each projection module',
            'python member and interpreter identities inside the sealed bundle',
        ],
        'interpretations': [
            'projection module names are derived from the manifest output paths '
            '(python/<pkg>/<mod>.py -> pkg.mod, __init__.py -> pkg); the contract '
            'names the modules but does not spell out this mapping',
        ],
    }


# ------------------------------------------------------------------------ CLI


def load_handoff(path):
    fields = {}
    raw = read_regular(path, 'the collector handoff', limit=64 * 1024)
    for line in raw.decode('ascii').splitlines():
        key, separator, value = line.partition('=')
        require(separator and key and key not in fields,
                'the collector handoff is malformed')
        fields[key] = value
    obj(fields, ('schema', 'build_id', 'binary', 'binary_sha256', 'binary_bytes',
                 'provenance', 'provenance_sha256'), 'the collector handoff')
    require(fields['schema'] == HANDOFF_SCHEMA, 'the collector handoff has the wrong schema')
    return fields


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--provenance', required=True, type=Path)
    parser.add_argument('--input-manifest', required=True, type=Path)
    parser.add_argument('--uv-lock', required=True, type=Path)
    parser.add_argument('--handoff', type=Path,
                        help='the collector handoff naming this invocation')
    parser.add_argument('--build-id', help='this invocation identity, if no handoff is published')
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--source-tree', required=True)
    parser.add_argument('--report', type=Path)
    options = parser.parse_args(argv)
    try:
        if options.handoff is not None:
            handoff = load_handoff(options.handoff)
            build_id = handoff['build_id']
            require(options.build_id in (None, build_id),
                    'the supplied build id is not the handoff build id')
        elif options.build_id:
            handoff = None
            build_id = options.build_id
        else:
            raise MissingAuthority(
                'no current-invocation identity was supplied: pass --handoff or --build-id')
        binary = read_regular(options.binary, 'the sidecar binary', limit=1024 ** 3)
        provenance = read_regular(options.provenance, 'the provenance record',
                                  limit=MAX_PROVENANCE_BYTES)
        manifest = read_regular(options.input_manifest, 'the input manifest', limit=8 * 1024 ** 2)
        lock = read_regular(options.uv_lock, 'the product lock', limit=64 * 1024 ** 2)
        if handoff is not None:
            require(handoff['binary_sha256'] == sha256_bytes(binary)
                    and handoff['binary_bytes'] == str(len(binary)),
                    'the installed binary is not the one the handoff named')
            require(handoff['provenance_sha256'] == sha256_bytes(provenance),
                    'the installed provenance is not the one the handoff named')
        report = validate(
            provenance_bytes=provenance, binary_bytes_length=len(binary),
            binary_sha256=sha256_bytes(binary), build_id=build_id,
            source_commit=options.source_commit, source_tree=options.source_tree,
            manifest_bytes=manifest, uv_lock_sha256=sha256_bytes(lock))
    except MissingAuthority as error:
        print('voicebox: sidecar gate has no build authority: ' + str(error), file=sys.stderr)
        print('voicebox: bridge required -- ' + BRIDGE, file=sys.stderr)
        return 3
    except Refused as error:
        print('voicebox: sidecar provenance refused: ' + str(error), file=sys.stderr)
        return 2
    report['handoff_verified'] = handoff is not None
    if options.report is not None:
        options.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n',
                                  encoding='utf-8')
    print(json.dumps({'status': 'verified', 'build_id': report['build_id'],
                      'sidecar_bytes': report['sidecar_bytes'],
                      'handoff_verified': report['handoff_verified']}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
