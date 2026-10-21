#!/usr/bin/env python3
"""Build the VR coordinate monitor using this workspace's local Android tools."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import urllib.parse

project = Path(__file__).resolve().parents[1]
workspace = project.parent
tools = workspace / '.build-tools'
env = os.environ.copy()
env['JAVA_HOME'] = str(tools / 'jdk17')
env['ANDROID_HOME'] = str(tools / 'android-sdk')
gradle = tools / 'gradle-8.5/bin/gradle'
if not gradle.exists() or not (Path(env['JAVA_HOME']) / 'bin/java').exists():
    raise SystemExit('Local build tools missing under ' + str(tools))
command = [str(gradle), '-p', str(project / 'app'), 'assembleDebug',
           '--no-daemon', '--max-workers=4',
           '-PmetaSdkDir=' + str(tools / 'meta-openxr-sdk')]
proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
if proxy:
    address = urllib.parse.urlsplit(proxy)
    if address.scheme == 'http' and address.hostname and address.port:
        for protocol in ('http', 'https'):
            command += ['-D' + protocol + '.proxyHost=' + address.hostname,
                        '-D' + protocol + '.proxyPort=' + str(address.port)]
subprocess.run(command, env=env, check=True)
output = workspace / 'artifacts/quest-coordinate-monitor-debug.apk'
output.parent.mkdir(exist_ok=True)
apk_dir = project / 'app/build/outputs/apk/debug'
metadata = json.loads((apk_dir / 'output-metadata.json').read_text())
shutil.copy2(apk_dir / metadata['elements'][0]['outputFile'], output)
shutil.copy2(output, project / 'oculus_reader/APK/teleop-debug.apk')
print('APK:', output)
