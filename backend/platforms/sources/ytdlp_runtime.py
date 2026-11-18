import json
import os
import shutil
import subprocess
from importlib.metadata import version

_PROBE_TIMEOUT = 10


def javascript_runtime_options():
    """yt-dlp options enabling a Node.js JS runtime for signature challenges.

    Pure-Python installs have no Node: return {} so yt-dlp falls back to its
    built-in behavior instead of hard-failing the extraction.
    """
    node_path = shutil.which('node')
    if not node_path:
        return {}
    return {'js_runtimes': {'node': {'path': node_path}}, 'remote_components': []}


def probe_javascript_runtime():
    """Diagnostic probe for the optional Node.js + yt-dlp-ejs challenge solver."""
    node_path = shutil.which('node')
    if not node_path:
        raise RuntimeError('yt-dlp JavaScript runtime unavailable')
    env = dict(os.environ)
    env.pop('NODE_OPTIONS', None)
    version_result = subprocess.run(
        [node_path, '--version'],
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT,
        env=env,
    )
    if version_result.returncode != 0:
        raise RuntimeError('yt-dlp JavaScript runtime version probe failed')
    node_version = version_result.stdout.strip()

    from yt_dlp_ejs.yt import solver
    ejs_version = version('yt-dlp-ejs')
    request = {
        'type': 'preprocessed',
        'preprocessed_player': '_result.sig = s => s.split("").reverse().join(""); _result.n = s => s.slice(1);',
        'requests': [
            {'type': 'sig', 'challenges': ['abcdef']},
            {'type': 'n', 'challenges': ['012345']},
        ],
    }
    script = (
        solver.lib()
        + '\nconst { meriyah, astring } = lib;\n'
        + solver.core()
        + '\nconsole.log(JSON.stringify(jsc(' + json.dumps(request) + ')));'
    )
    result = subprocess.run(
        [node_path, '-'],
        input=script,
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError('yt-dlp JavaScript runtime probe failed')
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError('yt-dlp JavaScript runtime probe returned invalid JSON')
    responses = payload.get('responses') if payload.get('type') == 'result' else None
    if not isinstance(responses, list) or len(responses) != 2:
        raise RuntimeError('yt-dlp JavaScript runtime probe returned unexpected result')
    if responses[0].get('type') != 'result' or responses[0].get('data') != {'abcdef': 'fedcba'}:
        raise RuntimeError('yt-dlp JavaScript runtime sig challenge mismatch')
    if responses[1].get('type') != 'result' or responses[1].get('data') != {'012345': '12345'}:
        raise RuntimeError('yt-dlp JavaScript runtime n challenge mismatch')
    return {
        'nodeVersion': node_version,
        'ejsVersion': ejs_version,
        'signatureResult': responses[0]['data'],
        'nResult': responses[1]['data'],
    }


if __name__ == '__main__':
    print(json.dumps(probe_javascript_runtime()))
