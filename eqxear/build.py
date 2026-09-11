# SPDX-License-Identifier: Apache-2.0
"""Build the small native DSP plugin into an XDG cache, never system folders."""
import errno
import hashlib
import os
from pathlib import Path
import subprocess
import shutil
import tempfile

URI = 'urn:eqxear:v2:eq'
ROOT = Path(__file__).resolve().parents[1]

def build_plugin():
    source = (ROOT/'native/eq.c').read_bytes()
    # Include the build recipe and metadata as well as source in the identity.
    digest = hashlib.sha256(source + Path(__file__).read_bytes()).hexdigest()
    cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home()/'.cache'))/'eqxear-v2/lv2'
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache/digest
    if destination.is_dir():
        return destination
    # Publish the complete immutable bundle in one rename. Concurrent builders
    # use independent workspaces; the winner's identical bundle can be reused.
    temporary = Path(tempfile.mkdtemp(prefix='.build-', dir=cache))
    try:
        bundle = temporary/'eqxear.lv2'
        bundle.mkdir()
        snapshot = temporary/'eq.c'
        snapshot.write_bytes(source)
        result = subprocess.run(['cc', '-std=gnu11', '-O2', '-fPIC', '-shared', '-Wall', '-Wextra', str(snapshot), '-lm', '-o', str(bundle/'eq.so')], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Could not build the native EQ plugin. A C compiler and LV2 headers are needed.\n'+result.stderr)
        snapshot.unlink()
        write_metadata(bundle)
        try:
            os.rename(temporary, destination)
        except OSError as error:
            if error.errno not in (errno.EEXIST, errno.ENOTEMPTY) or not destination.is_dir():
                raise
        return destination
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def write_metadata(cache):
    (cache/'manifest.ttl').write_text(f'@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n<{URI}> a lv2:Plugin; lv2:binary <eq.so>; rdfs:seeAlso <eq.ttl> .\n')
    ports = []
    for i, symbol in enumerate(('in_l','in_r','out_l','out_r')):
        ports.append(f'[ a lv2:{"InputPort" if i<2 else "OutputPort"}, lv2:AudioPort; lv2:index {i}; lv2:symbol "{symbol}"; lv2:name "{symbol}" ]')
    controls = [('preamp',-96,18,0),('mute',0,1,0),('bypass',0,1,0)]
    for i in range(8):
        controls += [(f'b{i}_type',0,3,0),(f'b{i}_freq',20,20000,1000),(f'b{i}_gain',-12,12,0),(f'b{i}_q',.3,10,1)]
    for i,(name,low,high,default) in enumerate(controls,4):
        ports.append(f'[ a lv2:InputPort, lv2:ControlPort; lv2:index {i}; lv2:symbol "{name}"; lv2:name "{name}"; lv2:minimum {low}; lv2:maximum {high}; lv2:default {default} ]')
    (cache/'eq.ttl').write_text('@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n@prefix doap: <http://usefulinc.com/ns/doap#> .\n'+f'<{URI}> a lv2:Plugin; doap:name "EQxEar"; lv2:optionalFeature lv2:hardRTCapable; lv2:port '+',\n'.join(ports)+' .\n')
